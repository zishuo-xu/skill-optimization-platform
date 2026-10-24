"""生成示例环境的工具协议与示例数据集（可重复执行）。

运行：.venv/bin/python scripts/build_sample_assets.py
输出：
  app/samples/tool_schema.json
  app/samples/dataset.jsonl

示例数据故意保留了可被优化器修复的缺陷（详见 app/samples/README.md）：
  * 路由关键词缺失：进行中的任务 / 交给 / 改由 / 新建
  * 参数映射错误：进行中 -> active（应为 in_progress）、紧急 -> urgent（应为 high）
  * 缺少必填参数：task_assign 未提取 assignee_id
  * 缺少澄清条件：创建任务缺 title/project_id 时未返回 clarify
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "app" / "samples"


def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


STRING = {"type": "string"}
TOOL_SCHEMA = [
    tool("list_tasks", "查询任务列表或任务详情", {
        "project_id": STRING, "task_id": STRING,
        "status": {"type": "string", "enum": ["in_progress", "done", "todo"]},
        "assignee_id": STRING,
    }, []),
    tool("create_task", "在项目中创建任务", {
        "title": STRING, "project_id": STRING,
        "priority": {"type": "string", "enum": ["high", "normal", "low"]},
    }, ["title", "project_id"]),
    tool("update_task_status", "更新任务状态", {
        "task_id": STRING,
        "status": {"type": "string", "enum": ["in_progress", "done", "todo"]},
    }, ["task_id", "status"]),
    tool("assign_task", "把任务指派给负责人", {
        "task_id": STRING, "assignee_id": STRING,
    }, ["task_id", "assignee_id"]),
    tool("set_priority", "设置任务优先级", {
        "task_id": STRING,
        "priority": {"type": "string", "enum": ["high", "normal", "low"]},
    }, ["task_id", "priority"]),
    tool("get_project", "查询项目详情", {"project_id": STRING}, ["project_id"]),
    tool("list_members", "列出项目成员", {"project_id": STRING}, ["project_id"]),
    tool("add_comment", "在任务下添加评论", {
        "task_id": STRING, "comment": STRING,
    }, ["task_id", "comment"]),
    tool("add_attachment", "给任务上传附件", {
        "task_id": STRING, "filename": STRING,
    }, ["task_id", "filename"]),
    tool("log_work", "登记任务工时", {
        "task_id": STRING, "hours": {"type": "number"},
    }, ["task_id", "hours"]),
    tool("add_dependency", "设置任务依赖", {
        "task_id": STRING, "depends_on": STRING,
    }, ["task_id", "depends_on"]),
    tool("create_milestone", "为项目创建里程碑", {
        "project_id": STRING, "name": STRING, "due_date": STRING,
    }, ["project_id", "name"]),
    tool("add_tag", "给任务添加标签", {
        "task_id": STRING, "tag": STRING,
    }, ["task_id", "tag"]),
    tool("search_tasks", "按关键词搜索任务", {"keyword": STRING}, ["keyword"]),
    tool("batch_update_tasks", "批量更新任务", {
        "task_ids": {"type": "array", "items": STRING},
        "status": {"type": "string", "enum": ["in_progress", "done", "todo"]},
    }, ["task_ids", "status"]),
    tool("export_tasks", "导出项目任务", {
        "project_id": STRING,
        "format": {"type": "string", "enum": ["xlsx", "csv"]},
    }, ["project_id", "format"]),
    tool("query_stats", "统计项目任务", {"project_id": STRING}, ["project_id"]),
    tool("create_reminder", "创建任务提醒", {
        "task_id": STRING,
        "channel": {"type": "string", "enum": ["app", "email", "sms"]},
    }, ["task_id", "channel"]),
    tool("create_from_template", "从模板创建任务", {"template": STRING}, ["template"]),
    tool("archive_task", "归档任务", {"task_id": STRING}, ["task_id"]),
    tool("check_permission", "校验任务操作权限", {
        "task_id": STRING,
        "action": {"type": "string", "enum": ["assign", "update", "archive"]},
    }, ["task_id", "action"]),
]


def call(tool_name: str, **arguments) -> dict:
    return {"name": tool_name, "arguments": arguments}


def case(case_id: str, query: str, group_id: str, split: str, tags: list[str],
         expected_action: str = "tool_call", routes: list[list[str]] | None = None,
         calls: list[dict] | None = None, missing_fields: list[str] | None = None,
         critical: bool = False, with_fixtures: bool = True,
         route_ordered: bool = False, call_match: str = "ordered_exact",
         variants: list[dict] | None = None,
         fixture_calls: list[dict] | None = None) -> dict:
    """Emit the compact dataset shorthand consumed by the platform importer."""
    routes = routes or []
    calls = calls or []
    out = {
        "id": case_id,
        "query": query,
        "group_id": group_id,
        "split": split,
        "tags": tags,
        "context": {"timezone": "Asia/Shanghai"},
    }
    if expected_action == "tool_call":
        if routes:
            if len(routes) == 1:
                route = list(routes[0])
                out["skill"] = route[0] if len(route) == 1 else route
            else:
                out["routes"] = [list(route) for route in routes]
        if calls:
            normalised = [{"name": item["name"], "arguments": item["arguments"]}
                          for item in calls]
            if len(normalised) == 1:
                out["tool"] = normalised[0]
            else:
                out["tools"] = normalised
        if with_fixtures:
            # Generic mock covers all business tools, so wrong-route calls still
            # receive a simulated result and produce routing diagnostics.
            out["mock"] = {"ok": True}
        else:
            out["fixtures"] = []
    elif expected_action == "clarify":
        out["action"] = "clarify"
        out["missing"] = list(missing_fields or [])
        if routes:
            route = list(routes[0])
            out["skill"] = route[0] if len(route) == 1 else route
        if with_fixtures:
            out["mock"] = {"ok": True}
        else:
            out["fixtures"] = []
    elif expected_action == "no_action":
        out["action"] = "no_action"
    if route_ordered:
        out["route_ordered"] = True
    if call_match != "ordered_exact":
        out["call_match"] = call_match
    if variants:
        out["variants"] = variants
    if critical:
        out["critical"] = True
    return out


CASES = [
    # ---- 优化集 ---------------------------------------------------------
    case("opt_001", "查询项目 P123 中进行中的任务", "g_status_query", "optimize",
         ["进行中的任务", "状态映射"], routes=[["task_query"]],
         calls=[call("list_tasks", project_id="P123", status="in_progress")]),
    case("opt_002", "项目 P123 里有哪些进行中的任务", "g_status_query", "optimize",
         ["进行中的任务", "任务列表"], routes=[["task_query"]],
         calls=[call("list_tasks", project_id="P123", status="in_progress")]),
    case("opt_003", "查询任务 T42 的状态", "g_task_detail", "optimize", ["任务详情"],
         routes=[["task_query"]], calls=[call("list_tasks", task_id="T42")]),
    case("opt_004", "把任务 T42 交给 U7 处理", "g_assign", "optimize",
         ["交给", "负责人"], routes=[["task_assign"]],
         calls=[call("assign_task", task_id="T42", assignee_id="U7")]),
    case("opt_005", "任务 T43 改由 U9 负责", "g_assign", "optimize",
         ["改由", "负责人"], routes=[["task_assign"]],
         calls=[call("assign_task", task_id="T43", assignee_id="U9")]),
    case("opt_006", "指派任务 T42 给 U7", "g_assign_direct", "optimize",
         ["指派"], routes=[["task_assign"]],
         calls=[call("assign_task", task_id="T42", assignee_id="U7")]),
    case("opt_007", "指派任务 T43 给 U9", "g_assign_direct", "optimize",
         ["指派"], routes=[["task_assign"]],
         calls=[call("assign_task", task_id="T43", assignee_id="U9")]),
    case("opt_008", "创建任务：修复登录失败（项目 P200，紧急）", "g_create_prio",
         "optimize", ["任务创建"],
         routes=[["task_create"]],
         calls=[call("create_task", title="修复登录失败", project_id="P200",
                     priority="high")]),
    case("opt_009", "新建一个紧急任务：优化首页性能，项目 P201", "g_create_prio",
         "optimize", ["新建一个", "任务创建"], routes=[["task_create"]],
         calls=[call("create_task", title="优化首页性能", project_id="P201",
                     priority="high")]),
    case("opt_010", "帮我建任务", "g_clarify_create", "optimize", ["澄清"],
         expected_action="clarify", routes=[["task_create"]],
         missing_fields=["title", "project_id"],
         fixture_calls=[call("create_task", title="待补充", project_id="P000")]),
    case("opt_011", "再建任务", "g_clarify_create", "optimize", ["澄清"],
         expected_action="clarify", routes=[["task_create"]],
         missing_fields=["title", "project_id"],
         fixture_calls=[call("create_task", title="待补充", project_id="P000")]),
    case("opt_012", "把任务 T42 标记为已完成", "g_status_update", "optimize",
         ["状态更新"], routes=[["task_update_status"]],
         calls=[call("update_task_status", task_id="T42", status="done")],
         critical=True),
    case("opt_013", "设置任务 T42 的优先级为紧急", "g_priority", "optimize",
         ["优先级"], routes=[["task_priority"]],
         calls=[call("set_priority", task_id="T42", priority="high")]),
    case("opt_014", "设置任务 T43 的优先级为普通", "g_priority", "optimize",
         ["优先级"], routes=[["task_priority"]],
         calls=[call("set_priority", task_id="T43", priority="normal")]),
    case("opt_015", "查询项目 P123 的详情", "g_project_query", "optimize",
         ["项目查询"], routes=[["project_query"]],
         calls=[call("get_project", project_id="P123")]),
    case("opt_016", "列出项目 P123 的成员", "g_members", "optimize",
         ["项目成员"], routes=[["project_members"]],
         calls=[call("list_members", project_id="P123")]),
    case("opt_017", "统计项目 P123 的任务数量", "g_stats", "optimize",
         ["统计"], routes=[["task_stats"]],
         calls=[call("query_stats", project_id="P123")]),
    case("opt_018", "把项目 P123 的任务导出为 Excel", "g_export", "optimize",
         ["导出"], routes=[["task_export"]],
         calls=[call("export_tasks", project_id="P123", format="xlsx")]),
    case("opt_019", "搜索包含登录失败的任务", "g_search", "optimize",
         ["搜索"], routes=[["task_search"]],
         calls=[call("search_tasks", keyword="登录失败")]),
    case("opt_020", "在任务 T42 下评论：已定位问题", "g_comment", "optimize",
         ["评论"], routes=[["task_comment"]],
         calls=[call("add_comment", task_id="T42", comment="已定位问题")]),
    case("opt_021", "在任务 T42 登记 2 小时工时", "g_time_log", "optimize",
         ["工时"], routes=[["task_time_log"]],
         calls=[call("log_work", task_id="T42", hours=2)]),
    case("opt_022", "设置任务 T42 依赖任务 T43", "g_dependency", "optimize",
         ["依赖"], routes=[["task_dependency"]],
         calls=[call("add_dependency", task_id="T42", depends_on="T43")]),
    case("opt_023", "为项目 P123 创建里程碑 v1.0，截止 2026-11-30", "g_milestone",
         "optimize", ["里程碑"], routes=[["task_milestone"]],
         calls=[call("create_milestone", project_id="P123", name="v1.0",
                     due_date="2026-11-30")]),
    case("opt_024", "指派任务 T42 给 U7 并通知", "g_assign_multi", "optimize",
         ["指派"], routes=[["task_assign"], ["task_assign", "task_reminder"]],
         calls=[call("assign_task", task_id="T42", assignee_id="U7")]),
    case("opt_025", "今天天气怎么样", "g_no_action", "optimize", ["闲聊"],
         expected_action="no_action"),
    case("opt_026", "给任务 T42 上传附件 report.pdf", "g_attachment", "optimize",
         ["附件"], routes=[["task_attachment"]],
         calls=[call("add_attachment", task_id="T42", filename="report.pdf")]),
    case("opt_027", "给任务 T42 打标签 后端", "g_tag", "optimize", ["标签"],
         routes=[["task_tag"]], calls=[call("add_tag", task_id="T42", tag="后端")]),
    case("opt_028", "批量修改任务 T42、T43 的状态为已完成", "g_batch", "optimize",
         ["批量"], routes=[["task_batch"]],
         calls=[call("batch_update_tasks", task_ids=["T42", "T43"], status="done")]),
    case("opt_029", "提醒我处理任务 T42", "g_reminder", "optimize", ["提醒"],
         routes=[["task_reminder"]],
         calls=[call("create_reminder", task_id="T42", channel="app")]),
    case("opt_030", "使用模板 周一例会 建任务", "g_template", "optimize", ["模板"],
         routes=[["task_template"]],
         calls=[call("create_from_template", template="周一例会")]),
    case("opt_031", "归档任务 T42", "g_archive", "optimize", ["归档"],
         routes=[["task_archive"]], calls=[call("archive_task", task_id="T42")]),
    case("opt_032", "我能否把任务 T42 转给别人", "g_permission", "optimize",
         ["权限"], routes=[["permission_check"]],
         calls=[call("check_permission", task_id="T42", action="assign")]),
    case("opt_033", "查询任务 T42 的状态是否为进行中", "g_status_value", "optimize",
         ["状态映射"], routes=[["task_query"]],
         calls=[call("list_tasks", task_id="T42", status="in_progress")]),
    # ---- 验证集 ---------------------------------------------------------
    case("val_001", "查询项目 P300 中进行中的任务", "g_val_status_query",
         "validation", ["进行中的任务", "状态映射"], routes=[["task_query"]],
         calls=[call("list_tasks", project_id="P300", status="in_progress")]),
    case("val_002", "把任务 T50 交给 U8 处理", "g_val_assign", "validation",
         ["交给", "负责人"], routes=[["task_assign"]],
         calls=[call("assign_task", task_id="T50", assignee_id="U8")]),
    case("val_003", "项目 P301 里有哪些进行中的任务", "g_val_status_query",
         "validation", ["进行中的任务", "任务列表"], routes=[["task_query"]],
         calls=[call("list_tasks", project_id="P301", status="in_progress")]),
    case("val_004", "创建任务：修复支付失败（项目 P302，紧急）", "g_val_create_prio",
         "validation", ["任务创建"], routes=[["task_create"]],
         calls=[call("create_task", title="修复支付失败", project_id="P302",
                     priority="high")]),
    case("val_005", "请建任务", "g_val_clarify", "validation", ["澄清"],
         expected_action="clarify", routes=[["task_create"]],
         missing_fields=["title", "project_id"],
         fixture_calls=[call("create_task", title="待补充", project_id="P000")]),
    case("val_006", "把任务 T51 标记为已完成", "g_val_status_update",
         "validation", ["状态更新"], routes=[["task_update_status"]],
         calls=[call("update_task_status", task_id="T51", status="done")],
         critical=True),
    case("val_007", "列出项目 P302 的成员", "g_val_members", "validation",
         ["项目成员"], routes=[["project_members"]],
         calls=[call("list_members", project_id="P302")]),
    case("val_008", "查询任务 T52 的状态", "g_val_task_detail", "validation",
         ["任务详情"], routes=[["task_query"]],
         calls=[call("list_tasks", task_id="T52")]),
    # ---- 最终测试集（仅阶段验收时运行） ---------------------------------
    case("fin_001", "统计项目 P303 的任务数量", "g_fin_stats", "final_test",
         ["统计"], routes=[["task_stats"]],
         calls=[call("query_stats", project_id="P303")]),
    case("fin_002", "查询项目 P304 中进行中的任务", "g_fin_status_query",
         "final_test", ["进行中的任务", "状态映射"], routes=[["task_query"]],
         calls=[call("list_tasks", project_id="P304", status="in_progress")]),
    case("fin_003", "查询任务 T99 的详情", "g_fin_fixture_miss", "final_test",
         ["任务详情"], routes=[["task_query"]],
         calls=[call("list_tasks", task_id="T99")], with_fixtures=False),
    case("fin_004", "你好", "g_fin_no_action", "final_test", ["闲聊"],
         expected_action="no_action"),
]


def main() -> None:
    (SAMPLES / "tool_schema.json").write_text(
        json.dumps(TOOL_SCHEMA, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = []
    seen: set[str] = set()
    for item in CASES:
        assert item["id"] not in seen, item["id"]
        seen.add(item["id"])
        lines.append(json.dumps(item, ensure_ascii=False))
    (SAMPLES / "dataset.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    splits: dict[str, int] = {}
    for item in CASES:
        splits[item["split"]] = splits.get(item["split"], 0) + 1
    print("wrote", SAMPLES / "tool_schema.json")
    print("wrote", SAMPLES / "dataset.jsonl", "cases:", len(CASES), "splits:", splits)


if __name__ == "__main__":
    main()
