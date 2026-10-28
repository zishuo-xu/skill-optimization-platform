"""A06 验收：单轮优化器、补丁应用与静态检查。

测试全部使用内联的假 adapter / 假 skill 包，不依赖 app/execution.py、网络或数据库。
"""

from __future__ import annotations

import json

import pytest

from app import event_types, optimizer, patch

# --------------------------------------------------------------------------- #
# inline fixture: a tiny Skill package mirroring the sample package structure
# --------------------------------------------------------------------------- #
MANIFEST = {
    "version": 1,
    "main": "SKILL.md",
    "subskills": [
        {"skill_id": "task_query", "name": "任务查询",
         "path": "references/task_query.md",
         "description": "查询项目或成员的任务列表、任务详情与状态"},
        {"skill_id": "weekly_report", "name": "周报生成",
         "path": "references/weekly_report.md",
         "description": "生成项目周报与汇总统计"},
    ],
}

SKILL_MD = """# 主技能

## 分发表

| skill_id | 子技能名 | 工具名 | 关键词 |
| --- | --- | --- | --- |
| task_query | 任务查询 | list_tasks | 任务列表 |
| weekly_report | 周报生成 | report_tasks | 周报 |
"""

TASK_QUERY_MD = """# 任务查询

负责查询任务列表与任务详情。

## 状态映射

- 进行中 -> active
- 已完成 -> done

## 参数
- project_id 与 status 由用户请求提供。
"""

WEEKLY_REPORT_MD = """# 周报生成

负责生成项目周报。
"""

BASE_FILES_TEXT = {
    "SKILL.md": SKILL_MD,
    "references/task_query.md": TASK_QUERY_MD,
    "references/weekly_report.md": WEEKLY_REPORT_MD,
}
BASE_FILES = {path: text.encode("utf-8") for path, text in BASE_FILES_TEXT.items()}
ALLOWED_PATHS = sorted(BASE_FILES_TEXT)
TEST_CASE_IDS = ["case_008", "case_012", "case_015", "case_021"]


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class FakeEmit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event_type, payload=None, *, level="info", case_id=None,
                 stage=None, call_id=None):
        event = {
            "seq": len(self.events) + 1,
            "type": event_type,
            "payload": dict(payload or {}),
            "level": level,
            "case_id": case_id,
            "stage": stage,
            "call_id": call_id,
        }
        self.events.append(event)
        return event

    @property
    def types(self) -> list[str]:
        return [event["type"] for event in self.events]

    def by_type(self, event_type: str) -> list[dict]:
        return [event for event in self.events if event["type"] == event_type]

    def index(self, event_type: str) -> int:
        return self.types.index(event_type)

    def error_events(self) -> list[dict]:
        return [event for event in self.events if event["level"] in ("error", "warning")]


class FakeAdapter:
    name = "fake_optimizer"
    capabilities = {"routing": False, "tool_arguments": False,
                    "native_tools": False, "usage": True, "streaming": False}

    def __init__(self, response=None, *, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict] = []

    def invoke(self, messages, config, tools):
        self.calls.append({"messages": messages, "config": config, "tools": tools})
        if self.exc is not None:
            raise self.exc
        return self.response

    @staticmethod
    def text(content: str, *, model: str = "fake-optimizer-v1") -> dict:
        return {
            "content": content,
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"input_tokens": 120, "output_tokens": 240},
            "model": model,
            "raw": {},
        }


def _model_candidate() -> dict:
    return {
        "diagnosis": {
            "summary": "分发表缺少状态映射关键词，导致 task_query 漏选",
            "attribution": "routing",
            "findings": [
                {"case_id": "case_008", "attribution": "routing",
                 "observable_events": ["skill_selected", "score_completed"],
                 "evidence": "预期 task_query，实际选中 weekly_report"},
            ],
        },
        "hypotheses": ["在分发表为 task_query 补充适用关键词可提升分发命中率"],
        "changed_files": ["SKILL.md"],
        "patch": {
            "summary": "补充 task_query 的适用关键词",
            "edits": [{
                "path": "SKILL.md",
                "op": "replace",
                "search": "| task_query | 任务查询 | list_tasks | 任务列表 |",
                "replace": "| task_query | 任务查询 | list_tasks | 任务列表、状态映射 |",
                "reason": "分发漏选 task_query",
            }],
        },
        "expected_effect": "预期提升路由准确率 R",
        "risk_cases": ["可能影响周报类 query 的路由歧义"],
    }


def _context(failures, *, optimizer_config=("configured")):
    context = {
        "project": {"id": "prj_demo", "name": "演示项目"},
        "baseline_skill_version": {
            "id": "sv_base",
            "manifest": MANIFEST,
            "files": dict(BASE_FILES_TEXT),
        },
        "allowed_paths": list(ALLOWED_PATHS),
        "failures": list(failures),
        "passing_summary": {"count": 20, "sample_case_ids": ["case_ok_1"]},
        "diagnosis_hints": {"routing_error": 3},
    }
    if optimizer_config == "configured":
        context["optimizer_config"] = {"provider": "fake", "model": "fake-optimizer-v1"}
    elif optimizer_config is not None:
        context["optimizer_config"] = optimizer_config
    return context


def _routing_failure(case_id: str = "case_008") -> dict:
    return {
        "case_id": case_id,
        "query": "把进行中的任务汇总一下",
        "tags": ["状态映射", "任务列表"],
        "expected": {"action": "tool_call", "routes": [["task_query"]],
                     "tool_calls": [{"name": "list_tasks",
                                     "arguments": {"status": "in_progress"}}]},
        "actual": {"selected_skills": ["weekly_report"], "action": "tool_call",
                   "tool_calls": [{"name": "report_tasks", "arguments": {}}]},
        "score": {
            "status": "fail",
            "routing": {"evaluable": True, "passed": False,
                        "actual_routes": [["weekly_report"]],
                        "allowed_routes": [["task_query"]]},
            "tool_args": {"evaluable": True, "passed": False},
            "joint": False,
            "diagnostics": [{"category": "wrong_route", "skill_id": "weekly_report",
                             "case_id": case_id}],
        },
        "events": [{"type": "skill_selected", "payload": {}},
                   {"type": "score_completed", "payload": {}}],
    }


