"""Versioned HTTP API (``/api/v3``) implemented with Starlette."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from pathlib import Path
from typing import Any, Mapping

from starlette.requests import Request
from starlette.responses import (FileResponse, JSONResponse, PlainTextResponse,
                                 Response, StreamingResponse)
from starlette.routing import Route

from . import config, event_types, persistence, services, skill_package
from .errors import AppError, ConflictError, NotFoundError, ValidationError

API_VERSION = "1.0.0"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def ok(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code)


async def json_body(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"请求体不是合法 JSON: {exc}", code="invalid_json")
    if not isinstance(data, dict):
        raise ValidationError("请求体必须是 JSON 对象", code="invalid_json")
    return data


def _limit(request: Request, default: int = 50, cap: int = 200) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get("limit", default))
    except (TypeError, ValueError):
        limit = default
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, cap)), max(0, offset)


def _bool(value: Any) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
async def healthz(request: Request) -> Response:
    from . import worker

    return ok({"status": "ok", "version": API_VERSION,
               "time": persistence.now_iso(),
               "worker_started": bool(getattr(worker.get_worker(), "_thread", None))})


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
def _current_skill_summary(project: Mapping[str, Any]) -> dict[str, Any] | None:
    version_id = project.get("current_skill_version_id")
    if not version_id:
        return None
    try:
        version = persistence.get_skill_version(version_id)
    except NotFoundError:
        return None
    return {"id": version["id"], "status": version.get("status"),
            "content_hash": version.get("content_hash"),
            "subskill_count": len((version.get("manifest") or {}).get("subskills") or []),
            "created_at": version.get("created_at"),
            "parent_id": version.get("parent_id")}


def _current_env_summary(project: Mapping[str, Any]) -> dict[str, Any] | None:
    environment_id = project.get("current_environment_id")
    if not environment_id:
        return None
    try:
        env = persistence.get_environment(environment_id)
    except NotFoundError:
        return None
    return {"id": env["id"], "version_seq": env.get("version_seq"),
            "status": env.get("status"), "hash": env.get("hash"),
            "provider": (env.get("model_config") or {}).get("provider"),
            "model": (env.get("model_config") or {}).get("model"),
            "published_at": env.get("published_at")}


def _current_dataset_summary(project: Mapping[str, Any]) -> dict[str, Any] | None:
    dataset_id = project.get("current_dataset_id")
    if not dataset_id:
        return None
    try:
        dataset = persistence.get_dataset(dataset_id)
    except NotFoundError:
        return None
    return {"id": dataset["id"], "version_seq": dataset.get("version_seq"),
            "status": dataset.get("status"), "case_count": dataset.get("case_count"),
            "hash": dataset.get("hash"), "published_at": dataset.get("published_at")}


def project_detail(project_id: str) -> dict[str, Any]:
    project = persistence.get_project(project_id)
    project["current_skill_version"] = _current_skill_summary(project)
    project["current_environment"] = _current_env_summary(project)
    project["current_dataset"] = _current_dataset_summary(project)
    overview = services.overview(project_id)
    project["completeness"] = overview["completeness"]
    project["baseline"] = overview["baseline"]
    project["primary_action"] = overview["primary_action"]
    return project


async def list_projects(request: Request) -> Response:
    include_archived = _bool(request.query_params.get("include_archived", "0"))
    projects = persistence.list_projects(include_archived=include_archived)
    items = []
    for project in projects:
        item = project_detail(project["id"])
        runs = persistence.list_runs(project_id=project["id"], limit=1)
        latest = runs["items"][0] if runs["items"] else None
        item["recent_run"] = latest
        item["latest_run"] = latest
        item["run_count"] = persistence.list_runs(project_id=project["id"])["total"]
        items.append(item)
    return ok({"items": items, "total": len(items)})


async def create_project(request: Request) -> Response:
    body = await json_body(request)
    project = persistence.create_project(body.get("name", ""), body.get("description", ""))
    persistence.append_audit(project["id"], "project.create", {"name": project["name"]})
    return ok(project_detail(project["id"]), status_code=201)


async def get_project(request: Request) -> Response:
    return ok(project_detail(request.path_params["project_id"]))


async def patch_project(request: Request) -> Response:
    body = await json_body(request)
    project = persistence.update_project(
        request.path_params["project_id"],
        name=body.get("name"), description=body.get("description"),
        archived=1 if body.get("archived") is True else (0 if body.get("archived") is False else None))
    return ok(project_detail(project["id"]))


async def project_overview(request: Request) -> Response:
    return ok(services.overview(request.path_params["project_id"]))


async def project_versions(request: Request) -> Response:
    project_id = request.path_params["project_id"]
    persistence.get_project(project_id)
    skills = persistence.list_skill_versions(project_id)
    for version in skills:
        version["subskill_count"] = len(
            (version.get("manifest") or {}).get("subskills") or [])
    environments = persistence.list_environments(project_id)
    for env in environments:
        env.pop("model_config", None)
        env.pop("optimizer_config", None)
        env.pop("tool_schema", None)
        env.pop("prompt", None)
    datasets = persistence.list_datasets(project_id)
    return ok({
        "skill_versions": skills,
        "environments": environments,
        "datasets": datasets,
        "audit": persistence.list_audit(project_id),
    })


async def project_export_summary(request: Request) -> Response:
    project_id = request.path_params["project_id"]
    project = project_detail(project_id)
    versions = persistence.list_skill_versions(project_id)
    return ok({"project": project, "skill_versions": len(versions),
               "notice": "导出 ZIP 只包含 Skill 文件与 manifest，不写入密钥、验证答案或执行器私有配置。"})


# --------------------------------------------------------------------------- #
# skill versions
# --------------------------------------------------------------------------- #
async def create_skill_version(request: Request) -> Response:
    project_id = request.path_params["project_id"]
    persistence.get_project(project_id)
    body: dict[str, Any] = {}
    zip_bytes = None
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise ValidationError("multipart 上传缺少 file 字段", field_path="file")
        zip_bytes = await upload.read() if hasattr(upload, "read") else bytes(upload)
        body = {
            "status": form.get("status") or "published",
            "message": form.get("message") or "",
            "parent_id": form.get("parent_id") or None,
            "set_current": str(form.get("set_current", "true")).lower()
            not in ("false", "0", "no"),
        }
        if not zip_bytes:
            raise ValidationError("上传的 Skill 包为空")
        version = services.create_skill_version(
            project_id, zip_bytes=zip_bytes, files_payload=None,
            status=body["status"], message=body["message"],
            parent_id=body["parent_id"], set_current=body["set_current"])
        detail = services.skill_version_detail(version["id"])
        detail["warnings"] = version.get("warnings") or []
        return ok(detail, status_code=201)
    body_bytes = await request.body()
    if "application/json" in content_type:
        body = await json_body(request)
        files_payload = body.get("files")
        if not files_payload:
            raise ValidationError("JSON 导入需要 files 数组", field_path="files")
        zip_bytes = None
    elif body_bytes:
        zip_bytes = body_bytes
        files_payload = None
        if "application/zip" not in content_type and body_bytes[:2] != b"PK":
            raise ValidationError("上传内容不是 ZIP 包", code="invalid_zip")
    else:
        raise ValidationError("缺少 Skill 包内容")
    version = services.create_skill_version(
        project_id, zip_bytes=zip_bytes, files_payload=files_payload,
        status=body.get("status") or "published",
        message=body.get("message") or "",
        parent_id=body.get("parent_id"),
        set_current=body.get("set_current"))
    detail = services.skill_version_detail(version["id"])
    detail["warnings"] = version.get("warnings") or []
    return ok(detail, status_code=201)


async def list_skill_versions(request: Request) -> Response:
    project_id = request.path_params["project_id"]
    persistence.get_project(project_id)
    items = []
    for version in persistence.list_skill_versions(project_id):
        version["subskill_count"] = len(
            (version.get("manifest") or {}).get("subskills") or [])
        items.append(version)
    return ok({"items": items, "total": len(items)})


async def get_skill_version(request: Request) -> Response:
    return ok(services.skill_version_detail(request.path_params["skill_version_id"]))


async def get_skill_file(request: Request) -> Response:
    version_id = request.path_params["skill_version_id"]
    relpath = request.path_params["path"]
    return ok(services.skill_file_content(version_id, relpath))


async def put_skill_file(request: Request) -> Response:
    body = await json_body(request)
    content = body.get("content")
    if not isinstance(content, str):
        raise ValidationError("content 必须是字符串", field_path="content")
    version = services.save_draft_file(request.path_params["skill_version_id"],
                                       request.path_params["path"], content)
    return ok(services.skill_version_detail(version["id"]))


async def publish_skill_version(request: Request) -> Response:
    body = await json_body(request)
    published = services.publish_skill_version(
        request.path_params["skill_version_id"],
        set_current=body.get("set_current", True))
    return ok(services.skill_version_detail(published["id"]))


async def activate_skill_version(request: Request) -> Response:
    body = await json_body(request)
    version = services.activate_skill_version(
        request.path_params["skill_version_id"],
        expected_current_version=body.get("expected_current_version"))
    return ok(services.skill_version_detail(version["id"]))


async def export_skill_version(request: Request) -> Response:
    version_id = request.path_params["skill_version_id"]
    persistence.get_skill_version(version_id)
    path = persistence.export_skill_zip(version_id)
    return FileResponse(path, media_type="application/zip",
                        filename=f"skill-{version_id}.zip")


async def diff_skill_versions(request: Request) -> Response:
    version_id = request.path_params["skill_version_id"]
    from_id = request.query_params.get("from")
    if not from_id:
        version = persistence.get_skill_version(version_id)
        from_id = version.get("parent_id")
    if not from_id:
        return ok({"items": [], "changes": []})
    changes = persistence.diff_skill_versions(from_id, version_id)
    return ok({"items": changes, "changes": changes,
               "from": from_id, "to": version_id})


# --------------------------------------------------------------------------- #
# environments
# --------------------------------------------------------------------------- #
async def list_environments(request: Request) -> Response:
    project_id = request.path_params["project_id"]
    persistence.get_project(project_id)
    items = [services.public_environment(env)
             for env in persistence.list_environments(project_id)]
    return ok({"items": items, "total": len(items)})


async def create_environment(request: Request) -> Response:
    body = await json_body(request)
    env = services.create_environment(
        request.path_params["project_id"], body,
        status=body.get("status") or "published",
        parent_id=body.get("parent_id"),
        set_current=body.get("set_current"))
    return ok(services.public_environment(env), status_code=201)


async def get_environment(request: Request) -> Response:
    env = persistence.get_environment(request.path_params["environment_id"])
    return ok(services.public_environment(env))


async def publish_environment(request: Request) -> Response:
    body = await json_body(request)
    env = services.publish_environment(request.path_params["environment_id"],
                                       set_current=body.get("set_current", True))
    return ok(services.public_environment(env))


async def activate_environment(request: Request) -> Response:
    body = await json_body(request)
    env = services.activate_environment(
        request.path_params["environment_id"],
        expected_current_version=body.get("expected_current_version"))
    return ok(services.public_environment(env))


async def test_connection(request: Request) -> Response:
    body = await json_body(request)
    from starlette.concurrency import run_in_threadpool
    return ok(await run_in_threadpool(services.probe_connection, body))


async def environment_template(request: Request) -> Response:
    return ok(services.default_environment_template())


# --------------------------------------------------------------------------- #
# datasets
# --------------------------------------------------------------------------- #
async def list_datasets(request: Request) -> Response:
    project_id = request.path_params["project_id"]
    persistence.get_project(project_id)
    items = persistence.list_datasets(project_id)
    return ok({"items": items, "total": len(items)})


async def preflight_dataset(request: Request) -> Response:
    body = await json_body(request)
    project_id = body.get("project_id")
    if not project_id:
        project_id = request.query_params.get("project_id")
    report = services.preflight_dataset(project_id, body)
    return ok(report)


async def create_dataset(request: Request) -> Response:
    body = await json_body(request)
    dataset = services.create_dataset(request.path_params["project_id"], body)
    return ok(services.dataset_detail(dataset["id"]), status_code=201)


async def get_dataset(request: Request) -> Response:
    return ok(services.dataset_detail(request.path_params["dataset_id"]))


async def dataset_cases(request: Request) -> Response:
    dataset_id = request.path_params["dataset_id"]
    limit, offset = _limit(request, default=50)
    result = persistence.list_cases(
        dataset_id, split=request.query_params.get("split") or None,
        search=request.query_params.get("search") or None,
        tag=request.query_params.get("tag") or None,
        limit=limit, offset=offset)
    items = []
    for case in result["items"]:
        expected = case.get("expected") or {}
        items.append({
            "case_id": case["case_id"], "query": case["query"],
            "split": case["split"], "group_id": case.get("group_id"),
            "tags": case.get("tags") or [],
            "expected_action": expected.get("action"),
            "expected_routes": expected.get("routes") or [],
            "expected_tools": [call.get("name") for call in expected.get("tool_calls") or []],
            "status": "valid" if not (case.get("validation") or {}).get("errors") else "invalid",
        })
    result["items"] = items
    return ok(result)


async def dataset_case(request: Request) -> Response:
    return ok(persistence.get_case(request.path_params["dataset_id"],
                                   request.path_params["case_id"]))


async def dataset_coverage(request: Request) -> Response:
    return ok(services.dataset_detail(request.path_params["dataset_id"])["coverage"])


async def publish_dataset(request: Request) -> Response:
    body = await json_body(request)
    dataset = persistence.publish_dataset(request.path_params["dataset_id"],
                                          set_current=body.get("set_current", True))
    return ok(services.dataset_detail(dataset["id"]))


async def activate_dataset(request: Request) -> Response:
    body = await json_body(request)
    dataset = services.activate_dataset(request.path_params["dataset_id"],
                                        body.get("expected_current_version"))
    return ok(services.dataset_detail(dataset["id"]))


async def export_dataset(request: Request) -> Response:
    dataset_id = request.path_params["dataset_id"]
    data = services.export_dataset(dataset_id)
    return Response(data, media_type="application/x-ndjson",
                    headers={"Content-Disposition":
                             f'attachment; filename="dataset-{dataset_id}.jsonl"'})


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #
async def create_run(request: Request) -> Response:
    body = await json_body(request)
    key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
    if key:
        body["idempotency_key"] = key
        project_id = body.get("project_id")
        if project_id:
            existing = persistence.find_idempotent_run(project_id, key)
            if existing:
                return ok(services.run_view(existing["id"]), status_code=202)
    run = services.create_run(body)
    try:
        from .worker import get_worker
        get_worker()  # ensure the in-process worker exists
    except Exception:
        pass
    return ok(services.run_view(run["id"]), status_code=202)


async def list_runs(request: Request) -> Response:
    limit, offset = _limit(request, default=50)
    result = persistence.list_runs(
        project_id=request.query_params.get("project_id") or None,
        status=request.query_params.get("status") or None,
        limit=limit, offset=offset)
    items = []
    for run in result["items"]:
        items.append(_run_list_item(run))
    result["items"] = items
    return ok(result)


def _run_list_item(run: Mapping[str, Any]) -> dict[str, Any]:
    metrics = run.get("metrics") or {}
    return {
        "id": run["id"], "project_id": run["project_id"], "mode": run["mode"],
        "status": run["status"], "stage": run["stage"],
        "decision": run.get("decision"), "decision_reason": run.get("decision_reason"),
        "created_at": run.get("created_at"), "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "baseline_cache_hit": bool(run.get("baseline_cache_hit")),
        "baseline_skill_version_id": run.get("baseline_skill_version_id"),
        "candidate_skill_version_id": run.get("candidate_skill_version_id"),
        "metrics": {
            "baseline": services._metric_block(metrics.get("baseline")
                                              or metrics.get("baseline_optimize")),
            "candidate": services._metric_block(metrics.get("candidate")
                                                or metrics.get("candidate_optimize")),
            "delta": metrics.get("delta"),
        },
        "cost": run.get("cost") or {},
    }


async def get_run(request: Request) -> Response:
    return ok(services.run_view(request.path_params["run_id"]))


async def cancel_run(request: Request) -> Response:
    run = persistence.request_cancel(request.path_params["run_id"])
    return ok(services.run_view(run["id"]))


async def apply_run(request: Request) -> Response:
    body = await json_body(request)
    return ok(services.apply_candidate(request.path_params["run_id"], body))


async def run_cases(request: Request) -> Response:
    limit, offset = _limit(request, default=200, cap=500)
    result = services.run_cases(
        request.path_params["run_id"],
        phase=request.query_params.get("phase") or None,
        only_failed=_bool(request.query_params.get("only_failed", "0")),
        search=request.query_params.get("search") or None,
        changed=request.query_params.get("changed") or None,
        limit=limit, offset=offset)
    return ok(result)


async def run_model_output(request: Request) -> Response:
    try:
        seq = int(request.path_params["seq"])
    except (TypeError, ValueError):
        raise ValidationError("记录序号必须为整数", field_path="seq")
    return ok(services.run_model_output(request.path_params["run_id"], seq))


async def run_case_detail(request: Request) -> Response:
    return ok(services.run_case_detail(request.path_params["run_id"],
                                       request.path_params["case_id"]))


async def run_diff(request: Request) -> Response:
    return ok(services.run_diff(request.path_params["run_id"]))


async def run_issues(request: Request) -> Response:
    data = services.run_view(request.path_params["run_id"])
    return ok(data["issues_summary"])


async def run_event_history(request: Request) -> Response:
    run_id = request.path_params["run_id"]
    persistence.get_run(run_id)
    limit, offset = _limit(request, default=200, cap=500)
    after_seq = int(request.query_params.get("after_seq") or 0)
    cases = request.query_params.getlist("case_id") if hasattr(request.query_params, "getlist") else []
    if not cases:
        single = request.query_params.get("case_id")
        cases = [single] if single else None
    events = persistence.list_events(
        run_id, after_seq=after_seq, limit=1000,
        level=request.query_params.get("level") or None,
        event_type=request.query_params.get("type") or None,
        stage=request.query_params.get("stage") or None,
        case_ids=cases,
        errors_only=_bool(request.query_params.get("errors_only", "0")))
    total = len(events)
    return ok({"items": events[offset:offset + limit], "total": total,
               "limit": limit, "offset": offset, "after_seq": after_seq})


async def run_events_sse(request: Request) -> Response:
    run_id = request.path_params["run_id"]
    persistence.get_run(run_id)
    last_event_id = request.headers.get("last-event-id")
    try:
        cursor = int(last_event_id or request.query_params.get("after_seq") or 0)
    except (TypeError, ValueError):
        cursor = 0

    async def stream():
        nonlocal cursor
        last_ping = time.time()
        while True:
            events = persistence.list_events(run_id, after_seq=cursor, limit=200)
            for event in events:
                cursor = event["seq"]
                data = json.dumps(event, ensure_ascii=False)
                yield f"id: {cursor}\ndata: {data}\n\n"
                last_ping = time.time()
            if await request.is_disconnected():
                break
            if time.time() - last_ping > 15:
                yield ": keep-alive\n\n"
                last_ping = time.time()
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


async def run_trace_artifact(request: Request) -> Response:
    run_id = request.path_params["run_id"]
    artifact = persistence.get_artifact(run_id, request.path_params["artifact_id"])
    raw = artifact["content_path"].read_bytes()
    content: Any
    try:
        content = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        content = raw.decode("utf-8", errors="replace")
    artifact.pop("content_path", None)
    artifact.pop("path", None)
    return ok({"artifact": artifact, "content": content})


async def run_report_json(request: Request) -> Response:
    run_id = request.path_params["run_id"]
    report = services.build_report(run_id)
    return Response(json.dumps(report, ensure_ascii=False, indent=2),
                    media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="run-{run_id}-report.json"'})


async def run_report_csv(request: Request) -> Response:
    run_id = request.path_params["run_id"]
    report = services.build_report(run_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["case_id", "split", "query", "baseline_passed", "candidate_passed",
                     "change_type", "baseline_reason", "candidate_reason"])
    for row in report.get("cases") or []:
        writer.writerow([row.get("case_id"), row.get("split"), row.get("query"),
                         row.get("baseline_passed"), row.get("candidate_passed"),
                         row.get("change_type"), row.get("baseline_reason") or "",
                         row.get("candidate_reason") or ""])
    data = ("\ufeff" + output.getvalue()).encode("utf-8")
    return Response(data, media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="run-{run_id}-report.csv"'})


async def debug_case(request: Request) -> Response:
    body = await json_body(request)
    project_id = request.path_params.get("project_id") or body.get("project_id")
    if not project_id:
        raise ValidationError("缺少 project_id", field_path="project_id")
    project = persistence.get_project(project_id)
    skill_version_id = body.get("skill_version_id") or project.get("current_skill_version_id")
    environment_id = body.get("environment_id") or project.get("current_environment_id")
    dataset_id = body.get("dataset_id") or project.get("current_dataset_id")
    case_id = body.get("case_id")
    if not (skill_version_id and environment_id and dataset_id and case_id):
        raise ValidationError("调试需要已发布的 Skill、环境、数据集与案例")
    env = persistence.get_environment(environment_id)
    case = persistence.get_case(dataset_id, case_id)
    from . import execution
    from .adapters.registry import build_adapter

    adapter = build_adapter(env.get("model_config") or {})
    request_payload = {
        "query": case["query"],
        "context": case.get("context") or {},
        "skill_snapshot_path": str(persistence.skill_files_dir(skill_version_id)),
        "system_prompt": env["prompt"],
        "model_config": env.get("model_config") or {},
        "request_template": (env.get("model_config") or {}).get("request_template"),
        "tool_schema": env.get("tool_schema") or [],
        "fixtures": case.get("fixtures") or [],
        "limits": {
            **(env.get("loader_config") or {}),
            "max_model_calls": (env.get("run_config") or {}).get("max_model_calls", 12),
            "case_timeout_s": (env.get("run_config") or {}).get("case_timeout_s", 120),
        },
        "run_id": "debug",
        "case_id": case_id,
        "manifest": persistence.get_skill_version(skill_version_id).get("manifest") or {},
        "allowed_skill_ids": [item["skill_id"] for item in
                              (persistence.get_skill_version(skill_version_id)
                               .get("manifest") or {}).get("subskills") or []],
    }
    events: list[dict[str, Any]] = []

    def emit(event_type: str, payload: Any = None, **fields: Any) -> None:
        events.append({"type": event_type, "payload": payload or {}, **fields})

    result = execution.run_case(request_payload, adapter, emit)
    actual = result
    hide_expected = bool(body.get("hide_expected"))
    return ok({
        "case_id": case_id,
        "query": case["query"],
        "expected": None if hide_expected else case.get("expected"),
        "actual": actual,
        "events": events,
        "debug": True,
        "note": "调试记录与正式评测分开，不写入正式运行成绩。",
    })


# --------------------------------------------------------------------------- #
# route table
# --------------------------------------------------------------------------- #
def build_routes() -> list[Route]:
    p = config.API_PREFIX
    return [
        Route(f"{p}/healthz", healthz, methods=["GET"]),
        Route(f"{p}/projects", list_projects, methods=["GET"]),
        Route(f"{p}/projects", create_project, methods=["POST"]),
        Route(f"{p}/projects/{{project_id}}", get_project, methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}", patch_project, methods=["PATCH", "PUT"]),
        Route(f"{p}/projects/{{project_id}}/overview", project_overview, methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/versions", project_versions, methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/export-summary", project_export_summary,
              methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/skill-versions", list_skill_versions,
              methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/skill-versions", create_skill_version,
              methods=["POST"]),
        Route(f"{p}/projects/{{project_id}}/skill-versions/{{skill_version_id}}/publish",
              publish_skill_version, methods=["POST"]),
        Route(f"{p}/projects/{{project_id}}/skill-versions/{{skill_version_id}}/activate",
              activate_skill_version, methods=["POST"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}", get_skill_version,
              methods=["GET"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}/files/{{path:path}}",
              get_skill_file, methods=["GET"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}/files/{{path:path}}",
              put_skill_file, methods=["PUT"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}/publish", publish_skill_version,
              methods=["POST"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}/activate", activate_skill_version,
              methods=["POST"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}/export", export_skill_version,
              methods=["GET"]),
        Route(f"{p}/skill-versions/{{skill_version_id}}/diff", diff_skill_versions,
              methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/environments", list_environments,
              methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/environments", create_environment,
              methods=["POST"]),
        Route(f"{p}/environments/{{environment_id}}", get_environment, methods=["GET"]),
        Route(f"{p}/environments/{{environment_id}}/publish", publish_environment,
              methods=["POST"]),
        Route(f"{p}/environments/{{environment_id}}/activate", activate_environment,
              methods=["POST"]),
        Route(f"{p}/environments/test-connection", test_connection, methods=["POST"]),
        Route(f"{p}/environment-template", environment_template, methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/datasets", list_datasets, methods=["GET"]),
        Route(f"{p}/projects/{{project_id}}/datasets", create_dataset, methods=["POST"]),
        Route(f"{p}/projects/{{project_id}}/debug-cases", debug_case, methods=["POST"]),
        Route(f"{p}/datasets/preflight", preflight_dataset, methods=["POST"]),
        Route(f"{p}/datasets/{{dataset_id}}", get_dataset, methods=["GET"]),
        Route(f"{p}/datasets/{{dataset_id}}/cases", dataset_cases, methods=["GET"]),
        Route(f"{p}/datasets/{{dataset_id}}/cases/{{case_id}}", dataset_case,
              methods=["GET"]),
        Route(f"{p}/datasets/{{dataset_id}}/coverage", dataset_coverage, methods=["GET"]),
        Route(f"{p}/datasets/{{dataset_id}}/publish", publish_dataset, methods=["POST"]),
        Route(f"{p}/datasets/{{dataset_id}}/activate", activate_dataset,
              methods=["POST"]),
        Route(f"{p}/datasets/{{dataset_id}}/export", export_dataset, methods=["GET"]),
        Route(f"{p}/runs", list_runs, methods=["GET"]),
        Route(f"{p}/runs", create_run, methods=["POST"]),
        Route(f"{p}/runs/{{run_id}}", get_run, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/cancel", cancel_run, methods=["POST"]),
        Route(f"{p}/runs/{{run_id}}/apply", apply_run, methods=["POST"]),
        Route(f"{p}/runs/{{run_id}}/cases", run_cases, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/cases/{{case_id}}", run_case_detail, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/diff", run_diff, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/issues", run_issues, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/events", run_events_sse, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/events/{{seq}}/model-output",
              run_model_output, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/event-history", run_event_history, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/trace-artifacts/{{artifact_id}}",
              run_trace_artifact, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/report.json", run_report_json, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/report.csv", run_report_csv, methods=["GET"]),
        Route(f"{p}/runs/{{run_id}}/debug-cases", debug_case, methods=["POST"]),
    ]
