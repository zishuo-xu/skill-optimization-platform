"""Application services shared by the HTTP API, the worker and the demo seed."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import config, evaluation, hashing, ids, persistence, skill_package
from .errors import ConflictError, NotFoundError, ValidationError


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return persistence.now_iso()


def _get(payload: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = payload.get(key)
    return default if value is None else value


def default_environment_template() -> dict[str, Any]:
    samples = config.SAMPLES_DIR
    prompt = (samples / "system_prompt.md").read_text(encoding="utf-8")
    template = json.loads((samples / "environment.json").read_text(encoding="utf-8"))
    template["prompt"] = prompt
    return template


def normalize_model_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("model_config 必须是对象", field_path="model_config")
    provider = raw.get("provider")
    if provider in ("flow", "python_flow", "python"):
        raise ValidationError("Python 流程接入已移除，请改用标准 API 或 WebSocket 网关",
                              field_path="model_config.provider")
    if provider not in ("demo_scripted", "openai_http", "enterprise_ws", "heuristic"):
        raise ValidationError(
            f"不支持的模型提供方: {provider!r}", field_path="model_config.provider")
    normalized = dict(raw)
    if provider == "enterprise_ws":
        if "heartbeat_interval_s" not in normalized and "heartbeat_s" in normalized:
            normalized["heartbeat_interval_s"] = normalized["heartbeat_s"]
        normalized.pop("heartbeat_s", None)
    return normalized


# --------------------------------------------------------------------------- #
# skill versions
# --------------------------------------------------------------------------- #
def create_skill_version(project_id: str, *, zip_bytes: bytes | None = None,
                         files_payload: list[Mapping[str, Any]] | None = None,
                         status: str = "published", message: str = "",
                         parent_id: str | None = None, set_current: bool | None = None,
                         manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    project = persistence.get_project(project_id)
    if status not in ("draft", "published", "candidate"):
        raise ValidationError("status 只能是 draft/published/candidate", field_path="status")
    if zip_bytes is not None:
        result = skill_package.import_package(zip_bytes)
    elif files_payload is not None:
        result = skill_package.import_json_files(files_payload)
    else:
        raise ValidationError("必须提供 ZIP 或 files")
    if result["errors"]:
        raise ValidationError("Skill 包校验失败", code="skill_package_invalid",
                              details=result["errors"])
    files = result["files"]
    package_manifest = dict(manifest or result["manifest"] or {})
    if not files:
        raise ValidationError("Skill 包为空", code="skill_package_invalid")
    if set_current is None:
        set_current = status == "published"
    version = persistence.create_skill_version(
        project_id, files, package_manifest, parent_id=parent_id,
        status=status, message=message, set_current=False)
    if status == "published" and set_current:
        persistence.set_current_skill_version(project_id, version["id"])
        persistence.append_audit(project_id, "skill_version.publish",
                                 {"skill_version_id": version["id"],
                                  "parent_id": parent_id})
    version["warnings"] = result["warnings"]
    if project.get("current_skill_version_id") is None and status == "draft":
        pass
    return version


def save_draft_file(version_id: str, relpath: str, content: str) -> dict[str, Any]:
    version = persistence.get_skill_version(version_id)
    if version["status"] != "draft":
        raise ConflictError("只有草稿版本可以编辑", code="version_read_only",
                            details={"status": version["status"]})
    if not skill_package.is_editable(relpath):
        raise ValidationError("只允许编辑 Markdown 文件", field_path="path")
    files = _read_version_files(version_id)
    if relpath not in files:
        raise NotFoundError(f"文件不存在: {relpath}", code="not_found")
    files[relpath] = content.encode("utf-8")
    version = persistence.get_skill_version(version_id)
    manifest_errors, _warnings = skill_package.validate_manifest(version["manifest"], files)
    if manifest_errors:
        raise ValidationError("编辑后 Skill 包校验失败", details=manifest_errors)
    persistence.update_skill_version_files(version_id, files, version["manifest"])
    return persistence.get_skill_version(version_id)


def publish_skill_version(version_id: str, *, set_current: bool = True) -> dict[str, Any]:
    version = persistence.get_skill_version(version_id)
    files = _read_version_files(version_id)
    errors, _warnings = skill_package.validate_manifest(version["manifest"], files)
    if errors:
        raise ValidationError("Skill 包校验失败", code="skill_package_invalid",
                              details=errors)
    published = persistence.publish_skill_version(version_id, set_current=set_current)
    persistence.append_audit(version["project_id"], "skill_version.publish",
                             {"skill_version_id": version_id,
                              "parent_id": version["parent_id"]})
    return published


def activate_skill_version(version_id: str, *, expected_current_version: str | None = None,
                           actor: str = "user") -> dict[str, Any]:
    version = persistence.get_skill_version(version_id)
    if version["status"] != "published":
        raise ConflictError("只能把已发布版本设为当前版本", code="version_not_published")
    previous = persistence.get_project(version["project_id"]).get("current_skill_version_id")
    persistence.activate_skill_version_cas(
        version["project_id"], version_id, expected_current_version,
    )
    persistence.append_audit(version["project_id"], "skill_version.activate",
                             {"skill_version_id": version_id, "previous": previous,
                              "actor": actor})
    return persistence.get_skill_version(version_id)


def _read_version_files(version_id: str) -> dict[str, bytes]:
    root = persistence.skill_files_dir(version_id)
    files: dict[str, bytes] = {}
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def skill_version_detail(version_id: str) -> dict[str, Any]:
    version = persistence.get_skill_version(version_id)
    files = []
    for entry in version.get("files") or []:
        files.append({"path": entry["path"], "sha256": entry["sha256"],
                      "size": entry["size"],
                      "editable": skill_package.is_editable(entry["path"])})
    version["file_tree"] = files
    version["summary"] = skill_package.package_summary(_read_version_files(version_id),
                                                       version.get("manifest"))
    return version


def skill_file_content(version_id: str, relpath: str) -> dict[str, Any]:
    data = persistence.read_skill_file(version_id, relpath)
    try:
        text = data.decode("utf-8")
        return {"path": relpath, "encoding": "utf-8", "text": text,
                "editable": skill_package.is_editable(relpath)}
    except UnicodeDecodeError:
        return {"path": relpath, "encoding": "binary", "text": None,
                "size": len(data), "editable": False}


# --------------------------------------------------------------------------- #
# environments
# --------------------------------------------------------------------------- #
def create_environment(project_id: str, payload: Mapping[str, Any], *,
                       status: str = "published", parent_id: str | None = None,
                       set_current: bool | None = None) -> dict[str, Any]:
    persistence.get_project(project_id)
    parent = None
    if parent_id:
        parent = persistence.get_environment(parent_id)
        if parent["project_id"] != project_id:
            raise ValidationError("parent_id 必须属于当前项目", field_path="parent_id")
    if status not in ("draft", "published"):
        raise ValidationError("status 只能是 draft/published", field_path="status")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValidationError("业务系统 Prompt 不能为空", field_path="prompt")
    raw_model_config = payload.get("model_config")
    if not raw_model_config and parent:
        raw_model_config = dict(parent.get("model_config") or {})
    model_config = normalize_model_config(raw_model_config)
    optimizer_config = payload.get("optimizer_config") or (
        dict(parent.get("optimizer_config") or {}) if parent else {})
    if optimizer_config:
        optimizer_config = normalize_model_config(optimizer_config)
    # Public configuration contains masks at any depth (templates, headers,
    # lists). Restore only matching credential paths, never ordinary settings.
    for group_name, group in (("model_config", model_config),
                              ("optimizer_config", optimizer_config)):
        parent_group = (parent or {}).get(group_name) or {}
        same_provider = group.get("provider") == parent_group.get("provider")
        inherited = _inherit_credentials(
            group, parent_group if same_provider else {})
        _reject_credential_masks(inherited, group_name)
        if group_name == "model_config":
            model_config = inherited
        else:
            optimizer_config = inherited
    if optimizer_config:
        provider = optimizer_config.get("provider")
        if provider not in ("heuristic", "demo_scripted", "openai_http", "enterprise_ws"):
            raise ValidationError("优化模型 provider 不支持",
                                  field_path="optimizer_config.provider")
    loader_config = dict(payload.get("loader_config") or {})
    strict_finish = loader_config.get("strict_finish", False)
    if not isinstance(strict_finish, bool):
        raise ValidationError("loader_config.strict_finish 必须是布尔值",
                              field_path="loader_config.strict_finish")
    loader_config["strict_finish"] = strict_finish
    run_config = dict(payload.get("run_config") or {})
    budget_config = dict(payload.get("budget_config") or {})
    scorer_config = dict(payload.get("scorer_config") or {})
    # Accept legacy field names from older UI/API clients and normalise them.
    run_config.setdefault("base_repeats", run_config.get("repeats_baseline", 1))
    run_config.setdefault("validation_repeats", run_config.get("repeats_validation", 3))
    budget_config.setdefault("max_seconds", budget_config.get("max_duration_s"))
    scorer_config.setdefault("version", scorer_config.get("scorer_version", config.SCORER_VERSION))
    compare_error = evaluation.compare_rules_error(scorer_config.get("compare"))
    if compare_error:
        raise ValidationError(f"评分规则无效：{compare_error}", field_path="scorer_config.compare")
    scorer_config = _normalise_judge_config(scorer_config, parent)
    if not (budget_config.get("max_tokens") or budget_config.get("max_cost")):
        raise ValidationError(
            "必须设置总 Token 或金额上限（金额未知时以 Token 硬限制为准）",
            field_path="budget_config.max_tokens")
    if not budget_config.get("max_seconds"):
        raise ValidationError("必须设置最长运行时间", field_path="budget_config.max_seconds")
    acceptance_config = dict(payload.get("acceptance_config") or {})
    acceptance_config.setdefault("rules_version", config.ACCEPTANCE_RULES_VERSION)
    acceptance_config.setdefault(
        "validation_repeats", int(run_config.get("validation_repeats") or 3))
    normalization = {
        "prompt": prompt,
        "model_config": model_config,
        "optimizer_config": optimizer_config,
        "loader_config": loader_config,
        "run_config": run_config,
        "budget_config": budget_config,
        "scorer_config": scorer_config,
        "acceptance_config": acceptance_config,
        "adapter_hash": hashing.hash_json({
            "provider": model_config.get("provider"),
            "model": model_config.get("model"),
            "request_template": model_config.get("request_template"),
            "stream_parser": model_config.get("stream_parser"),
        }),
        "reproducibility_note": payload.get("reproducibility_note") or "",
    }
    environment = persistence.create_environment(
        project_id, normalization, parent_id=parent_id, status=status)
    if status == "published" and (set_current is None or set_current):
        persistence.publish_environment(environment["id"], set_current=True)
        persistence.append_audit(project_id, "environment.publish",
                                 {"environment_id": environment["id"]})
    return persistence.get_environment(environment["id"])


def publish_environment(environment_id: str, *, set_current: bool = True) -> dict[str, Any]:
    env = persistence.publish_environment(environment_id, set_current=set_current)
    persistence.append_audit(env["project_id"], "environment.publish",
                             {"environment_id": environment_id})
    return env


def activate_environment(environment_id: str,
                         expected_current_version: str | None = None) -> dict[str, Any]:
    env = persistence.get_environment(environment_id)
    if env["status"] != "published":
        raise ConflictError("只能把已发布环境设为当前环境", code="version_not_published")
    project = persistence.get_project(env["project_id"])
    if expected_current_version is not None and \
            project.get("current_environment_id") != expected_current_version:
        raise ConflictError("项目当前环境已变更", code="conflict",
                            details={"current_version":
                                     project.get("current_environment_id")})
    previous = project.get("current_environment_id")
    persistence.set_current_environment(env["project_id"], environment_id)
    persistence.append_audit(env["project_id"], "environment.activate",
                             {"environment_id": environment_id, "previous": previous})
    return persistence.get_environment(environment_id)


_SECRET_KEYS = {"api_key", "apikey", "authorization", "access_token",
                "refresh_token", "id_token", "token", "secret", "client_secret",
                "password", "passwd", "cookie", "set-cookie"}


def _is_secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered.endswith("_ref"):
        return True
    if lowered in _SECRET_KEYS:
        return True
    return lowered.endswith(("_api_key", "_secret", "_password", "_token")) \
        and lowered not in ("max_tokens", "total_tokens", "token_budget")


def _inherit_credentials(value: Any, parent: Any) -> Any:
    if isinstance(value, Mapping):
        old = parent if isinstance(parent, Mapping) else {}
        result = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                result[key] = old.get(key) if item in (None, "", "***") and key in old else item
            else:
                result[key] = _inherit_credentials(item, old.get(key))
        # Old credentials omitted by the form remain server-side. Missing
        # non-secret fields are deliberately not copied from the parent.
        for key, item in old.items():
            if key not in result and _is_secret_key(str(key)):
                result[key] = item
        return result
    if isinstance(value, list):
        old = parent if isinstance(parent, list) else []
        return [_inherit_credentials(item, old[index] if index < len(old) else None)
                for index, item in enumerate(value)]
    return value


def _reject_credential_masks(value: Any, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            field = f"{path}.{key}"
            if _is_secret_key(str(key)) and item == "***":
                raise ValidationError(
                    f"{field} 是掩码 ***，没有可继承的凭据。切换接入方式时不会沿用原凭据，"
                    "请为新接入方式重新填写",
                    field_path=field)
            _reject_credential_masks(item, field)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_credential_masks(item, f"{path}[{index}]")


def redact_secrets(value: Any) -> Any:
    """Mask credential-like values before returning configuration over the API."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                text = str(item) if item is not None else ""
                env_ref = (isinstance(item, Mapping) and
                           set(item) == {"env"} and
                           isinstance(item.get("env"), str) and
                           re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", item["env"]))
                out[key] = item if env_ref or re.match(r"^\{?env:[A-Za-z_][A-Za-z0-9_]*\}?$", text) else ("***" if item not in (None, "") else item)
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


JUDGE_PROVIDERS = ("openai_http", "enterprise_ws")


def _normalise_judge_config(scorer_config: dict[str, Any],
                            parent: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate ``scorer_config.judge`` and restore its credentials like other models."""
    judge = scorer_config.get("judge")
    if judge in (None, {}, "") or (isinstance(judge, Mapping)
                                    and str(judge.get("provider") or "") in ("", "none")):
        scorer_config.pop("judge", None)
        return scorer_config
    if not isinstance(judge, Mapping):
        raise ValidationError("评分模型配置必须是对象", field_path="scorer_config.judge")
    if judge.get("provider") not in JUDGE_PROVIDERS:
        raise ValidationError("评分模型只支持标准 API 或 WebSocket 网关",
                              field_path="scorer_config.judge.provider")
    judge = normalize_model_config(dict(judge))
    parent_judge = (((parent or {}).get("scorer_config") or {}).get("judge")) or {}
    same_provider = judge.get("provider") == parent_judge.get("provider")
    judge = _inherit_credentials(judge, parent_judge if same_provider else {})
    _reject_credential_masks(judge, "scorer_config.judge")
    if not str(judge.get("model") or "").strip():
        raise ValidationError("评分模型 ID 不能为空", field_path="scorer_config.judge.model")
    endpoint = judge.get("ws_url") if judge["provider"] == "enterprise_ws" else (
        judge.get("base_url") or judge.get("url"))
    if not str(endpoint or "").strip():
        raise ValidationError("评分模型地址不能为空", field_path="scorer_config.judge")
    scorer_config["judge"] = judge
    return scorer_config


def public_scorer_config(scorer_config: Mapping[str, Any] | None) -> dict[str, Any]:
    public = dict(scorer_config or {})
    if isinstance(public.get("judge"), Mapping):
        public["judge"] = redact_secrets(public["judge"])
    return public


def public_environment(env: Mapping[str, Any]) -> dict[str, Any]:
    public = dict(env)
    public["model_config"] = redact_secrets(env.get("model_config") or {})
    public["optimizer_config"] = redact_secrets(env.get("optimizer_config") or {})
    public["scorer_config"] = public_scorer_config(env.get("scorer_config"))
    public["credential_status"] = {
        "model_config": _credential_status(env.get("model_config") or {}),
        "optimizer_config": _credential_status(env.get("optimizer_config") or {}),
        "judge_config": _credential_status(((env.get("scorer_config") or {}).get("judge")) or {}),
    }
    return public


def _credential_status(value: Mapping[str, Any]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for key, item in value.items():
        lowered = str(key).lower()
        if _is_secret_key(lowered) or lowered.endswith("_ref"):
            result[str(key)] = item not in (None, "", "***")
    return result


def test_connection(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("environment_id"):
        env = persistence.get_environment(str(payload["environment_id"]))
        model_config = dict(env.get("model_config") or {})
        supplied = payload.get("model_config")
        if isinstance(supplied, Mapping):
            for key, value in supplied.items():
                if value not in (None, "", "***"):
                    model_config[key] = value
    else:
        model_config = normalize_model_config(payload.get("model_config") or {})
    provider = model_config.get("provider")
    if provider == "demo_scripted":
        return {"ok": True, "provider": provider,
                "message": "演示适配器可用（确定性脚本，不访问外部服务）",
                "capabilities": {"routing": True, "tool_arguments": True,
                                 "native_tools": True, "usage": True,
                                 "streaming": False}}
    if provider == "heuristic":
        return {"ok": True, "provider": provider,
                "message": "未配置优化模型，运行时将记录 optimizer_not_configured 并使用冻结启发式回退",
                "capabilities": {"model": False, "heuristic": True}}
    if provider == "openai_http":
        base_url = model_config.get("url") or model_config.get("endpoint") or model_config.get("base_url")
        if not base_url:
            return {"ok": False, "provider": provider,
                    "message": "缺少 API 根地址或完整请求地址"}
        return {"ok": True, "provider": provider,
                "message": "已配置标准 API 接入；实际连通性需在该环境可访问网关时验证",
                "capabilities": {"routing": True, "tool_arguments": True,
                                 "native_tools": True, "usage": True,
                                 "streaming": False}}
    if provider == "enterprise_ws":
        ws_url = model_config.get("ws_url")
        if not ws_url:
            return {"ok": False, "provider": provider,
                    "message": "缺少企业网关 WebSocket 地址 ws/wss"}
        return {"ok": True, "provider": provider,
                "message": "已配置企业网关 WebSocket 接入；连接测试在运行案例时执行",
                "capabilities": {"routing": True, "tool_arguments": True,
                                 "native_tools": True, "usage": True,
                                 "streaming": True}}
    return {"ok": False, "provider": provider, "message": "未知 provider"}


def _probe_config(payload: Mapping[str, Any], target: str) -> dict[str, Any]:
    if target not in ("business", "optimizer", "judge"):
        raise ValidationError("target 必须是 business、optimizer 或 judge", field_path="target")
    group = {"optimizer": "optimizer_config", "judge": "judge_config"}.get(target, "model_config")
    stored: dict[str, Any] = {}
    if payload.get("environment_id"):
        env = persistence.get_environment(str(payload["environment_id"]))
        if target == "judge":
            stored = dict(((env.get("scorer_config") or {}).get("judge")) or {})
        else:
            stored = dict(env.get(group) or {})
    supplied = payload.get("model_config")
    if not isinstance(supplied, Mapping):
        supplied = payload.get(group)
    if not isinstance(supplied, Mapping):
        _reject_credential_masks(stored, "model_config")
        return normalize_model_config(stored)
    provider = supplied.get("provider") or stored.get("provider")
    merged = dict(stored) if provider == stored.get("provider") else {}
    merged.update(supplied)
    if provider == stored.get("provider"):
        merged = _inherit_credentials(merged, stored)
    _reject_credential_masks(merged, "model_config")
    merged.setdefault("provider", provider)
    return normalize_model_config(merged)


def probe_connection(payload: Mapping[str, Any]) -> dict[str, Any]:
    target = str(payload.get("target") or "business")
    input_text = payload.get("input")
    if input_text is not None:
        if not isinstance(input_text, str):
            raise ValidationError("input 必须是字符串", field_path="input")
        if len(input_text) > 16000:
            raise ValidationError("input 不能超过 16000 个字符", field_path="input")
    mode = str(payload.get("mode") or "probe")
    if mode not in ("probe", "check"):
        raise ValidationError("mode 必须是 probe 或 check", field_path="mode")
    if mode == "check":
        return test_connection({"model_config": _probe_config(payload, target)})
    from .connection_probe import probe_connection as _probe
    return _probe(_probe_config(payload, target), target=target, input_text=input_text)


# --------------------------------------------------------------------------- #
# datasets
# --------------------------------------------------------------------------- #
def _active_skill_ids(project: Mapping[str, Any]) -> list[str]:
    skill_version_id = project.get("current_skill_version_id")
    if not skill_version_id:
        return []
    try:
        version = persistence.get_skill_version(skill_version_id)
    except NotFoundError:
        return []
    return [item["skill_id"] for item in (version.get("manifest") or {}).get("subskills") or []]


def preflight_dataset(project_id: str | None, payload: Mapping[str, Any]) -> dict[str, Any]:
    from . import dataset as dataset_module

    filename = str(payload.get("filename") or "dataset.jsonl")
    content = payload.get("content")
    if content is None and isinstance(payload.get("cases"), list):
        items = [dict(item) if isinstance(item, Mapping) else item
                 for item in payload["cases"]]
        fmt = "json"
    else:
        if content is None:
            raise ValidationError("缺少数据集内容", field_path="content")
        if isinstance(content, str):
            raw = content.encode("utf-8")
        else:
            raw = bytes(content)
        items, fmt = dataset_module.parse_payload(raw, filename)
    purpose = payload.get("import_purpose")
    if "import_purpose" in payload and purpose not in ("optimize", "validation", "preserve"):
        raise ValidationError("import_purpose 必须是 optimize、validation 或 preserve",
                              field_path="import_purpose")
    if purpose in ("optimize", "validation"):
        if any(isinstance(item, Mapping) and item.get("split") == "final_test"
               for item in items):
            raise ValidationError("文件包含最终测试集，请选择保留文件划分后导入",
                                  code="final_test_requires_preserve",
                                  field_path="import_purpose")
        items = [{**item, "split": purpose} if isinstance(item, Mapping)
                 and not item.get("_parse_error") else item for item in items]
    project = persistence.get_project(project_id) if project_id else {}
    skill_ids = _active_skill_ids(project)
    split_config = payload.get("split_config") or {
        "optimize": 1, "validation": 0, "final_test": 0}
    if purpose in ("optimize", "validation"):
        split_config = {name: int(name == purpose)
                        for name in ("optimize", "validation", "final_test")}
    seed = int(payload.get("seed") or 42)
    manifest = {}
    if project:
        if project.get("current_skill_version_id"):
            manifest = persistence.get_skill_version(
                project["current_skill_version_id"]).get("manifest") or {}
    report = dataset_module.validate_and_prepare(
        items, manifest=manifest,
        split_config=split_config, seed=seed)
    cases = report.get("cases") or []
    report["cases"] = cases
    report["ok"] = not report.get("errors")
    report["format"] = fmt
    report["filename"] = filename
    report["split_config"] = split_config
    coverage_stats = dataset_module.coverage(cases, skill_ids)
    coverage_stats["single_route_cases"] = max(
        0, int(coverage_stats.get("total") or 0)
        - int(coverage_stats.get("multi_route_cases") or 0))
    report["coverage"] = coverage_stats
    report["case_summaries"] = [
        {"case_id": case["case_id"], "query": case["query"],
         "split": case.get("split"), "group_id": case.get("group_id"),
         "tags": case.get("tags") or []} for case in cases
    ]
    return report


def create_dataset(project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    persistence.get_project(project_id)
    report = preflight_dataset(project_id, payload)
    cases = report.get("cases") or []
    errors = report.get("errors") or []
    exclude_invalid = bool(payload.get("exclude_invalid"))
    if errors and not exclude_invalid:
        raise ValidationError("数据集校验未通过，请修复或明确剔除无效行",
                              code="dataset_invalid", details=errors)
    if not cases:
        raise ValidationError("没有有效案例可发布", code="dataset_invalid",
                              details=errors)
    split_config = report["split_config"]
    dataset = persistence.create_dataset(
        project_id, cases, split_config,
        source_name=str(payload.get("source_name") or payload.get("filename") or ""),
        import_report={"errors": errors, "warnings": report.get("warnings") or [],
                       "stats": report.get("stats") or {},
                       "excluded_invalid": exclude_invalid and bool(errors)},
        set_current=True)
    splits = {}
    for case in cases:
        split = case.get("split") or "optimize"
        splits[split] = splits.get(split, 0) + 1
    if "final_test" not in splits and len(cases) >= 10:
        pass
    persistence.append_audit(project_id, "dataset.publish",
                             {"dataset_id": dataset["id"], "cases": len(cases),
                              "splits": splits})
    return dataset


def create_dataset_draft(dataset_id: str) -> dict[str, Any]:
    draft = persistence.clone_dataset(dataset_id)
    persistence.append_audit(draft["project_id"], "dataset.draft_create",
                             {"dataset_id": draft["id"], "parent_id": dataset_id})
    return draft


def update_dataset_case(dataset_id: str, case: Mapping[str, Any], expected_hash: str,
                        case_id: str | None = None) -> dict[str, Any]:
    dataset = persistence.get_dataset(dataset_id)
    if dataset["status"] != "draft":
        raise ConflictError("已发布数据集不可编辑", code="dataset_read_only")
    if not isinstance(case, Mapping):
        raise ValidationError("case 必须为 JSON 对象", field_path="case")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise ValidationError("保存需要 expected_hash", field_path="expected_hash")
    if case_id is not None and str(case.get("case_id") or "") != str(case_id):
        raise ValidationError("case_id 不允许改名", field_path="case.case_id")
    split = case.get("split")
    if split not in ("optimize", "validation", "final_test"):
        raise ValidationError("split 不合法", field_path="case.split")
    current = persistence.get_case(dataset_id, str(case.get("case_id") or ""))
    project = persistence.get_project(dataset["project_id"])
    manifest = {}
    if project.get("current_skill_version_id"):
        manifest = persistence.get_skill_version(project["current_skill_version_id"]).get("manifest") or {}
    from . import dataset as dataset_module
    report = dataset_module.validate_and_prepare([dict(case)], manifest=manifest,
                                                  split_config={"optimize": 1, "validation": 0, "final_test": 0})
    if report.get("errors"):
        raise ValidationError("案例校验未通过", code="dataset_invalid", details=report["errors"])
    canonical = (report.get("cases") or [dict(case)])[0]
    all_rows = persistence.all_cases(dataset_id)
    for row in all_rows:
        if canonical.get("group_id") and row.get("group_id") == canonical.get("group_id") and row.get("case_id") != canonical.get("case_id") \
                and row.get("split") != canonical.get("split"):
            raise ValidationError("同 group 不能跨 split", code="group_split_conflict",
                                  field_path="case.split")
    revised = [canonical if row["case_id"] == canonical["case_id"] else row for row in all_rows]
    full_report = preflight_dataset(dataset["project_id"], {
        "cases": revised, "split_config": dataset.get("split_config") or {}})
    result = persistence.update_case_cas(dataset_id, canonical, expected_hash,
                                         {"errors": full_report.get("errors") or [],
                                          "warnings": full_report.get("warnings") or [],
                                          "stats": full_report.get("stats") or {}})
    persistence.append_audit(dataset["project_id"], "dataset.case_update",
                             {"dataset_id": dataset_id, "case_id": canonical["case_id"]})
    return result


def publish_dataset_version(dataset_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    dataset = persistence.get_dataset(dataset_id)
    if dataset["status"] != "draft":
        if payload.get("expected_hash") is not None:
            raise ConflictError("数据集已经发布，请刷新版本状态", code="dataset_conflict")
        return persistence.publish_dataset(dataset_id, set_current=bool(payload.get("set_current", True)))
    if not isinstance(payload.get("expected_hash"), str) or not payload["expected_hash"] \
            or "expected_current_version" not in payload:
        raise ValidationError("发布需要 expected_hash 与 expected_current_version")
    cases = persistence.all_cases(dataset_id)
    groups: dict[str, str] = {}
    for row in cases:
        group = row.get("group_id")
        if group:
            previous = groups.setdefault(str(group), str(row.get("split")))
            if previous != str(row.get("split")):
                raise ValidationError("同 group 不能跨 split", code="group_split_conflict")
    report = preflight_dataset(dataset["project_id"], {"cases": cases,
        "split_config": dataset.get("split_config") or {"optimize": 1, "validation": 0, "final_test": 0}})
    if report.get("errors"):
        raise ValidationError("数据集校验未通过", code="dataset_invalid", details=report["errors"])
    result = persistence.publish_dataset_cas(dataset_id, payload["expected_hash"],
                                           payload["expected_current_version"],
                                           bool(payload.get("set_current", True)),
                                           import_report={"errors": [],
                                                          "warnings": report.get("warnings") or [],
                                                          "stats": report.get("stats") or {}})
    persistence.append_audit(dataset["project_id"], "dataset.publish",
                             {"dataset_id": dataset_id, "parent_id": dataset.get("parent_id")})
    return result


def activate_dataset(dataset_id: str,
                     expected_current_version: str | None = None) -> dict[str, Any]:
    dataset = persistence.get_dataset(dataset_id)
    if dataset["status"] != "published":
        raise ConflictError("只能把已发布数据集设为当前版本", code="version_not_published")
    project = persistence.get_project(dataset["project_id"])
    if expected_current_version is not None and \
            project.get("current_dataset_id") != expected_current_version:
        raise ConflictError("项目当前数据集已变更", code="conflict",
                            details={"current_version": project.get("current_dataset_id")})
    previous = project.get("current_dataset_id")
    persistence.activate_dataset_cas(dataset["project_id"], dataset_id, previous)
    persistence.append_audit(dataset["project_id"], "dataset.activate",
                             {"dataset_id": dataset_id, "previous": previous})
    return persistence.get_dataset(dataset_id)


def dataset_detail(dataset_id: str) -> dict[str, Any]:
    from . import dataset as dataset_module

    dataset = persistence.get_dataset(dataset_id)
    project = persistence.get_project(dataset["project_id"])
    cases = persistence.all_cases(dataset_id)
    skill_ids = _active_skill_ids(project)
    coverage = dataset_module.coverage(cases, skill_ids)
    coverage["single_route_cases"] = max(
        0, int(coverage.get("total") or 0)
        - int(coverage.get("multi_route_cases") or 0))
    splits: dict[str, int] = {}
    for case in cases:
        split = case.get("split") or "optimize"
        splits[split] = splits.get(split, 0) + 1
    dataset["split_counts"] = splits
    dataset["coverage"] = coverage
    dataset["warnings"] = []
    if not splits.get("final_test"):
        dataset["warnings"].append({
            "code": "no_final_test",
            "message": "没有最终测试集，无法做独立最终验收；最终测试只应在阶段验收时运行。",
        })
    dataset["cases"] = cases if len(cases) <= 200 else []
    if len(cases) > 200:
        dataset["case_page"] = persistence.list_cases(dataset_id, limit=100)
    return dataset


def export_dataset(dataset_id: str) -> bytes:
    cases = persistence.all_cases(dataset_id)
    lines = []
    for case in cases:
        lines.append(json.dumps({
            "id": case["case_id"],
            "query": case["query"],
            "context": case.get("context") or {},
            "expected": case.get("expected") or {},
            "fixtures": case.get("fixtures") or [],
            "split": case.get("split"),
            "group_id": case.get("group_id"),
            "tags": case.get("tags") or [],
        }, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- #
# runs: creation & views
# --------------------------------------------------------------------------- #
def run_fingerprint(skill_version_id: str, environment_id: str, dataset_id: str,
                    scorer_version: str, repeats: int) -> str:
    return hashing.hash_json({
        "skill_version_id": skill_version_id,
        "environment_id": environment_id,
        "dataset_id": dataset_id,
        "scorer_version": scorer_version,
        "repeats": repeats,
        # Changing finish handling must invalidate cached baselines.  This is
        # deliberately a frozen revision rather than inferred from old rows.
        "finish_policy": "finish-v2-business-independent",
        # A baseline produced before read-before-act enforcement and per-turn
        # context checks is not evidence for the current execution contract.
        "skill_load_policy": "progressive-loop-v2",
    })


def _baseline_repeats(env: Mapping[str, Any]) -> int:
    run_config = env.get("run_config") or {}
    return int(run_config.get("base_repeats") or 1)


def _validation_repeats(env: Mapping[str, Any]) -> int:
    run_config = env.get("run_config") or {}
    acceptance = env.get("acceptance_config") or {}
    return int(acceptance.get("validation_repeats")
               or run_config.get("validation_repeats") or 3)


def _validate_source(project: Mapping[str, Any], source_run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source = persistence.get_run(source_run_id)
    if (source.get("project_id") != project["id"] or source.get("mode") != "optimize"
            or source.get("status") != "completed" or source.get("decision") is not None):
        raise ConflictError("验收来源必须是本项目已完成测试集重跑的优化运行", code="invalid_source_run")
    if (project.get("current_skill_version_id") != source.get("baseline_skill_version_id")
            or project.get("current_environment_id") != source.get("environment_id")):
        raise ConflictError("当前 Skill 或环境与优化运行不一致，请重新优化", code="version_conflict")
    candidate = persistence.get_candidate_for_run(source_run_id)
    version_id = source.get("candidate_skill_version_id")
    if not candidate or not version_id or candidate.get("skill_version_id") != version_id:
        raise ConflictError("来源运行没有可验收的候选", code="no_candidate")
    version = persistence.get_skill_version(version_id)
    if (version.get("project_id") != project["id"] or version.get("source_run_id") != source_run_id
            or not (candidate.get("checks") or {}).get("ok")
            or not persistence.skill_version_integrity(version_id).get("ok")):
        raise ConflictError("候选版本无效或已变化，请重新优化", code="candidate_changed")
    return source, candidate


def _check_validation_independence(source: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]) -> None:
    validation = [case for case in cases if case.get("split") == "validation"]
    if not validation:
        raise ValidationError("正式验收需要独立验收集，请先补充验收案例", code="dataset_incomplete")
    source_cases = persistence.all_cases(source["dataset_id"])
    others = [case for case in source_cases if (case.get("split") or "optimize") != "validation"]
    others += [case for case in cases if (case.get("split") or "optimize") != "validation"]
    ids = {case.get("case_id") for case in others}
    queries = {" ".join((case.get("query") or "").casefold().split()) for case in others}
    groups = {case.get("group_id") for case in others if case.get("group_id")}
    overlap = [case["case_id"] for case in validation
               if case.get("case_id") in ids
               or " ".join((case.get("query") or "").casefold().split()) in queries
               or (case.get("group_id") and case.get("group_id") in groups)]
    if overlap:
        raise ValidationError("独立验收集与测试集或最终测试集存在重复案例、内容或分组，请调整后再验收",
                              code="dataset_leakage", details={"case_ids": overlap})


def create_run(payload: Mapping[str, Any]) -> dict[str, Any]:
    project_id = payload.get("project_id")
    if not project_id:
        raise ValidationError("缺少 project_id", field_path="project_id")
    project = persistence.get_project(project_id)
    mode = payload.get("mode") or "baseline"
    if mode not in ("evaluate", "baseline", "optimize", "validate", "final_test"):
        raise ValidationError("mode 只能是 evaluate/baseline/optimize/validate/final_test",
                              field_path="mode")
    idempotency_key = payload.get("idempotency_key")
    if idempotency_key:
        existing = persistence.find_idempotent_run(project_id, idempotency_key)
        if existing:
            return existing
    skill_version_id = project.get("current_skill_version_id")
    environment_id = project.get("current_environment_id")
    dataset_id = project.get("current_dataset_id")
    for field, actual in (("expected_skill_version_id", skill_version_id),
                          ("expected_environment_id", environment_id),
                          ("expected_dataset_id", dataset_id)):
        if field in payload and payload[field] != actual:
            raise ConflictError("当前版本已变更，请重新选择后启动", code="version_conflict",
                                details={"field": field, "actual": actual})
    missing = []
    if not skill_version_id:
        missing.append("skill_package")
    if not environment_id:
        missing.append("environment")
    if not dataset_id:
        missing.append("dataset")
    if missing:
        raise ValidationError("项目配置不完整，无法启动运行", code="project_incomplete",
                              details={"missing": missing})
    env = persistence.get_environment(environment_id)
    dataset = persistence.get_dataset(dataset_id)
    if dataset.get("status") != "published":
        raise ConflictError("数据集草稿尚未发布，不能用于运行", code="version_not_published")
    splits = dataset.get("split_counts") or {}
    cases = persistence.all_cases(dataset_id)
    split_names = {case.get("split") or "optimize" for case in cases}
    if mode in ("optimize", "baseline", "evaluate") and "optimize" not in split_names:
        raise ValidationError("日常评测和优化需要测试集案例", code="dataset_incomplete")
    source = candidate = None
    if mode == "validate":
        source_run_id = payload.get("source_run_id")
        if not source_run_id:
            raise ValidationError("正式验收需要 source_run_id", field_path="source_run_id")
        source, candidate = _validate_source(project, source_run_id)
        _check_validation_independence(source, cases)
    if mode == "final_test" and "final_test" not in split_names:
        raise ValidationError("最终测试运行需要最终测试集", code="dataset_incomplete")
    if mode == "final_test" and payload.get("skill_version_id"):
        selected_version = persistence.get_skill_version(payload["skill_version_id"])
        if selected_version["project_id"] != project_id:
            raise ConflictError("所选 Skill 版本不属于当前项目", code="version_conflict")
        skill_version_id = selected_version["id"]
    scorer_version = (env.get("scorer_config") or {}).get("version") or config.SCORER_VERSION
    repeats = _baseline_repeats(env)
    fingerprint = run_fingerprint(skill_version_id, environment_id, dataset_id,
                                  scorer_version, repeats)
    budget = env.get("budget_config") or {}
    acceptance = dict(env.get("acceptance_config") or {})
    acceptance.setdefault("rules_version", config.ACCEPTANCE_RULES_VERSION)
    acceptance["validation_repeats"] = _validation_repeats(env)
    acceptance["frozen"] = {
        "fingerprint": fingerprint,
        "scorer_version": scorer_version,
        "base_repeats": repeats,
    }
    parent_run_id = source["id"] if source else payload.get("parent_run_id")
    baseline_run_id = None
    baseline_cache_hit = False
    if mode == "optimize":
        reuse = payload.get("reuse_baseline", True)
        cached = persistence.find_cached_baseline(project_id, fingerprint) if reuse else None
        if cached:
            baseline_run_id = cached["id"]
            baseline_cache_hit = True
        elif parent_run_id is None:
            parent = persistence.find_cached_baseline(project_id, fingerprint)
            parent_run_id = parent["id"] if parent else None
    snapshot = {
        "project": {"id": project["id"], "name": project["name"]},
        "skill_version": _version_snapshot(skill_version_id),
        "environment": {"id": env["id"], "version_seq": env.get("version_seq"),
                        "hash": env.get("hash"), "provider":
                        (env.get("model_config") or {}).get("provider")},
        "dataset": {"id": dataset["id"], "version_seq": dataset.get("version_seq"),
                    "hash": dataset.get("hash"), "case_count": dataset.get("case_count"),
                    "splits": sorted(split_names)},
        "scorer_version": scorer_version,
        "base_repeats": repeats,
        "validation_repeats": _validation_repeats(env),
        "fingerprint": fingerprint,
        "rules_version": acceptance["rules_version"],
        "adapter_hash": env.get("adapter_hash"),
        "reproducibility_note": env.get("reproducibility_note") or "",
    }
    if source:
        snapshot["source_run_id"] = source["id"]
        snapshot["candidate_skill_version"] = _version_snapshot(source["candidate_skill_version_id"])
    run = persistence.create_run(
        project_id, mode=mode, baseline_skill_version_id=skill_version_id,
        environment_id=environment_id, dataset_id=dataset_id,
        candidate_skill_version_id=(source.get("candidate_skill_version_id") if source else None), parent_run_id=parent_run_id,
        baseline_run_id=baseline_run_id, budget=budget, acceptance=acceptance,
        snapshot=snapshot, idempotency_key=idempotency_key,
        fingerprint=fingerprint)
    persistence.update_run(run["id"], rules_version=acceptance["rules_version"])
    if candidate:
        persistence.create_candidate(
            run["id"], skill_version_id=candidate["skill_version_id"],
            diagnosis=candidate.get("diagnosis") or {}, hypotheses=candidate.get("hypotheses") or [],
            changed_files=candidate.get("changed_files") or [], patch=candidate.get("patch") or {},
            expected_effect=candidate.get("expected_effect") or "", risk_cases=candidate.get("risk_cases") or [],
            checks=candidate.get("checks") or {})
    if baseline_cache_hit and baseline_run_id:
        _copy_baseline_results(baseline_run_id, run["id"])
        persistence.update_run(run["id"], baseline_cache_hit=1,
                               baseline_run_id=baseline_run_id,
                               stage_progress={"stage": "preflight", "completed": 0,
                                               "total": 0, "message": "已复用兼容基线"})
    persistence.append_audit(project_id, "run.create",
                             {"run_id": run["id"], "mode": mode,
                              "fingerprint": fingerprint,
                              "baseline_cache_hit": baseline_cache_hit})
    return persistence.get_run(run["id"])


def _version_snapshot(version_id: str) -> dict[str, Any]:
    try:
        version = persistence.get_skill_version(version_id)
    except NotFoundError:
        return {"id": version_id}
    return {"id": version["id"], "label": version["id"],
            "parent_id": version.get("parent_id"),
            "status": version.get("status"), "content_hash": version.get("content_hash"),
            "created_at": version.get("created_at"),
            "subskill_count": len((version.get("manifest") or {}).get("subskills") or [])}


def _copy_baseline_results(source_run_id: str, target_run_id: str) -> None:
    rows = persistence.get_case_results_for_run(source_run_id, "baseline")
    for row in rows:
        persistence.save_case_result(
            target_run_id, row["case_id"], "baseline",
            variant=row.get("variant") or "main",
            repeat_index=row.get("repeat_index") or 1,
            split=row.get("split") or "",
            selected_skills=row.get("selected_skills") or [],
            action=row.get("action"),
            tool_calls=row.get("tool_calls") or [],
            missing_fields=row.get("missing_fields") or [],
            actual=row.get("actual") or {},
            scores=row.get("scores") or {},
            error_code=row.get("error_code"),
            error_message=row.get("error_message"),
            result_source=row.get("result_source"),
            capabilities=row.get("capabilities") or {},
            usage=row.get("usage") or {},
            duration_ms=row.get("duration_ms"),
        )


def _metric_block(metrics: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not metrics:
        return None
    return {
        "routing": metrics.get("routing"),
        "tool_args": metrics.get("tool_args"),
        "joint": metrics.get("joint"),
        "diagnostics": metrics.get("diagnostics"),
        "execution": metrics.get("execution"),
    }


def delta_metrics(baseline: Mapping[str, Any] | None,
                  candidate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not baseline or not candidate:
        return None
    delta: dict[str, Any] = {}
    for key in ("routing", "tool_args", "joint"):
        before = (baseline.get(key) or {}).get("value")
        after = (candidate.get(key) or {}).get("value")
        delta[key] = None if before is None or after is None else round(after - before, 4)
    return delta


def overview(project_id: str) -> dict[str, Any]:
    project = persistence.get_project(project_id)
    env = None
    dataset = None
    skill_version = None
    if project.get("current_environment_id"):
        try:
            env = persistence.get_environment(project["current_environment_id"])
        except NotFoundError:
            env = None
    if project.get("current_dataset_id"):
        try:
            dataset = persistence.get_dataset(project["current_dataset_id"])
        except NotFoundError:
            dataset = None
    if project.get("current_skill_version_id"):
        try:
            skill_version = persistence.get_skill_version(
                project["current_skill_version_id"])
        except NotFoundError:
            skill_version = None
    items: dict[str, Any] = {}
    items["skill_package"] = {
        "ok": skill_version is not None,
        "label": "Skill 包",
        "detail": (f"{skill_version.get('id')} · {len((skill_version.get('manifest') or {}).get('subskills') or [])} 个子技能"
                   if skill_version else "尚未导入 Skill 包"),
        "route": "#/projects/{}/skill".format(project_id),
    }
    items["environment"] = {
        "ok": env is not None and env.get("status") == "published",
        "label": "执行环境",
        "detail": (f"版本 v{env.get('version_seq')} · {((env.get('model_config') or {}).get('provider'))}"
                   if env else "尚未发布执行环境"),
        "route": "#/projects/{}/environment".format(project_id),
    }
    dataset_ok = dataset is not None and bool(dataset.get("case_count"))
    items["dataset"] = {
        "ok": dataset_ok,
        "label": "数据集",
        "detail": (f"版本 v{dataset.get('version_seq')} · {dataset.get('case_count')} 个案例"
                   if dataset else "尚未发布数据集"),
        "route": "#/projects/{}/dataset".format(project_id),
    }
    fingerprint = None
    baseline = None
    compatible = False
    reason = ""
    if skill_version and env and dataset and dataset.get("case_count"):
        scorer_version = (env.get("scorer_config") or {}).get("version") or config.SCORER_VERSION
        fingerprint = run_fingerprint(skill_version["id"], env["id"], dataset["id"],
                                      scorer_version, _baseline_repeats(env))
        baseline = persistence.find_cached_baseline(project_id, fingerprint)
        compatible = baseline is not None
        reason = "" if compatible else "当前 Skill 包/环境/数据集/评分器/重复次数组合尚无兼容基线"
    items["baseline"] = {
        "ok": compatible,
        "label": "兼容基线",
        "detail": (f"基线运行 {baseline['id']}" if baseline else reason),
        "route": "#/runs/{}".format(baseline["id"]) if baseline else None,
    }
    missing = [key for key in ("skill_package", "environment", "dataset")
               if not items[key]["ok"]]
    ready = not missing
    metrics = None
    evaluated_at = None
    stale = False
    if baseline:
        baseline_metrics = (baseline.get("metrics") or {}).get("baseline")
        if baseline_metrics is None:
            baseline_metrics = _metric_block(baseline.get("metrics") or {})
        metrics = baseline_metrics if isinstance(baseline_metrics, dict) else None
        evaluated_at = baseline.get("finished_at") or baseline.get("created_at")
        stale = not compatible
    primary_action = {
        "mode": "baseline" if ready and not compatible else ("optimize" if ready else "configure"),
        "label": ("单轮优化" if ready and compatible
                  else ("运行基线" if ready else "完成配置")),
        "enabled": ready,
        "reason": "" if ready else "缺少: " + "、".join(items[key]["label"] for key in missing),
    }
    recent = persistence.list_runs(project_id=project_id, limit=8)
    for item in recent["items"]:
        item["decision_code"] = run_decision_code(item)
    pending_candidate = (_pending_candidate(project, recent["items"])
                         if ready else None)
    no_failures = bool(ready and compatible and _baseline_has_no_failures(metrics))
    if pending_candidate:
        primary_action = {
            "mode": "apply",
            "label": "查看并应用候选",
            "enabled": True,
            "run_id": pending_candidate["run_id"],
            "reason": "上一轮候选已通过评测，应用后才会成为当前版本。",
        }
    elif no_failures:
        primary_action = {
            "mode": "add_cases",
            "label": "补充测试案例",
            "enabled": True,
            "reason": "当前基线在优化集上已全部通过，没有可改进的失败案例。",
        }
    latest_evaluation = (persistence.find_latest_evaluation(project_id, fingerprint)
                         if fingerprint else None)
    display_run = latest_evaluation or baseline
    display_metrics = ((display_run.get("metrics") or {}).get("baseline") or {}) if display_run else {}
    return {
        "project": project,
        "completeness": {"ready": ready, "missing": missing, "items": items},
        "metrics": (None if not display_run else {
            "routing": display_metrics.get("routing"),
            "tool_args": display_metrics.get("tool_args"),
            "joint": display_metrics.get("joint"),
            "dataset": {"id": dataset["id"], "version_seq": dataset.get("version_seq"),
                        "case_count": dataset.get("case_count")},
            "evaluated_at": display_run.get("finished_at") or display_run.get("created_at"),
            "run_id": display_run["id"], "mode": display_run["mode"], "stale": False,
        }),
        "recent_runs": recent,
        "baseline": {
            "available": baseline is not None,
            "run_id": baseline["id"] if baseline else None,
            "compatible": compatible,
            "reason": reason,
        },
        "primary_action": primary_action,
        "pending_candidate": pending_candidate,
        "baseline_all_passed": no_failures,
        "template_available": True,
    }


def run_decision_code(run: Mapping[str, Any]) -> str | None:
    """Finer-grained outcome for display (e.g. ``no_failures``).

    ``decision`` stays ``accept / reject / inconclusive``; the code tells the UI
    when an inconclusive run simply had nothing to fix.
    """
    if run.get("decision") != "inconclusive":
        return None
    record = persistence.get_decision_for_run(run["id"])
    rationale = (record or {}).get("rationale") or {}
    return rationale.get("error_code") or None


def _baseline_has_no_failures(metrics: Mapping[str, Any] | None) -> bool:
    joint = (metrics or {}).get("joint") or {}
    evaluated = joint.get("evaluated")
    passed = joint.get("passed")
    total = joint.get("total_cases", evaluated)
    if not isinstance(evaluated, int) or not isinstance(passed, int) or evaluated <= 0:
        return False
    return passed == evaluated and total == evaluated


def _pending_candidate(project: Mapping[str, Any],
                       runs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Latest accepted, still-applicable candidate of the project (if any)."""
    for run in runs:
        if run.get("mode") not in ("optimize", "validate"):
            continue
        if run.get("status") != "completed" or run.get("decision") != "accept":
            # Only the most recent finished optimize run counts; an older
            # accepted run behind a newer one is no longer the next step.
            if run.get("status") in config.TERMINAL_RUN_STATUSES:
                return None
            continue
        view = run_view(run["id"])
        if not view.get("can_apply"):
            return None
        joint = (view.get("metrics") or {})
        return {
            "run_id": run["id"],
            "finished_at": run.get("finished_at"),
            "baseline_joint": ((joint.get("baseline") or {}).get("joint") or {}).get("value"),
            "candidate_joint": ((joint.get("candidate") or {}).get("joint") or {}).get("value"),
        }
    return None


def run_view(run_id: str) -> dict[str, Any]:
    run = persistence.get_run(run_id)
    snapshot = run.get("snapshot") or {}
    project = None
    try:
        project = persistence.get_project(run["project_id"])
    except NotFoundError:
        project = None
    candidate = persistence.get_candidate_for_run(run_id)
    decision = persistence.get_decision_for_run(run_id)
    run["candidate"] = None
    if candidate:
        run["candidate"] = {
            "id": candidate["id"],
            "skill_version_id": candidate.get("skill_version_id"),
            "diagnosis": candidate.get("diagnosis") or {},
            "hypotheses": candidate.get("hypotheses") or [],
            "changed_files": candidate.get("changed_files") or [],
            "patch": candidate.get("patch") or {},
            "expected_effect": candidate.get("expected_effect") or "",
            "risk_cases": candidate.get("risk_cases") or [],
            "checks": candidate.get("checks") or {},
        }
    run["decision_record"] = decision
    run["project_name"] = (project or {}).get("name")
    run["current_skill_version_id"] = (project or {}).get("current_skill_version_id")
    stale_reasons: list[str] = []
    for field, label in (("current_skill_version_id", "Skill"),
                         ("current_environment_id", "环境"),
                         ("current_dataset_id", "数据集")):
        expected = run.get({
            "current_skill_version_id": "baseline_skill_version_id",
            "current_environment_id": "environment_id",
            "current_dataset_id": "dataset_id",
        }[field])
        if project and project.get(field) != expected:
            stale_reasons.append(f"{label}已变更")
    acceptance_config = dict(run.get("acceptance") or {})
    rationale = (decision or {}).get("rationale") or {}
    run["acceptance"] = {
        **acceptance_config,
        "decision": run.get("decision"),
        "reason": rationale.get("reason") or run.get("decision_reason"),
        "rules": rationale.get("rules") or [],
        "applied_at": (decision or {}).get("applied_at"),
        "expected_current_version": (decision or {}).get(
            "expected_current_version"),
        "rules_version": (decision or {}).get("rules_version")
        or acceptance_config.get("rules_version"),
    }
    run["rules_version"] = run.get("rules_version") or (
        run["acceptance"].get("rules_version"))
    metrics = run.get("metrics") or {}
    run["metrics"] = {
        "baseline": _metric_block(metrics.get("baseline")
                                  or metrics.get("baseline_optimize")),
        "candidate": _metric_block(metrics.get("candidate")
                                   or metrics.get("candidate_optimize")),
        "delta": metrics.get("delta") or delta_metrics(
            metrics.get("baseline") or metrics.get("baseline_optimize"),
            metrics.get("candidate") or metrics.get("candidate_optimize")),
        "validation": metrics.get("validation"),
        "final": metrics.get("final"),
        "temporary": run["status"] not in config.TERMINAL_RUN_STATUSES,
    }
    skill_snapshot = snapshot.get("skill_version") or {}
    if skill_snapshot and not skill_snapshot.get("label"):
        skill_snapshot["label"] = skill_snapshot.get("id") or run.get(
            "baseline_skill_version_id")
        skill_snapshot["id"] = skill_snapshot.get("id") or run.get(
            "baseline_skill_version_id")
        snapshot["skill_version"] = skill_snapshot
    run["snapshot"] = snapshot
    # Execution stats / cost feeds the "结果概览" tab; prefer the candidate phase
    # once it exists, otherwise fall back to baseline or final-test results.
    candidate_block = metrics.get("candidate") or {}
    baseline_block = metrics.get("baseline") or {}
    final_block = metrics.get("final") or {}
    execution = (candidate_block.get("execution")
                 or baseline_block.get("execution")
                 or final_block.get("execution") or {})
    run["execution"] = execution
    run["metrics"]["execution"] = execution
    cost = dict(run.get("cost") or {})
    cost.setdefault("total_tokens", execution.get("tokens"))
    cost.setdefault("tokens", execution.get("tokens"))
    cost.setdefault("duration_ms", execution.get("duration_ms"))
    run["cost"] = cost
    stats = run.get("case_stats") or {}
    run["case_stats"] = {
        **stats,
        "planned": execution.get("planned") or stats.get("case_results"),
        "completed": execution.get("completed") or stats.get("case_results"),
        "valid": execution.get("valid") or stats.get("scored"),
        "errors": execution.get("infra_errors") or 0,
    }
    run["source_run_id"] = run.get("parent_run_id") if run.get("mode") == "validate" else None
    run["can_validate"] = False
    if project and run.get("mode") == "optimize":
        try:
            _validate_source(project, run_id)
            run["can_validate"] = True
        except (ConflictError, NotFoundError):
            pass
    run["can_apply"] = (
        run["status"] == "completed" and run.get("decision") == "accept"
        and candidate is not None and candidate.get("skill_version_id") is not None
        and decision is not None and not decision.get("applied_at")
        and not stale_reasons
    )
    run["applicability"] = {
        "current": not stale_reasons,
        "stale_reasons": stale_reasons,
        "message": "当前评测条件仍有效" if not stale_reasons else "；".join(stale_reasons) + "，需要重新评测",
    }
    run["stage_labels"] = _stage_labels()
    run["optimizer_mode"] = _optimizer_mode(run.get("environment_id"))
    run["decision_code"] = ((decision or {}).get("rationale") or {}).get("error_code") \
        if run.get("decision") == "inconclusive" else None
    run["issues_summary"] = _issues_summary(
        run_id, heuristic_mode=run["optimizer_mode"] == "heuristic")
    return run


def _optimizer_mode(environment_id: str | None) -> str | None:
    """``heuristic`` when the environment deliberately uses rule-based repair."""
    if not environment_id:
        return None
    try:
        env = persistence.get_environment(environment_id)
    except NotFoundError:
        return None
    provider = str(((env.get("optimizer_config") or {}).get("provider")) or "").strip().lower()
    if provider == "heuristic":
        return "heuristic"
    return "model" if provider and provider not in ("none", "disabled", "off") else None


def _stage_labels() -> dict[str, str]:
    from . import event_types

    return dict(event_types.STAGE_LABELS)


def _issues_summary(run_id: str, *, heuristic_mode: bool = False) -> dict[str, Any]:
    events = persistence.list_events(run_id, after_seq=0, limit=1000, errors_only=True)
    items = []
    for event in events:
        payload = event.get("payload") or {}
        issue = payload.get("issue")
        # EventEmitter.issue() records the issue fields at the top level of the
        # payload; adapters/optimizer nest them under "issue". Accept both.
        if not isinstance(issue, Mapping) and payload.get("error_code") and payload.get("title"):
            issue = payload
        if not isinstance(issue, Mapping) or not issue:
            continue
        if heuristic_mode and issue.get("error_code") == "optimizer_not_configured":
            # Rule-based repair was chosen on purpose; older runs recorded it
            # as an error, which is not a problem the user has to fix.
            continue
        items.append({
            "seq": event["seq"],
            "level": event.get("level"),
            "stage": event.get("stage"),
            "case_id": event.get("case_id"),
            "type": event.get("type"),
            "title": issue.get("title"),
            "message": issue.get("message"),
            "impact": issue.get("impact"),
            "next_action": issue.get("next_action"),
            "error_code": issue.get("error_code"),
            "cause_event_seq": issue.get("cause_event_seq"),
        })
    return {"items": items, "total": len(items),
            "counts": {"error": sum(1 for i in items if i["level"] == "error"),
                       "warning": sum(1 for i in items if i["level"] == "warning")}}


def run_cases(run_id: str, *, phase: str | None = None,
              only_failed: bool = False, search: str | None = None,
              changed: str | None = None, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    run = persistence.get_run(run_id)
    dataset_id = run["dataset_id"]
    case_map = {case["case_id"]: case for case in persistence.all_cases(dataset_id)}
    results = persistence.get_case_results_for_run(run_id)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in results:
        key = (row["case_id"], row.get("variant") or "main")
        phase_map = by_key.setdefault(key, {})
        existing = phase_map.get(row["phase"])
        if existing is None or (row.get("repeat_index") or 1) < (
                existing.get("repeat_index") or 1):
            phase_map[row["phase"]] = row
    phases = ["baseline", "candidate"] if phase in (None, "", "all") else [phase]
    items = []
    for (case_id, variant), phases_map in by_key.items():
        case = case_map.get(case_id)
        if case is None:
            continue
        if only_failed:
            if not any(not (row.get("scores") or {}).get("passed")
                       for row in phases_map.values()):
                continue
        if search and search.lower() not in (case.get("query") or "").lower():
            continue
        if phase == "validation_baseline" or phase == "validation_candidate":
            baseline = phases_map.get("validation_baseline")
            candidate = phases_map.get("validation_candidate")
        elif phase in (None, "", "all"):
            baseline = phases_map.get("baseline") or _first_repeat(
                phases_map.get("validation_baseline"))
            candidate = phases_map.get("candidate") or _first_repeat(
                phases_map.get("validation_candidate"))
            if baseline is None and candidate is None:
                continue
        else:
            baseline = phases_map.get(phases[0]) if len(phases) > 0 else None
            candidate = phases_map.get(phases[1]) if len(phases) > 1 else None
        change = _change_type(baseline, candidate)
        if changed and change != changed:
            continue
        item = {
            "case_id": case_id,
            "query": case.get("query"),
            "split": case.get("split"),
            "group_id": case.get("group_id"),
            "tags": case.get("tags") or [],
            "phase": phase or "all",
            "repeat_index": 1,
            "change_type": change,
            "baseline": _side(baseline),
            "candidate": _side(candidate),
            "diagnostics": _diagnostics(baseline, candidate),
        }
        items.append(item)
    items.sort(key=lambda item: (
        {"regressed": 0, "unchanged_fail": 1, "pending": 2,
         "unchanged_pass": 3, "fixed": 4}.get(item["change_type"], 5),
        item["case_id"]))
    total = len(items)
    return {"items": items[offset:offset + limit], "total": total,
            "limit": limit, "offset": offset}


def _first_repeat(row: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    return row if row and (row.get("repeat_index") or 1) == 1 else None


def _change_type(baseline: Mapping[str, Any] | None,
                 candidate: Mapping[str, Any] | None) -> str:
    if baseline is None or candidate is None:
        return "pending"
    before = bool((baseline.get("scores") or {}).get("passed"))
    after = bool((candidate.get("scores") or {}).get("passed"))
    if not before and after:
        return "fixed"
    if before and not after:
        return "regressed"
    return "unchanged_pass" if before else "unchanged_fail"


def _side(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    scores = row.get("scores") or {}
    return {
        "selected_skills": row.get("selected_skills") or [],
        "action": row.get("action"),
        "tool_calls": row.get("tool_calls") or [],
        "missing_fields": row.get("missing_fields") or [],
        "passed": scores.get("passed"),
        "status": scores.get("status"),
        "finish_protocol": scores.get("finish_protocol") or (row.get("actual") or {}).get("finish_protocol"),
        "error_code": row.get("error_code") or scores.get("error_code"),
        "reason": (scores.get("joint") or {}).get("reason") if isinstance(
            scores.get("joint"), dict) else scores.get("reason"),
        "error_message": row.get("error_message"),
        "duration_ms": row.get("duration_ms"),
        "usage": row.get("usage") or {},
        "routing": scores.get("routing"),
        "tool_args": scores.get("tool_args"),
        "action_ok": scores.get("action_ok"),
        "diagnostics": scores.get("diagnostics") or [],
        "judgments": scores.get("judgments") or [],
    }


def _diagnostics(baseline: Mapping[str, Any] | None,
                 candidate: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    merged: list[dict[str, Any]] = []
    for row in (candidate, baseline):
        if not row:
            continue
        for item in (row.get("scores") or {}).get("diagnostics") or []:
            key = (item.get("category"), item.get("field_path"), item.get("tool"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def run_model_output(run_id: str, seq: int) -> dict[str, Any]:
    """Return captured output, or honestly labelled saved case data for old events."""
    from .model_output import capture_model_response, redact_output

    run = persistence.get_run(run_id)
    event = persistence.get_event(run_id, seq)
    if event is None:
        raise NotFoundError("运行记录不存在", code="not_found")
    if event["type"] not in ("model_call_finished", "optimizer_call_finished"):
        raise ValidationError("该记录不是模型调用完成记录", field_path="seq")
    try:
        env = persistence.get_environment(run["environment_id"])
    except NotFoundError:
        env = {}
    credentials = {"model_config": env.get("model_config") or {},
                   "optimizer_config": env.get("optimizer_config") or {}}
    payload = event.get("payload") or {}
    result = {"source": "unavailable", "scope": "model_call", "response": None,
              "case_results": [], "message": "此历史调用未保存逐次模型输出，无法恢复原始回复。"}
    response = payload.get("response")
    if not isinstance(response, Mapping):
        for ref in payload.get("artifacts") or []:
            if ref.get("kind") != "model_response":
                continue
            artifact = persistence.get_artifact(run_id, ref["id"])
            try:
                loaded = json.loads(artifact["content_path"].read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise NotFoundError("已保存的模型输出文件不可读取", code="output_unavailable") from exc
            if isinstance(loaded, Mapping):
                # Accept earlier trace writers that wrapped the projection.
                wrapped = loaded.get("response")
                response = wrapped if isinstance(wrapped, Mapping) else loaded
                break
    if isinstance(response, Mapping):
        result.update(source="captured", response=capture_model_response(response, credentials),
                      message=payload.get("response_note") or "本次调用已保存的模型输出（已脱敏）。")
        return result
    if event["type"] == "optimizer_call_finished":
        preview = payload.get("output_preview") or payload.get("raw_preview")
        if preview:
            result.update(source="captured", response={"content": redact_output(preview, credentials),
                                                       "tool_calls": []},
                          message="旧记录仅保存优化器输出摘要，可能已截断，无法恢复完整回复。")
        return result
    if not event.get("case_id"):
        return result
    rows = persistence.list_case_results(run_id, case_id=event["case_id"], limit=1000)["items"]
    if event.get("stage") == "baseline_eval" or run["mode"] == "baseline":
        rows = [row for row in rows if row["phase"] == "baseline"]
    elif run["mode"] == "final_test":
        rows = [row for row in rows if row["phase"] == "final"]
    saved = []
    for row in rows:
        actual = row.get("actual") or {}
        saved.append({"phase": row["phase"], "repeat_index": row.get("repeat_index"),
                      "variant": row.get("variant"),
                      "selected_skills": row.get("selected_skills") or [],
                      "action": row.get("action"),
                      "tool_calls": row.get("tool_calls") or [],
                      "raw_output": actual.get("raw_output") or "",
                      "missing_fields": row.get("missing_fields") or [],
                      "usage": row.get("usage"),
                      "finish_reason": actual.get("finish_reason")})
    if saved:
        result.update(source="legacy_case_result", scope="case_result",
                      case_results=redact_output(saved, credentials),
                      message="此历史调用没有保存逐次原始回复；以下为该案例已保存的执行结果，包含业务工具及参数，不代表本次单独模型回复。")
    return result


def run_case_detail(run_id: str, case_id: str) -> dict[str, Any]:
    run = persistence.get_run(run_id)
    case = persistence.get_case(run["dataset_id"], case_id)
    results = [row for row in persistence.get_case_results_for_run(run_id)
               if row["case_id"] == case_id]
    events = persistence.list_events(run_id, after_seq=0, limit=500)
    case_events = [event for event in events if event.get("case_id") == case_id]
    return {
        "run_id": run_id,
        "case": case,
        "results": results,
        "events": case_events,
        "baseline": _side(next((row for row in results if row["phase"] == "baseline"), None)),
        "candidate": _side(next((row for row in results if row["phase"] == "candidate"), None)),
    }


def run_diff(run_id: str) -> dict[str, Any]:
    run = persistence.get_run(run_id)
    candidate = persistence.get_candidate_for_run(run_id)
    changes: list[dict[str, Any]] = []
    if candidate and candidate.get("skill_version_id"):
        changes = persistence.diff_skill_versions(run["baseline_skill_version_id"],
                                                  candidate["skill_version_id"])
    related: list[str] = []
    for change in changes:
        for event in persistence.list_events(run_id, after_seq=0, limit=500,
                                             errors_only=True):
            payload = event.get("payload") or {}
            if change["path"] in (payload.get("changed_files") or []):
                if event.get("case_id") and event["case_id"] not in related:
                    related.append(event["case_id"])
    diagnosis = (candidate or {}).get("diagnosis") or {}
    if isinstance(diagnosis, dict):
        related.extend([cid for cid in diagnosis.get("case_ids") or []
                        if cid not in related])
    return {
        "items": changes,
        "changes": changes,
        "related_cases": related,
        "diagnosis": diagnosis,
        "hypotheses": (candidate or {}).get("hypotheses") or [],
        "expected_effect": (candidate or {}).get("expected_effect") or "",
        "patch": (candidate or {}).get("patch") or {},
        "checks": (candidate or {}).get("checks") or {},
    }


def apply_candidate(run_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    run = persistence.get_run(run_id)
    if run["status"] != "completed" or run.get("decision") != "accept":
        raise ConflictError("只有评测达标（accept）的候选可以应用", code="not_applicable",
                            details={"decision": run.get("decision")})
    expected = payload.get("expected_current_version")
    if expected is None:
        raise ValidationError("缺少 expected_current_version", field_path="expected_current_version")
    project = persistence.get_project(run["project_id"])
    current = project.get("current_skill_version_id")
    if current != expected:
        raise ConflictError("项目当前版本与请求不一致，请重新比较", code="version_conflict",
                            details={"current_version": current,
                                     "expected_current_version": expected})
    if current != run["baseline_skill_version_id"]:
        raise ConflictError("项目当前版本已不是本轮基线版本，请重新比较", code="version_conflict",
                            details={"current_version": current,
                                     "baseline_version": run["baseline_skill_version_id"]})
    if project.get("current_environment_id") != run.get("environment_id"):
        raise ConflictError("项目当前环境已变更，请重新评测", code="version_conflict")
    if project.get("current_dataset_id") != run.get("dataset_id"):
        raise ConflictError("项目当前数据集已变更，请重新评测", code="version_conflict")
    candidate = persistence.get_candidate_for_run(run_id)
    if not candidate or not candidate.get("skill_version_id"):
        raise ConflictError("本轮没有可应用的候选", code="no_candidate")
    version_id = candidate["skill_version_id"]
    version = persistence.get_skill_version(version_id)
    if version["project_id"] != run["project_id"]:
        raise ConflictError("候选版本不属于当前项目", code="no_candidate")
    integrity = persistence.skill_version_integrity(version_id)
    if not integrity.get("ok"):
        raise ConflictError("候选文件已变化，请重新评测", code="candidate_changed")
    errors, _warnings = skill_package.validate_manifest(
        version["manifest"], _read_version_files(version_id))
    if errors:
        raise ValidationError("Skill 包校验失败", code="skill_package_invalid",
                              details=errors)
    decision = persistence.get_decision_for_run(run_id)
    if not decision or decision.get("applied_at"):
        raise ConflictError("候选已经应用或缺少决策记录", code="version_conflict")
    persistence.apply_candidate_cas(
        run_id, run["project_id"], version_id, expected,
        run.get("environment_id"), run.get("dataset_id"), decision["id"])
    persistence.append_audit(run["project_id"], "candidate.apply", {
        "run_id": run_id, "skill_version_id": version_id,
        "previous": current, "expected_current_version": expected,
    })
    _emit(run_id, "candidate_applied", {
        "skill_version_id": version_id, "previous_version_id": current,
        "candidate_id": candidate["id"],
    }, stage="decision")
    return {
        "run_id": run_id,
        "decision": "accept",
        "project": persistence.get_project(run["project_id"]),
        "applied_candidate": {"id": candidate["id"], "skill_version_id": version_id},
        "applied_at": persistence.now_iso(),
    }


def _emit(run_id: str, event_type: str, payload: dict[str, Any], **fields: Any) -> None:
    try:
        persistence.append_event(run_id, event_type, payload=payload, **fields)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# reports
# --------------------------------------------------------------------------- #
def build_report(run_id: str) -> dict[str, Any]:
    from . import reports

    return reports.build_report(run_id)