def _mapping_failure(case_id: str = "case_012") -> dict:
    return {
        "case_id": case_id,
        "query": "查询项目 P1 中进行中的任务",
        "tags": ["状态映射"],
        "expected": {"action": "tool_call", "routes": [["task_query"]],
                     "tool_calls": [{"name": "list_tasks",
                                     "arguments": {"status": "in_progress"}}]},
        "actual": {"selected_skills": ["task_query"], "action": "tool_call",
                   "tool_calls": [{"name": "list_tasks",
                                   "arguments": {"status": "active"}}]},
        "score": {
            "status": "fail",
            "routing": {"evaluable": True, "passed": True},
            "tool_args": {"evaluable": True, "passed": False},
            "joint": False,
            "diagnostics": [{"category": "value_error", "skill_id": "task_query",
                             "tool": "list_tasks", "field_path": "$.status",
                             "expected_value": "in_progress",
                             "actual_value": "active", "case_id": case_id}],
        },
        "events": [{"type": "tool_call_finished", "payload": {}},
                   {"type": "score_completed", "payload": {}}],
    }


def _missing_arg_failure(case_id: str = "case_015") -> dict:
    return {
        "case_id": case_id,
        "query": "查询项目任务",
        "tags": ["任务查询"],
        "expected": {"action": "tool_call", "routes": [["task_query"]],
                     "tool_calls": [{"name": "list_tasks",
                                     "arguments": {"project_id": "P1"}}]},
        "actual": {"selected_skills": ["task_query"], "action": "tool_call",
                   "tool_calls": [{"name": "list_tasks", "arguments": {}}]},
        "score": {
            "status": "fail",
            "routing": {"evaluable": True, "passed": True},
            "tool_args": {"evaluable": True, "passed": False},
            "joint": False,
            "diagnostics": [{"category": "missing_arg", "skill_id": "task_query",
                             "tool": "list_tasks", "field_path": "$.project_id",
                             "case_id": case_id}],
        },
        "events": [{"type": "tool_call_finished", "payload": {}},
                   {"type": "score_completed", "payload": {}}],
    }


def _clarify_failure(case_id: str = "case_021") -> dict:
    return {
        "case_id": case_id,
        "query": "帮我查一下任务",
        "tags": ["澄清"],
        "expected": {"action": "clarify", "routes": [["task_query"]],
                     "missing_fields": ["project_id", "status"]},
        "actual": {"action": "tool_call", "selected_skills": ["task_query"],
                   "tool_calls": [{"name": "list_tasks", "arguments": {}}]},
        "score": {
            "status": "fail",
            "action_ok": False,
            "routing": {"evaluable": True, "passed": True},
            "tool_args": {"evaluable": True, "passed": False},
            "joint": False,
            "diagnostics": [{"category": "clarify_mismatch",
                             "skill_id": "task_query", "case_id": case_id}],
        },
        "events": [{"type": "finish", "payload": {}},
                   {"type": "score_completed", "payload": {}}],
    }


def _all_failures() -> list[dict]:
    return [_routing_failure(), _mapping_failure(), _missing_arg_failure(),
            _clarify_failure()]


# --------------------------------------------------------------------------- #
# patch.apply_edits
# --------------------------------------------------------------------------- #
def test_apply_edits_unique_hit_preserves_other_files_and_builds_diff():
    patched, issues = patch.apply_edits(
        BASE_FILES,
        [{"path": "references/task_query.md", "op": "replace",
          "search": "- 进行中 -> active", "replace": "- 进行中 -> in_progress"}],
        ALLOWED_PATHS,
    )
    assert issues == []
    assert b"- \xe8\xbf\x9b\xe8\xa1\x8c\xe4\xb8\xad -> in_progress" in \
        patched["references/task_query.md"]
    # untouched files keep their exact bytes
    assert patched["SKILL.md"] == BASE_FILES["SKILL.md"]
    assert patched["references/weekly_report.md"] == BASE_FILES["references/weekly_report.md"]

    diffs = patch.diff_files(BASE_FILES, patched)
    assert len(diffs) == 1
    assert diffs[0]["path"] == "references/task_query.md"
    assert diffs[0]["status"] == "modified"
    assert "进行中 -> active" in diffs[0]["diff"]
    assert "+- 进行中 -> in_progress" in diffs[0]["diff"]


def test_apply_edits_reports_not_found_and_not_unique():
    patched, issues = patch.apply_edits(
        BASE_FILES,
        [{"path": "SKILL.md", "search": "不存在的片段", "replace": "x"}],
        ALLOWED_PATHS,
    )
    assert issues and issues[0]["code"] == "edit_not_found"
    assert patched == BASE_FILES  # atomic: unchanged

    files = {"SKILL.md": b"# A\ndup\ndup\n"}
    patched, issues = patch.apply_edits(
        files, [{"path": "SKILL.md", "search": "dup", "replace": "x"}], ["SKILL.md"])
    assert issues and issues[0]["code"] == "edit_not_unique"
    assert patched == files


