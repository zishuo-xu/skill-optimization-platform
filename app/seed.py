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

from . import config, db, persistence, services, single_round, skill_package
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
        "tools": "tool_schema.json",
        "dataset": "dataset.jsonl",
        "dataset_name": "示例数据集（任务管理）",
    },
    {
        "name": DEMO_IMAGE_PROJECT_NAME,
        "description": "使用渐进式图片编辑 Skill 包演示分发、工具参数与单轮优化闭环。",
        "zip": build_image_package_zip,
        "version_message": "示例图片编辑 Skill 包（主 Skill + 4 个子技能）",
        "environment": "image_environment.json",
        "prompt": "image_system_prompt.md",
        "tools": "image_tool_schema.json",
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
    template["tool_schema"] = _load_json(config.SAMPLES_DIR / spec["tools"])
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
    results = [_seed_project(spec, with_optimize) for spec in DEMO_SPECS]
    print()
    for item in results:
        project = item["project"]
        print(f"项目 ID: {project['id']}（{project['name']}）")
        print("启动服务后打开: "
              + f"http://{config.HOST}:{config.PORT}/#/projects/{project['id']}/overview")
    return {"projects": results}


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
    args = parser.parse_args(argv)
    if args.reset:
        reset_data()
    seed(args.with_optimize)
    return 0


if __name__ == "__main__":
    sys.exit(main())
