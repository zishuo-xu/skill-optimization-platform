"""Seed the demo project from ``app/samples`` and optionally run baseline / optimize.

Usage::

    .venv/bin/python -m app.seed              # 项目 + 基线
    .venv/bin/python -m app.seed --with-optimize
    .venv/bin/python -m app.seed --reset      # 清空本地 data/ 后重建（谨慎）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import config, dataset as dataset_module, db, hashing, onboarding, persistence, services, single_round, skill_package
from .errors import ConflictError, ValidationError
from .worker import Worker

DEMO_PROJECT_NAME = "示例项目：任务管理"
DEMO_IMAGE_PROJECT_NAME = "示例项目：修图"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_package_zip() -> bytes:
    return _build_zip("skill_package")


def build_image_package_zip() -> bytes:
    return _build_zip("image_skill_package")


def _build_zip(directory: str) -> bytes:
    root = config.SAMPLES_DIR / directory
    files = {path.relative_to(root).as_posix(): path.read_bytes()
             for path in sorted(root.rglob("*")) if path.is_file()}
    return skill_package.build_zip(files)


def reset_data() -> None:
    target = config.DATA_DIR.resolve()
    repo = config.REPO_ROOT.resolve()
    if repo not in target.parents and target != repo / "data":
        raise SystemExit(f"拒绝删除非工作区 data 目录: {target}")
    if target.exists():
        shutil.rmtree(target)
    config.ensure_dirs()


def run_sync(run_id: str) -> None:
    worker = Worker(executor=single_round.execute_run)
    # The worker claims queued runs in creation order; only our run is queued here.
    executed = worker.process_once()
    if not executed:
        print(f"警告：未能领取运行 {run_id}")


DEMO_SPECS = [
    {
        "name": DEMO_PROJECT_NAME,
        "description": "使用示例任务管理 Skill 包演示单轮评测与优化闭环。",
        "zip": build_package_zip,
        "version_message": "示例 Skill 包（主 Skill + 21 个子技能）",
        "environment": "environment.json",
        "prompt": "system_prompt.md",
        "dataset": "dataset.jsonl",
        "dataset_name": "示例数据集（任务管理）",
    },
    {
        "name": DEMO_IMAGE_PROJECT_NAME,
        "description": "将用户修图语流分发到 10 类子技能，生成有序、完整的工作流参数。",
        "zip": build_image_package_zip,
        "version_message": "语流修图 Skill 包（主技能路由 + 10 类参数子技能）",
        "environment": "image_environment.json",
        "prompt": "image_system_prompt.md",
        "dataset": "image_dataset.jsonl",
        "dataset_name": "示例数据集（图片编辑）",
    },
]


def _seed_project(spec: dict, with_optimize: bool) -> dict:
    existing = [project for project in persistence.list_projects()
                if project["name"] == spec["name"]]
    if existing:
        project = existing[0]
        print(f"{spec['name']} 已存在: {project['id']}（如需重建请加 --reset）")
        return {"project": project}

    print(f"\n===== {spec['name']} =====")
    project = persistence.create_project(spec["name"], spec["description"])
    persistence.append_audit(project["id"], "project.create",
                             {"name": project["name"], "seed": True})
    version = services.create_skill_version(
        project["id"], zip_bytes=spec["zip"](), status="published",
        set_current=True, message=spec["version_message"])
    detail = services.skill_version_detail(version["id"])
    print(f"Skill 版本: {version['id']}（{len(detail.get('file_tree') or [])} 个文件）")

    template = _load_json(config.SAMPLES_DIR / spec["environment"])
    template["prompt"] = (config.SAMPLES_DIR / spec["prompt"]).read_text(
        encoding="utf-8")
    env = services.create_environment(project["id"], template, status="published",
                                      set_current=True)
    print(f"执行环境: {env['id']}（provider={(env.get('model_config') or {}).get('provider')}）")

    dataset_payload = {
        "filename": spec["dataset"],
        "content": (config.SAMPLES_DIR / spec["dataset"]).read_text(encoding="utf-8"),
        "split_config": {"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
        "seed": 42,
        "source_name": spec["dataset_name"],
    }
    dataset = services.create_dataset(project["id"], dataset_payload)
    print(f"数据集: {dataset['id']}（{dataset['case_count']} 个案例）")

    baseline = services.create_run({"project_id": project["id"], "mode": "baseline"})
    run_sync(baseline["id"])
    baseline = persistence.get_run(baseline["id"])
    print(f"基线运行: {baseline['id']} status={baseline['status']}")
    _print_metrics(baseline, "baseline")

    result = {"project": persistence.get_project(project["id"]),
              "baseline_run": baseline}
    if with_optimize:
        optimize = services.create_run({"project_id": project["id"], "mode": "optimize"})
        run_sync(optimize["id"])
        optimize = persistence.get_run(optimize["id"])
        print(f"单轮优化运行: {optimize['id']} status={optimize['status']} "
              f"decision={optimize.get('decision')}")
        _print_metrics(optimize, "candidate")
        candidate = persistence.get_candidate_for_run(optimize["id"])
        if candidate:
            print(f"候选补丁: {candidate.get('patch', {}).get('summary')}")
            print("变更文件:", candidate.get("changed_files"))
        result["optimize_run"] = optimize
    return result


def seed(with_optimize: bool) -> dict:
    db.init_db()
    onboarding.ensure_guide()
    results = [_seed_project(spec, with_optimize) for spec in DEMO_SPECS]
    print()
    for item in results:
        project = item["project"]
        print(f"项目 ID: {project['id']}（{project['name']}）")
        print("启动服务后打开: "
              + f"http://{config.HOST}:{config.PORT}/#/projects/{project['id']}")
    return {"projects": results}


def refresh_image_sample() -> dict:
    """Publish the expanded image assets without resetting user data or models.

    All three versions are validated and saved before a single guarded pointer
    update. This command does not invoke any model or change historical runs.
    """
    db.init_db()
    spec = next(item for item in DEMO_SPECS if item["name"] == DEMO_IMAGE_PROJECT_NAME)
    existing = [p for p in persistence.list_projects() if p["name"] == spec["name"]]
    if not existing:
        raise ValidationError("修图示例尚未初始化，请先运行 python -m app.seed")
    project = existing[0]
    for status in config.ACTIVE_RUN_STATUSES:
        if persistence.list_runs(project_id=project["id"], status=status, limit=1)["total"]:
            raise ConflictError("修图项目仍有活动运行，请等待结束后更新示例")

    previous = {key: project.get(key) for key in (
        "current_skill_version_id", "current_environment_id", "current_dataset_id")}
    template = _load_json(config.SAMPLES_DIR / spec["environment"])
    old_env = persistence.get_environment(previous["current_environment_id"]) \
        if previous["current_environment_id"] else template
    env_payload = dict(old_env)
    env_payload["prompt"] = (config.SAMPLES_DIR / spec["prompt"]).read_text(encoding="utf-8")
    for key, fields in (("loader_config", ("max_steps",)),
                        ("run_config", ("max_model_calls",))):
        env_payload[key] = dict(old_env.get(key) or template[key])
        for field in fields:
            env_payload[key][field] = max(int(env_payload[key].get(field) or 0),
                                          int(template[key][field]))
    env_payload["reproducibility_note"] = (
        "10 类修图示例；模型接入配置沿用既有版本。新增 method 需由真实业务 SDK 实现；"
        "demo_scripted 成绩只代表确定性模拟器。")
    model = env_payload["model_config"]
    env_payload["adapter_hash"] = hashing.hash_json({
        "provider": model.get("provider"), "model": model.get("model"),
        "request_template": model.get("request_template"),
        "stream_parser": model.get("stream_parser")})

    package = skill_package.import_package(spec["zip"]())
    if package["errors"]:
        raise ValidationError("新版修图包校验失败", details=package["errors"])
    content = (config.SAMPLES_DIR / spec["dataset"]).read_bytes()
    items, _ = dataset_module.parse_payload(content, spec["dataset"])
    split_config = {"optimize": 0.7, "validation": 0.2, "final_test": 0.1}
    report = dataset_module.validate_and_prepare(
        items, manifest=package["manifest"],
        split_config=split_config, seed=42)
    if report["errors"]:
        raise ValidationError("新版修图数据集校验失败", details=report["errors"])

    old_files = services._read_version_files(previous["current_skill_version_id"]) \
        if previous["current_skill_version_id"] else {}
    old_dataset = persistence.get_dataset(previous["current_dataset_id"]) \
        if previous["current_dataset_id"] else {}
    if (old_files == package["files"]
            and old_env.get("hash") == persistence.compute_environment_hash(env_payload)
            and old_dataset.get("hash") == hashing.hash_json(report["cases"])):
        return {"project": project, "updated": False, "case_count": len(report["cases"])}

    version = persistence.create_skill_version(
        project["id"], package["files"], package["manifest"],
        parent_id=previous["current_skill_version_id"], status="published",
        message=spec["version_message"], set_current=False)
    env = services.create_environment(
        project["id"], env_payload, parent_id=previous["current_environment_id"],
        status="published", set_current=False)
    dataset = persistence.create_dataset(
        project["id"], report["cases"], split_config,
        source_name=spec["dataset_name"], parent_id=previous["current_dataset_id"],
        import_report={k: report.get(k) for k in ("errors", "warnings", "stats")},
        status="published", set_current=False)
    with db.transaction() as conn:
        changed = conn.execute(
            "UPDATE projects SET current_skill_version_id=?, current_environment_id=?,"
            " current_dataset_id=?, description=?, updated_at=? WHERE id=?"
            " AND current_skill_version_id IS ? AND current_environment_id IS ?"
            " AND current_dataset_id IS ?",
            (version["id"], env["id"], dataset["id"], spec["description"],
             persistence.now_iso(), project["id"],
             previous["current_skill_version_id"], previous["current_environment_id"],
             previous["current_dataset_id"])).rowcount
        if changed != 1:
            raise ConflictError("更新期间项目配置已变化，已保留新版本但未切换当前指针")
    persistence.append_audit(project["id"], "sample.image.refresh", {
        "previous": previous, "skill_version_id": version["id"],
        "environment_id": env["id"], "dataset_id": dataset["id"]})
    return {"project": persistence.get_project(project["id"]), "updated": True,
            "case_count": len(report["cases"]), "skill_version": version,
            "environment": env, "dataset": dataset}


def _print_metrics(run: dict, key: str) -> None:
    metrics = (run.get("metrics") or {}).get(key) or {}
    for name, label in (("routing", "分发准确率 R"), ("tool_args", "工具入参准确率 A"),
                        ("joint", "联合通过率 J")):
        block = metrics.get(name) or {}
        value = block.get("value")
        print(f"  {label}: " + ("未评估" if value is None
                                else f"{value:.2%}（{block.get('passed')}/{block.get('evaluated')}）"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化示例项目并运行基线")
    parser.add_argument("--reset", action="store_true", help="清空本地 data/ 后重建")
    parser.add_argument("--with-optimize", action="store_true",
                        help="创建并执行一次单轮优化")
    parser.add_argument("--refresh-image", action="store_true",
                        help="为已有修图示例发布新版 Skill 包和数据集，保留模型配置和历史；不调用模型")
    parser.add_argument("--ensure-guide", action="store_true", help="仅准备任务协作引导，不调用模型")
    args = parser.parse_args(argv)
    if args.ensure_guide:
        if args.reset or args.with_optimize or args.refresh_image:
            parser.error("--ensure-guide 不与其他选项混用")
        db.init_db()
        project = onboarding.ensure_guide()
        print(f"任务协作引导已准备: {project['id']}（未发起模型调用）")
        return 0
    if args.refresh_image:
        if args.reset or args.with_optimize:
            parser.error("--refresh-image 不与 --reset/--with-optimize 混用")
        refreshed = refresh_image_sample()
        project = refreshed["project"]
        print(f"修图示例{'已更新' if refreshed['updated'] else '已是最新'}: {project['id']}"
              f"（10 个子技能 / {refreshed['case_count']} 个案例；未发起模型调用）")
        return 0
    if args.reset:
        reset_data()
    seed(args.with_optimize)
    return 0


if __name__ == "__main__":
    sys.exit(main())