def test_apply_edits_is_atomic_when_one_edit_fails():
    patched, issues = patch.apply_edits(
        BASE_FILES,
        [
            {"path": "SKILL.md", "search": "任务列表 |", "replace": "任务列表、状态 |"},
            {"path": "SKILL.md", "search": "不存在", "replace": "x"},
        ],
        ALLOWED_PATHS,
    )
    assert len(issues) == 1 and issues[0]["code"] == "edit_not_found"
    assert patched["SKILL.md"] == BASE_FILES["SKILL.md"]


def test_apply_edits_rejects_non_markdown_add_delete_and_paths():
    # non-Markdown target
    patched, issues = patch.apply_edits(
        {**BASE_FILES, "manifest.json": b"{}"},
        [{"path": "manifest.json", "search": "{}", "replace": "{\"a\": 1}"}],
        ALLOWED_PATHS + ["manifest.json"],
    )
    assert issues[0]["code"] == "edit_not_markdown"

    # add / delete operations are forbidden
    for op in ("add", "delete", "remove", "rename"):
        patched, issues = patch.apply_edits(
            BASE_FILES, [{"path": "references/new.md", "op": op,
                          "search": "x", "replace": "y"}], ALLOWED_PATHS + [
                              "references/new.md"])
        assert issues[0]["code"] == "file_add_delete_not_allowed", op

    # path whitelist
    patched, issues = patch.apply_edits(
        BASE_FILES,
        [{"path": "references/task_query.md", "search": "任务查询",
          "replace": "任务查询 "}],
        ["SKILL.md"],
    )
    assert issues[0]["code"] == "edit_path_not_allowed"

    # path traversal
    patched, issues = patch.apply_edits(
        BASE_FILES, [{"path": "../evil.md", "search": "x", "replace": "y"}],
        ALLOWED_PATHS)
    assert issues[0]["code"] == "invalid_edit_path"


# --------------------------------------------------------------------------- #
# patch.static_check
# --------------------------------------------------------------------------- #
def _apply_model_edit() -> dict:
    patched, issues = patch.apply_edits(
        BASE_FILES,
        [{"path": "SKILL.md", "op": "replace",
          "search": "| task_query | 任务查询 | list_tasks | 任务列表 |",
          "replace": "| task_query | 任务查询 | list_tasks | 任务列表、状态映射 |"}],
        ALLOWED_PATHS)
    assert issues == []
    return patched


def test_static_check_passes_for_valid_candidate():
    result = patch.static_check(BASE_FILES, _apply_model_edit(), MANIFEST,
                                TEST_CASE_IDS)
    assert result["ok"] is True
    assert result["errors"] == []
    names = [check["name"] for check in result["checks"]]
    assert names == ["file_set_unchanged", "path_whitelist", "manifest_integrity",
                     "markdown_structure", "protected_content", "encoding_and_size"]
    assert all(check["ok"] for check in result["checks"])


def test_static_check_detects_case_id_and_expected_answer_leaks():
    patched = dict(BASE_FILES)
    text = SKILL_MD + "\n<!-- case_008 -->\n"
    patched["SKILL.md"] = text.encode("utf-8")
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert result["ok"] is False
    assert any(error["code"] == "test_case_id_written"
               for error in result["errors"])

    patched = dict(BASE_FILES)
    patched["SKILL.md"] = (SKILL_MD + "\n- expected tool_calls: []\n").encode("utf-8")
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "expected_answer_written"
               for error in result["errors"])

    patched = dict(BASE_FILES)
    patched["SKILL.md"] = (SKILL_MD + "\n标准答案：list_tasks\n").encode("utf-8")
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "expected_answer_written"
               for error in result["errors"])


def test_static_check_detects_path_manifest_and_structure_problems():
    # added file
    patched = dict(BASE_FILES)
    patched["references/new.md"] = b"# New\n"
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "file_added" for error in result["errors"])

    # changed non-Markdown resource
    base = {**BASE_FILES, "scripts/run.py": b"print(1)\n"}
    patched = {**base, "scripts/run.py": b"print(2)\n"}
    result = patch.static_check(base, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "path_not_markdown" for error in result["errors"])

    # manifest reference missing
    broken_manifest = {
        "version": 1, "main": "SKILL.md",
        "subskills": [{"skill_id": "task_query", "name": "任务查询",
                       "path": "references/missing.md"}],
    }
    result = patch.static_check(BASE_FILES, BASE_FILES, broken_manifest,
                                TEST_CASE_IDS)
    assert any(error["code"] == "missing_reference" for error in result["errors"])

    # empty markdown / invalid utf-8 / too large
    patched = dict(BASE_FILES)
    patched["references/task_query.md"] = b"   \n"
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "empty_file" for error in result["errors"])

    patched = dict(BASE_FILES)
    patched["references/task_query.md"] = b"# \xff\xfe\n"
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "encoding_error" for error in result["errors"])

    patched = dict(BASE_FILES)
    patched["SKILL.md"] = b"# Huge\n" + b"x" * (patch.MAX_MARKDOWN_BYTES + 10)
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "too_large" for error in result["errors"])

    # broken markdown structure: no heading / unbalanced fence
    patched = dict(BASE_FILES)
    patched["SKILL.md"] = b"plain text without heading\n"
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "markdown_structure" for error in result["errors"])

    patched = dict(BASE_FILES)
    patched["SKILL.md"] = (SKILL_MD + "\n```text\nunclosed\n").encode("utf-8")
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "markdown_structure" for error in result["errors"])


# --------------------------------------------------------------------------- #
# parse / validate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", ["", "   ", None])
def test_parse_output_empty_distinguishes_empty_response(raw):
    with pytest.raises(optimizer.OptimizerOutputError) as excinfo:
        optimizer.parse_output(raw)
    assert excinfo.value.code == "optimizer_empty_response"


@pytest.mark.parametrize("raw", ["不是 JSON", "{bad json}", "[1, 2, 3]", "42"])
def test_parse_output_invalid_json_is_parse_failed(raw):
    with pytest.raises(optimizer.OptimizerOutputError) as excinfo:
        optimizer.parse_output(raw)
    assert excinfo.value.code == "optimizer_parse_failed"


def test_parse_output_strips_single_code_fence_only():
    payload = json.dumps(_model_candidate(), ensure_ascii=False)
    assert optimizer.parse_output(payload)["changed_files"] == ["SKILL.md"]
    assert optimizer.parse_output(f"```json\n{payload}\n```")["changed_files"] == \
        ["SKILL.md"]
    with pytest.raises(optimizer.OptimizerOutputError):
        optimizer.parse_output("```json\n```")


def test_validate_candidate_output_normalizes_and_rejects_contract_errors():
    normalized, errors = optimizer.validate_candidate_output(_model_candidate())
    assert errors == []
    assert normalized["patch"]["edits"][0]["op"] == "replace"
    assert normalized["changed_files"] == ["SKILL.md"]
    assert normalized["diagnosis"]["attribution"] == "routing"
    assert normalized["diagnosis"]["findings"][0]["case_id"] == "case_008"

    bad = _model_candidate()
    del bad["hypotheses"]
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "missing_field" and error["field"] == "hypotheses"
               for error in errors)

    bad = _model_candidate()
    bad["changed_files"] = ["references/task_query.md"]
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "changed_files_mismatch" for error in errors)

    bad = _model_candidate()
    bad["diagnosis"]["attribution"] = "guessing"
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "invalid_attribution" for error in errors)

    bad = _model_candidate()
    bad["patch"]["edits"][0]["path"] = "../notes/other.md"
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "invalid_edit_path" for error in errors)

    bad = _model_candidate()
    bad["patch"]["edits"][0]["path"] = "notes/other.txt"
    bad["changed_files"] = ["notes/other.txt"]
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "invalid_edit_path" for error in errors)

    bad = _model_candidate()
    bad["patch"]["edits"][0]["op"] = "delete"
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "unsupported_edit_op" for error in errors)

    bad = _model_candidate()
    bad["diagnosis"]["findings"][0]["observable_events"] = []
    _, errors = optimizer.validate_candidate_output(bad)
    assert any(error["code"] == "missing_field"
               and "observable_events" in error["field"] for error in errors)


# --------------------------------------------------------------------------- #
# build_prompt
# --------------------------------------------------------------------------- #
def test_build_prompt_is_chinese_structured_and_secret_free():
    context = _context([_routing_failure()])
    context["optimizer_config"]["api_key"] = "should-never-enter-prompt"
    messages = optimizer.build_prompt(context)
    assert [message["role"] for message in messages] == ["system", "user"]
    system = messages[0]["content"]
    assert "changed_files" in system and "diagnosis" in system
    assert "不得写入任何测试 case_id" in system
    user = messages[1]["content"]
    assert "SKILL.md" in user and "references/task_query.md" in user
    assert "case_008" in user  # optimize-split evidence is allowed
    for message in messages:
        assert "should-never-enter-prompt" not in message["content"]
        assert "api_key" not in message["content"] or "api_key" not in message["content"]


def test_build_prompt_excludes_validation_and_final_test_samples():
    validation_failure = _routing_failure("case_validation_1")
    validation_failure["split"] = "validation"
    final_failure = _routing_failure("case_final_1")
    final_failure["split"] = "final_test"
    context = _context([_routing_failure(), validation_failure, final_failure])
    user = optimizer.build_prompt(context)[1]["content"]
    assert "case_008" in user
    assert "case_validation_1" not in user
    assert "case_final_1" not in user


# --------------------------------------------------------------------------- #
# heuristic fallback
# --------------------------------------------------------------------------- #
def test_heuristic_candidate_generates_the_generic_formats():
    candidate = optimizer.heuristic_candidate(_context(_all_failures()))
    assert candidate is not None
    edits = candidate["patch"]["edits"]
    assert edits
    by_path = {}
    for edit in edits:
        assert edit["op"] == "replace"
        by_path.setdefault(edit["path"], []).append(edit)

    # candidate is applicable and passes the static checks
    patched, issues = patch.apply_edits(BASE_FILES, edits, ALLOWED_PATHS)
    assert issues == []

    skill_text = patched["SKILL.md"].decode("utf-8")
    # 1) routing: dispatch table keywords appended to the matching row
    assert "| task_query | 任务查询 | list_tasks | 任务列表、状态映射 |" in skill_text
    # 2) clarify: section + per-field clarify bullet
    assert "## 澄清条件" in skill_text
    assert "缺少 project_id、status" in skill_text
    assert "返回 clarify 并列出缺失字段" in skill_text

    sub_text = patched["references/task_query.md"].decode("utf-8")
    # 3) wrong argument value: mapping literal fixed, other rows untouched
    assert "- 进行中 -> in_progress" in sub_text
    assert "- 已完成 -> done" in sub_text
    # 4) missing arg: required parameter line appended to the "## 参数" section
    assert "必填参数：project_id（从请求中提取）" in sub_text
    assert "## 参数" in sub_text
    assert by_path["references/task_query.md"]
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert result["ok"] is True, result["errors"]
    assert candidate["changed_files"] == ["SKILL.md", "references/task_query.md"]
    assert candidate["diagnosis"]["findings"]
    assert candidate["hypotheses"]
    assert candidate["expected_effect"]


def test_heuristic_candidate_never_embeds_case_ids_or_queries():
    failures = _all_failures()
    candidate = optimizer.heuristic_candidate(_context(failures))
    assert candidate is not None
    blob = json.dumps(candidate["patch"], ensure_ascii=False)
    for failure in failures:
        assert failure["case_id"] not in blob
        assert failure["query"] not in blob
    assert "case_" not in blob

    # a tag that looks like a case id must not be written into the Skill
    failure = _routing_failure()
    failure["tags"] = ["case_999"]
    assert optimizer.heuristic_candidate(_context([failure])) is None


# -- five-file / ~10-edit demo-scale heuristic (limits regression test) ------- #
FIVE_FILE_SKILL_MD = """# 主技能

## 分发表

| skill_id | 子技能 | 业务工具 | 适用场景关键词 |
| --- | --- | --- | --- |
| task_query | 任务查询 | list_tasks | 查询任务、任务列表 |
| task_assign | 任务指派 | assign_task | 指派任务、负责人 |
| task_create | 任务创建 | create_task | 创建任务、新建任务 |
| task_priority | 优先级设置 | set_priority | 设置优先级、紧急程度 |
"""

FIVE_FILE_FILES = {
    "SKILL.md": FIVE_FILE_SKILL_MD,
    "references/task_query.md": (
        "# 任务查询\n\n## 参数\n- project_id：从请求中提取项目 ID\n"
        "- status：按映射转换\n\n## 状态映射\n"
        "映射：进行中 -> active；已完成 -> done\n"),
    "references/task_assign.md": (
        "# 任务指派\n\n## 参数\n- task_id：从请求中提取任务 ID\n"),
    "references/task_create.md": (
        "# 任务创建\n\n## 参数\n- title：任务标题\n"
        "- project_id：从请求中提取项目 ID\n- priority：按映射转换\n\n"
        "## 优先级映射\n映射：紧急 -> urgent；普通 -> normal\n"),
    "references/task_priority.md": (
        "# 优先级设置\n\n## 参数\n- task_id：从请求中提取任务 ID\n"
        "- priority：按映射转换\n\n"
        "## 优先级映射\n映射：紧急 -> urgent；普通 -> normal\n"),
}
FIVE_FILE_MANIFEST = {
    "version": 1, "main": "SKILL.md",
    "subskills": [
        {"skill_id": "task_query", "name": "任务查询",
         "path": "references/task_query.md", "description": "查询任务"},
        {"skill_id": "task_assign", "name": "任务指派",
         "path": "references/task_assign.md", "description": "指派任务"},
        {"skill_id": "task_create", "name": "任务创建",
         "path": "references/task_create.md", "description": "创建任务"},
        {"skill_id": "task_priority", "name": "优先级设置",
         "path": "references/task_priority.md", "description": "设置优先级"},
    ],
}


def _value_error(case_id, skill_id, field, expected_value, actual_value) -> dict:
    return {
        "case_id": case_id,
        "query": f"调整 {field} 的请求",
        "tags": ["状态映射"],
        "expected": {"action": "tool_call", "routes": [[skill_id]]},
        "actual": {"selected_skills": [skill_id], "action": "tool_call"},
        "score": {"status": "fail", "routing": {"evaluable": True, "passed": True},
                  "tool_args": {"evaluable": True, "passed": False}, "joint": False,
                  "diagnostics": [{"category": "value_error", "skill_id": skill_id,
                                   "tool": "tool", "field_path": f"$.{field}",
                                   "expected_value": expected_value,
                                   "actual_value": actual_value}]},
        "events": [{"type": "tool_call_finished"}],
    }


def _missing_arg(case_id, skill_id, field, tool="tool") -> dict:
    return {
        "case_id": case_id,
        "query": "缺少字段的请求",
        "tags": ["缺参"],
        "expected": {"action": "tool_call", "routes": [[skill_id]]},
        "actual": {"selected_skills": [skill_id], "action": "tool_call"},
        "score": {"status": "fail", "routing": {"evaluable": True, "passed": True},
                  "tool_args": {"evaluable": True, "passed": False}, "joint": False,
                  "diagnostics": [{"category": "missing_arg", "skill_id": skill_id,
                                   "tool": tool, "field_path": f"$.{field}"}]},
        "events": [{"type": "tool_call_finished"}],
    }


def _five_file_failures() -> list[dict]:
    routing_query = _routing_failure("opt_001")
    routing_query["tags"] = ["进行中的任务", "状态映射"]
    routing_assign = _routing_failure("opt_004")
    routing_assign["expected"] = {
        "action": "tool_call", "routes": [["task_assign"]],
        "tool_calls": [{"name": "assign_task",
                        "arguments": {"task_id": "T42", "assignee_id": "U7"}}]}
    routing_assign["actual"] = {"selected_skills": ["task_query"],
                                "action": "tool_call",
                                "tool_calls": [{"name": "list_tasks",
                                                "arguments": {}}]}
    routing_assign["tags"] = ["交给", "改由"]
    clarify = _clarify_failure("opt_010")
    clarify["expected"] = {"action": "clarify", "routes": [["task_create"]],
                           "missing_fields": ["title", "project_id"]}
    clarify["actual"] = {"selected_skills": ["task_create"],
                         "action": "tool_call", "tool_calls": []}
    clarify["score"]["diagnostics"] = [
        {"category": "clarify_mismatch", "skill_id": "task_create"},
        {"category": "missing_arg", "skill_id": "task_create", "tool": "create_task",
         "field_path": "$.title"},
        {"category": "missing_arg", "skill_id": "task_create", "tool": "create_task",
         "field_path": "$.project_id"},
    ]
    return [
        routing_query, routing_assign,
        _missing_arg("opt_006", "task_assign", "assignee_id", "assign_task"),
        _missing_arg("opt_007", "task_assign", "assignee_id", "assign_task"),
        _value_error("opt_008", "task_create", "priority", "high", "urgent"),
        clarify,
        _value_error("opt_013", "task_priority", "priority", "high", "urgent"),
        _missing_arg("opt_024", "task_assign", "assignee_id", "assign_task"),
        _value_error("opt_033", "task_query", "status", "in_progress", "active"),
    ]


def _five_file_context() -> dict:
    return {
        "project": {"id": "prj_demo", "name": "演示项目"},
        "baseline_skill_version": {"id": "sv_base",
                                   "manifest": FIVE_FILE_MANIFEST,
                                   "files": dict(FIVE_FILE_FILES)},
        "allowed_paths": sorted(FIVE_FILE_FILES),
        "failures": _five_file_failures(),
        "passing_summary": {"count": 20, "sample_case_ids": []},
        "optimizer_config": {"provider": "heuristic"},
    }


def test_heuristic_candidate_produces_all_demo_scale_edits_without_dropping():
    context = _five_file_context()
    candidate = optimizer.heuristic_candidate(context)
    assert candidate is not None
    edits = candidate["patch"]["edits"]
    # 9 generic edits across 5 files must not be silently dropped by a low cap.
    assert len(edits) == 9, [edit["search"][:40] for edit in edits]
    assert candidate["changed_files"] == [
        "SKILL.md", "references/task_assign.md", "references/task_create.md",
        "references/task_priority.md", "references/task_query.md"]

    files = {path: text.encode("utf-8")
             for path, text in FIVE_FILE_FILES.items()}
    patched, issues = patch.apply_edits(files, edits, context["allowed_paths"])
    assert issues == []
    result = patch.static_check(files, patched, FIVE_FILE_MANIFEST,
                                [failure["case_id"] for failure in
                                 context["failures"]])
    assert result["ok"] is True, result["errors"]

    skill_text = patched["SKILL.md"].decode("utf-8")
    assert "进行中的任务" in skill_text
    assert "交给" in skill_text and "改由" in skill_text
    assert "## 澄清条件" in skill_text
    query_text = patched["references/task_query.md"].decode("utf-8")
    assert "进行中 -> in_progress" in query_text
    assign_text = patched["references/task_assign.md"].decode("utf-8")
    assert "必填参数：assignee_id（从请求中提取）" in assign_text
    create_text = patched["references/task_create.md"].decode("utf-8")
    assert "紧急 -> high" in create_text
    assert "必填参数：title（从请求中提取）" in create_text
    assert "必填参数：project_id（从请求中提取）" in create_text
    priority_text = patched["references/task_priority.md"].decode("utf-8")
    assert "紧急 -> high" in priority_text
    # no query / case id hardcoding anywhere
    blob = json.dumps(candidate["patch"], ensure_ascii=False)
    for failure in context["failures"]:
        assert failure["case_id"] not in blob
    assert "case_" not in blob


def test_heuristic_limits_are_context_configurable_and_bounded():
    context = _five_file_context()
    context["heuristic_limits"] = {"max_edits": 2, "max_files": 1}
    candidate = optimizer.heuristic_candidate(context)
    assert candidate is not None
    assert len(candidate["patch"]["edits"]) <= 2
    assert len(candidate["changed_files"]) <= 1

    context = _five_file_context()
    context["heuristic_limits"] = {"max_edits": 0, "max_files": -3}
    candidate = optimizer.heuristic_candidate(context)
    # clamps to at least 1 edit / 1 file instead of producing nothing
    assert candidate is not None
    assert len(candidate["patch"]["edits"]) == 1


def test_heuristic_candidate_returns_none_without_actionable_evidence():
    assert optimizer.heuristic_candidate(_context([])) is None
    unsupported = _routing_failure()
    unsupported["score"]["routing"] = {"evaluable": True, "passed": True,
                                       "actual_routes": [["task_query"]]}
    unsupported["score"]["diagnostics"] = [{"category": "extra_arg",
                                            "tool": "list_tasks"}]
    assert optimizer.heuristic_candidate(_context([unsupported])) is None
    context = _context([_routing_failure()])
    context["baseline_skill_version"]["files"] = {}
    assert optimizer.heuristic_candidate(context) is None
    context = _context([_routing_failure()])
    context["allowed_paths"] = []
    assert optimizer.heuristic_candidate(context) is None


def test_heuristic_candidate_appends_to_existing_clarify_section():
    files = dict(BASE_FILES_TEXT)
    files["SKILL.md"] = SKILL_MD + (
        "\n## 澄清条件\n"
        "- 当 task_query 缺少成员 ID 时，返回 clarify 并列出缺失字段。\n")
    context = _context([_clarify_failure()])
    context["baseline_skill_version"]["files"] = files
    candidate = optimizer.heuristic_candidate(context)
    assert candidate is not None
    edits = [edit for edit in candidate["patch"]["edits"]
             if edit["path"] == "SKILL.md"]
    assert edits
    patched, issues = patch.apply_edits(
        {path: content.encode("utf-8") for path, content in files.items()},
        edits, ALLOWED_PATHS)
    assert issues == []
    text = patched["SKILL.md"].decode("utf-8")
    assert text.count("## 澄清条件") == 1
    section = text.split("## 澄清条件", 1)[1]
    assert "缺少 project_id、status" in section
    assert "缺少成员 ID" in section


def test_static_check_detects_dangling_markdown_reference():
    patched = dict(BASE_FILES)
    patched["SKILL.md"] = (SKILL_MD + "\n[子技能](references/missing.md)\n").encode(
        "utf-8")
    result = patch.static_check(BASE_FILES, patched, MANIFEST, TEST_CASE_IDS)
    assert any(error["code"] == "dangling_reference"
               for error in result["errors"])


# --------------------------------------------------------------------------- #
# generate_candidate: happy path
# --------------------------------------------------------------------------- #
def test_generate_candidate_with_model_returns_patch_and_events_in_order():
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(
        json.dumps(_model_candidate(), ensure_ascii=False)))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)

    assert result["status"] == "ok"
    assert result["error_code"] is None
    assert result["used_fallback"] is False
    assert result["changed_files"] == ["SKILL.md"]
    assert result["patch"]["edits"][0]["search"].startswith("| task_query |")
    assert result["diagnosis"]["attribution"] == "routing"
    assert result["risk_cases"] == ["可能影响周报类 query 的路由歧义"]

    types = emit.types
    assert types[0] == event_types.OPTIMIZER_INPUT_BUILT
    assert types.index(event_types.OPTIMIZER_CALL_STARTED) < \
        types.index(event_types.OPTIMIZER_CALL_FINISHED) < \
        types.index(event_types.OPTIMIZER_OUTPUT_PARSED) < \
        types.index(event_types.PATCH_CREATED)
    assert event_types.FALLBACK_STARTED not in types
    for event in emit.events:
        assert event["stage"] == "candidate_generation"

    # the model received the built messages and the frozen optimizer config
    assert adapter.calls[0]["messages"] == optimizer.build_prompt(
        _context([_routing_failure()]))
    assert adapter.calls[0]["config"]["model"] == "fake-optimizer-v1"

    # no full model output is written into the event payloads
    finished = emit.by_type(event_types.OPTIMIZER_CALL_FINISHED)[0]
    assert finished["payload"]["content_chars"] > 0
    assert len(finished["payload"]["output_preview"]) <= 201

    patched, issues = patch.apply_edits(BASE_FILES, result["patch"]["edits"],
                                        ALLOWED_PATHS)
    assert issues == []
    assert patch.static_check(BASE_FILES, patched, MANIFEST,
                              TEST_CASE_IDS)["ok"] is True


# --------------------------------------------------------------------------- #
# generate_candidate: direct-cause events + fallback
# --------------------------------------------------------------------------- #
def _assert_issue_shape(event: dict) -> None:
    issue = event["payload"].get("issue")
    assert isinstance(issue, dict), event
    for field in ("error_code", "title", "message", "impact", "next_action",
                  "cause_event_seq"):
        assert field in issue, (field, event)


def test_generate_candidate_empty_output_records_direct_cause_then_fallback():
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(""))
    result = optimizer.generate_candidate(
        _context([_routing_failure()]), adapter, emit)

    assert result["status"] == "ok"  # heuristic produced a patch
    assert result["used_fallback"] is True
    empty_events = emit.by_type(event_types.OPTIMIZER_EMPTY_RESPONSE)
    assert empty_events and empty_events[0]["level"] == "error"
    _assert_issue_shape(empty_events[0])
    assert empty_events[0]["payload"]["issue"]["error_code"] == \
        "optimizer_empty_response"
    types = emit.types
    assert types.index(event_types.OPTIMIZER_EMPTY_RESPONSE) < \
        types.index(event_types.FALLBACK_STARTED) < \
        types.index(event_types.PATCH_CREATED)
    fallback_started = emit.by_type(event_types.FALLBACK_STARTED)[0]
    assert fallback_started["payload"]["reason"] == "optimizer_empty_response"
    assert fallback_started["payload"]["cause_event_seq"] == \
        empty_events[0]["seq"]


def test_generate_candidate_parse_failure_falls_back_to_heuristic():
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text("无法解析的模型输出"))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)
    assert result["status"] == "ok" and result["used_fallback"] is True
    parse_events = emit.by_type(event_types.OPTIMIZER_PARSE_FAILED)
    assert parse_events and parse_events[0]["level"] == "error"
    _assert_issue_shape(parse_events[0])
    assert parse_events[0]["payload"]["issue"]["error_code"] == \
        "optimizer_parse_failed"
    assert emit.types.index(event_types.OPTIMIZER_PARSE_FAILED) < \
        emit.types.index(event_types.FALLBACK_STARTED)
    row_edit = [edit for edit in result["patch"]["edits"]
                if edit["path"] == "SKILL.md"][0]
    assert "任务列表、状态映射" in row_edit["replace"]


def test_generate_candidate_call_exception_records_optimizer_call_failed():
    emit = FakeEmit()
    adapter = FakeAdapter(exc=RuntimeError("gateway down"))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)
    assert result["status"] == "ok" and result["used_fallback"] is True
    failed = emit.by_type(event_types.OPTIMIZER_CALL_FAILED)
    assert failed and failed[0]["level"] == "error"
    _assert_issue_shape(failed[0])
    issue = failed[0]["payload"]["issue"]
    assert issue["error_code"] == "optimizer_call_failed"
    assert issue["technical_detail"]["exception_type"] == "RuntimeError"
    assert emit.types.index(event_types.OPTIMIZER_CALL_FAILED) < \
        emit.types.index(event_types.FALLBACK_STARTED)


def test_generate_candidate_not_configured_and_no_fallback_failed():
    emit = FakeEmit()
    context = _context([_routing_failure()], optimizer_config={})
    result = optimizer.generate_candidate(context, FakeAdapter(), emit)
    assert result["status"] == "ok" and result["used_fallback"] is True
    not_configured = emit.by_type(event_types.OPTIMIZER_NOT_CONFIGURED)
    assert not_configured and not_configured[0]["level"] == "error"
    _assert_issue_shape(not_configured[0])
    assert not_configured[0]["payload"]["issue"]["error_code"] == \
        "optimizer_not_configured"
    assert emit.types.index(event_types.OPTIMIZER_NOT_CONFIGURED) < \
        emit.types.index(event_types.FALLBACK_STARTED)

    emit = FakeEmit()
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          None, emit, allow_fallback=False)
    assert result["status"] == "failed"
    assert result["error_code"] == "optimizer_not_configured"
    assert result["used_fallback"] is False
    assert event_types.FALLBACK_STARTED not in emit.types
    assert result["patch"] == {"summary": "", "edits": []}


def test_generate_candidate_no_fallback_keeps_direct_error_code():
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text("{not json"))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit, allow_fallback=False)
    assert result["status"] == "failed"
    assert result["error_code"] == "optimizer_parse_failed"
    assert event_types.FALLBACK_STARTED not in emit.types
    assert result["changed_files"] == []


def test_generate_candidate_fallback_without_change_is_no_patch():
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(""))
    result = optimizer.generate_candidate(_context([]), adapter, emit)
    assert result["status"] == "no_patch"
    assert result["error_code"] == "fallback_no_change"
    assert result["used_fallback"] is True
    no_change = emit.by_type(event_types.FALLBACK_NO_CHANGE)
    assert no_change and no_change[0]["level"] == "warning"
    _assert_issue_shape(no_change[0])
    assert no_change[0]["payload"]["issue"]["error_code"] == "fallback_no_change"


def test_generate_candidate_model_no_change_records_optimizer_no_change():
    candidate = _model_candidate()
    candidate["patch"] = {"summary": "证据不足，无需修改", "edits": []}
    candidate["changed_files"] = []
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(
        json.dumps(candidate, ensure_ascii=False)))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)
    # heuristic can still produce a patch after the explicit "no change"
    assert result["status"] == "ok" and result["used_fallback"] is True
    no_change = emit.by_type(event_types.OPTIMIZER_NO_CHANGE)
    assert no_change and no_change[0]["level"] == "warning"
    _assert_issue_shape(no_change[0])
    assert emit.types.index(event_types.OPTIMIZER_NO_CHANGE) < \
        emit.types.index(event_types.FALLBACK_STARTED)


def test_generate_candidate_invalid_validation_output_falls_back():
    candidate = _model_candidate()
    candidate["diagnosis"]["findings"][0]["observable_events"] = []
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(
        json.dumps(candidate, ensure_ascii=False)))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)
    assert result["status"] == "ok" and result["used_fallback"] is True
    parse_events = emit.by_type(event_types.OPTIMIZER_PARSE_FAILED)
    assert parse_events
    detail = parse_events[0]["payload"]["issue"]["technical_detail"]
    assert detail["validation_errors"]
    assert detail["validation_error_count"] >= 1


def test_generate_candidate_target_path_outside_whitelist_falls_back():
    candidate = _model_candidate()
    candidate["patch"]["edits"][0]["path"] = "references/other.md"
    candidate["changed_files"] = ["references/other.md"]
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(
        json.dumps(candidate, ensure_ascii=False)))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)
    assert result["status"] == "ok" and result["used_fallback"] is True
    parse_events = emit.by_type(event_types.OPTIMIZER_PARSE_FAILED)
    assert parse_events
    target_errors = parse_events[0]["payload"]["issue"]["technical_detail"][
        "target_errors"]
    assert any(error["code"] == "edit_path_not_allowed" for error in target_errors)


def test_generate_candidate_heuristic_provider_is_not_configured():
    # sample environment.json uses provider "heuristic": no real optimizer model.
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(
        json.dumps(_model_candidate(), ensure_ascii=False)))
    context = _context([_routing_failure()])
    context["optimizer_config"] = {"provider": "heuristic",
                                   "model": "frozen-heuristic-v1"}
    result = optimizer.generate_candidate(context, adapter, emit)
    assert adapter.calls == []
    assert result["status"] == "ok" and result["used_fallback"] is True
    not_configured = emit.by_type(event_types.OPTIMIZER_NOT_CONFIGURED)
    # Rule-based repair is a deliberate choice: the direct reason is still
    # recorded before the fallback, but as information rather than an error.
    assert not_configured and not_configured[0]["level"] == "info"
    assert not_configured[0]["payload"]["optimizer_mode"] == "heuristic"
    assert not_configured[0]["payload"]["issue"]["title"] == "规则修复模式"
    assert not_configured[0]["payload"]["issue"]["error_code"] == \
        "optimizer_not_configured"
    assert emit.types.index(event_types.OPTIMIZER_NOT_CONFIGURED) < \
        emit.types.index(event_types.FALLBACK_STARTED)


def test_generate_candidate_error_events_all_carry_issue_objects():
    scenarios = [
        (_context([_routing_failure()]), None, None),
        (_context([_routing_failure()], optimizer_config={}), FakeAdapter(), None),
        (_context([_routing_failure()]), FakeAdapter(exc=RuntimeError("x")), None),
        (_context([_routing_failure()]), FakeAdapter(FakeAdapter.text("")), None),
        (_context([_routing_failure()]), FakeAdapter(FakeAdapter.text("oops")), None),
        (_context([]), FakeAdapter(FakeAdapter.text("")), None),
    ]
    for context, adapter, _ in scenarios:
        emit = FakeEmit()
        optimizer.generate_candidate(context, adapter, emit)
        for event in emit.error_events():
            _assert_issue_shape(event)


# --------------------------------------------------------------------------- #
# log safety / raw output
# --------------------------------------------------------------------------- #
def test_generate_candidate_redacts_secrets_from_events_and_raw_output():
    candidate = _model_candidate()
    candidate["note"] = "api_key=SUPER_SECRET_TOKEN"
    content = json.dumps(candidate, ensure_ascii=False)
    emit = FakeEmit()
    adapter = FakeAdapter(FakeAdapter.text(content))
    result = optimizer.generate_candidate(_context([_routing_failure()]),
                                          adapter, emit)
    assert result["status"] == "ok"
    assert "SUPER_SECRET_TOKEN" not in result["raw_output"]
    for event in emit.events:
        assert "SUPER_SECRET_TOKEN" not in json.dumps(event["payload"],
                                                      ensure_ascii=False)
