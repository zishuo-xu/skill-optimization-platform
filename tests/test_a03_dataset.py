"""A03: dataset import, validation, grouping/split and fixture matching.

Covers requirement §12–§14 and contract §2.2/§2.5. Uses no global data dir and
no database: every assertion is a pure in-memory call.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from app import dataset
from app.errors import ValidationError
from app.fixtures import FixtureMatcher, FixtureRuntime, match_fixture
from app.ids import stable_case_id

MANIFEST = {
    "version": 1,
    "main": "SKILL.md",
    "subskills": [
        {"skill_id": "task_query", "name": "任务查询",
         "path": "references/task_query.md"},
        {"skill_id": "task_search", "name": "任务搜索",
         "path": "references/task_search.md"},
    ],
}

TOOL_SCHEMA = [
    {
        "name": "list_tasks",
        "description": "查询任务列表",
        "parameters": {
            "type": "object",
            "required": ["project_id"],
            "properties": {
                "project_id": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["in_progress", "active"]},
            },
        },
    },
    {"name": "search_tasks", "parameters": {"type": "object"}},
]


def make_case(index: int = 1, **overrides) -> dict:
    case = {
        "id": f"case_{index:03d}",
        "query": f"查询项目 P{index} 中进行中的任务",
        "group_id": f"group_{index}",
        "expected": {
            "action": "tool_call",
            "routes": [["task_query"]],
            "tool_calls": [{
                "name": "list_tasks",
                "arguments": {"project_id": f"P{index}",
                              "status": "in_progress"},
            }],
        },
        "fixtures": [{
            "name": "list_tasks",
            "match": {"project_id": f"P{index}"},
            "response": {"tasks": []},
        }],
    }
    case.update(overrides)
    return case


def prepare(items, **kwargs):
    options = {"manifest": MANIFEST, "tool_schema": TOOL_SCHEMA}
    options.update(kwargs)
    return dataset.validate_and_prepare(items, **options)


def codes(result: dict) -> list[str]:
    return [error["code"] for error in result["errors"]]


# --------------------------------------------------------------------------- #
# payload parsing
# --------------------------------------------------------------------------- #
def test_parse_jsonl_tolerates_bom_and_blank_lines_and_reports_bad_line():
    raw = ("\ufeff" + json.dumps(make_case(1), ensure_ascii=False)
           + "\n\n" + '{"query": ' + "\n")
    items, fmt = dataset.parse_payload(raw, "cases.jsonl")

    assert fmt == "jsonl"
    assert len(items) == 2
    assert items[0]["_line"] == 1
    assert items[0]["query"].startswith("查询项目")
    assert items[1]["_line"] == 3
    assert items[1]["_parse_error"]["line"] == 3
    assert items[1]["_parse_error"]["code"] == "parse_error"


def test_parse_json_array_and_cases_envelope():
    raw = json.dumps([make_case(1), make_case(2)], ensure_ascii=False)
    items, fmt = dataset.parse_payload(raw, "cases.json")
    assert fmt == "json"
    assert [item["_line"] for item in items] == [1, 2]
    assert [item["id"] for item in items] == ["case_001", "case_002"]

    envelope = json.dumps({"cases": [make_case(3)]}, ensure_ascii=False)
    items, fmt = dataset.parse_payload(envelope.encode("utf-8"), "cases.json")
    assert fmt == "json"
    assert items[0]["id"] == "case_003"


def test_parse_invalid_json_raises_with_line_number():
    with pytest.raises(ValidationError) as excinfo:
        dataset.parse_payload('[{"query": "x"},\n{"query": }]', "cases.json")
    error = excinfo.value
    assert error.code == "parse_error"
    assert error.details["line"] == 2


def test_parse_rejects_unsupported_extension():
    with pytest.raises(ValidationError) as excinfo:
        dataset.parse_payload("query\n", "cases.csv")
    assert excinfo.value.code == "unsupported_format"


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_validate_happy_path_normalises_case_and_assigns_split():
    result = prepare([make_case(1)])

    assert result["errors"] == []
    assert result["stats"]["total"] == 1
    assert result["stats"]["valid"] == 1
    assert result["stats"]["invalid"] == 0
    assert result["stats"]["splits"] == {"optimize": 1, "validation": 0,
                                         "final_test": 0}
    case = result["cases"][0]
    assert case["case_id"] == "case_001"
    assert case["split"] == "optimize"
    assert "_line" not in case
    expected = case["expected"]
    assert expected["action"] == "tool_call"
    assert expected["call_match"] == "ordered_exact"
    assert expected["route_ordered"] is False
    assert expected["missing_fields"] == []
    assert case["fixtures"][0]["consume"] == "always"
    coverage = result["stats"]["coverage"]
    assert coverage["skills"]["task_query"]["total"] == 1
    assert coverage["skills"]["task_search"]["total"] == 0
    assert coverage["tools"]["list_tasks"] == 1
    assert any(warning["code"] == "no_final_test"
               for warning in result["warnings"])


def test_validate_missing_query_and_expected_are_errors_with_line():
    bad_query = make_case(1)
    del bad_query["query"]
    bad_expected = make_case(2)
    del bad_expected["expected"]

    result = prepare([bad_query, bad_expected])
    assert codes(result) == ["missing_query", "missing_expected"]
    first, second = result["errors"]
    assert first["line"] == 1 and first["field_path"] == "query"
    assert second["line"] == 2 and second["field_path"] == "expected"
    assert result["cases"] == []
    assert result["stats"]["invalid"] == 2


def test_validate_generates_stable_case_id():
    raw = {"query": "查询项目 P9 中进行中的任务", "group_id": "g-9",
           "expected": {"action": "no_action"}}
    result = prepare([raw])
    expected_id = stable_case_id("查询项目 P9 中进行中的任务", "g-9")
    assert result["errors"] == []
    assert result["cases"][0]["case_id"] == expected_id


def test_validate_unknown_skill_and_tool_have_field_paths():
    case = make_case(1)
    case["expected"]["routes"] = [["task_query", "unknown_skill"]]
    case["expected"]["tool_calls"] = [{"name": "nope_tool", "arguments": {}}]

    result = prepare([case])
    assert set(codes(result)) == {"unknown_skill_id", "unknown_tool"}
    by_code = {error["code"]: error for error in result["errors"]}
    assert by_code["unknown_skill_id"]["field_path"] == "expected.routes[0][1]"
    assert by_code["unknown_tool"]["field_path"] == "expected.tool_calls[0].name"
    assert by_code["unknown_tool"]["case_id"] == "case_001"


@pytest.mark.parametrize("mutate, code", [
    (lambda c: c["expected"].__setitem__("action", "run"), "invalid_action"),
    (lambda c: c["expected"].__setitem__("call_match", "fuzzy"),
     "invalid_call_match"),
    (lambda c: c.__setitem__("split", "train"), "invalid_split"),
    (lambda c: c["fixtures"][0].__setitem__("consume", "sometimes"),
     "invalid_consume"),
])
def test_validate_rejects_illegal_enum_values(mutate, code):
    case = make_case(1)
    mutate(case)
    result = prepare([case])
    assert code in codes(result)
    assert result["cases"] == []


def test_validate_duplicate_case_id_and_duplicate_query_warning():
    first = make_case(1)
    duplicate_id = make_case(1)
    duplicate_id["query"] = "另一条 query"
    duplicate_query = make_case(2)
    duplicate_query["query"] = first["query"]

    result = prepare([first, duplicate_id, duplicate_query])
    assert codes(result) == ["duplicate_case_id"]
    assert result["errors"][0]["line"] == 2
    assert result["stats"]["valid"] == 2
    duplicate_warnings = [w for w in result["warnings"]
                          if w["code"] == "duplicate_query"]
    assert len(duplicate_warnings) == 1
    assert duplicate_warnings[0]["line"] == 3


def test_validate_conflicting_expectations():
    clarify_without_fields = make_case(1)
    clarify_without_fields["expected"] = {"action": "clarify"}
    no_action_with_calls = make_case(3)
    no_action_with_calls["expected"] = {
        "action": "no_action",
        "tool_calls": [{"name": "list_tasks",
                        "arguments": {"project_id": "P3"}}],
    }
    tool_call_with_fields = make_case(4)
    tool_call_with_fields["expected"]["missing_fields"] = ["project_id"]

    result = prepare([clarify_without_fields, no_action_with_calls,
                      tool_call_with_fields])
    assert codes(result) == [
        "clarify_missing_fields",
        "no_action_with_tool_calls",
        "conflicting_expected",
    ]
    assert result["cases"] == []


def test_clarify_may_keep_routes_for_optimizer_diagnosis():
    clarify = make_case(1)
    clarify["expected"] = {
        "action": "clarify",
        "routes": [["task_query"]],
        "tool_calls": [],
        "missing_fields": ["project_id"],
    }
    result = prepare([clarify])
    assert result["errors"] == []
    assert result["stats"]["valid"] == 1
    assert result["cases"][0]["expected"]["routes"] == [["task_query"]]
    assert result["cases"][0]["expected"]["missing_fields"] == ["project_id"]

    clarify_with_calls = make_case(2)
    clarify_with_calls["expected"] = {
        "action": "clarify",
        "routes": [["task_query"]],
        "tool_calls": [{"name": "list_tasks",
                        "arguments": {"project_id": "P2"}}],
        "missing_fields": ["project_id"],
    }
    rejected = prepare([clarify_with_calls])
    assert codes(rejected) == ["conflicting_expected"]


def test_validate_fixture_structure_errors():
    bad_match = make_case(1)
    bad_match["fixtures"] = [{"name": "list_tasks", "match": ["project_id"]}]
    unknown_tool = make_case(2)
    unknown_tool["fixtures"] = [{"name": "ghost_tool", "match": {}}]
    bad_list = make_case(3)
    bad_list["fixtures"] = {"name": "list_tasks"}

    result = prepare([bad_match, unknown_tool, bad_list])
    assert set(codes(result)) == {"invalid_fixture_match", "unknown_tool",
                                  "invalid_fixtures"}


# --------------------------------------------------------------------------- #
# splits
# --------------------------------------------------------------------------- #
def _split_cases(count: int, group_size: int = 3) -> list[dict]:
    cases = []
    for index in range(count):
        cases.append({
            "case_id": f"case_{index:03d}",
            "query": f"query {index}",
            "group_id": f"group_{index // group_size}",
            "expected": {"action": "no_action"},
        })
    return cases


def test_assign_splits_is_deterministic_and_never_splits_a_group():
    cases = _split_cases(30, group_size=3)
    config = {"optimize": 0.7, "validation": 0.2, "final_test": 0.1}

    first = dataset.assign_splits(cases, config, 42)
    second = dataset.assign_splits(cases, config, 42)
    assert [(c["case_id"], c["split"]) for c in first["cases"]] == \
        [(c["case_id"], c["split"]) for c in second["cases"]]

    group_splits: dict[str, set[str]] = {}
    for case in first["cases"]:
        group_splits.setdefault(case["group_id"], set()).add(case["split"])
    assert all(len(splits) == 1 for splits in group_splits.values())

    counts = Counter(case["split"] for case in first["cases"])
    assert counts == {"optimize": 21, "validation": 6, "final_test": 3}
    assert all(warning["code"] != "no_final_test"
               for warning in first["warnings"])


def test_assign_splits_small_dataset_falls_back_to_optimize_and_validation():
    cases = _split_cases(5, group_size=1)
    result = dataset.assign_splits(cases, None, 42)
    counts = Counter(case["split"] for case in result["cases"])
    assert counts["final_test"] == 0
    assert counts["optimize"] > 0 and counts["validation"] > 0
    assert any(warning["code"] == "no_final_test"
               for warning in result["warnings"])


def test_assign_splits_keeps_explicit_splits_and_warns_without_final_test():
    cases = _split_cases(4, group_size=1)
    for case in cases:
        case["split"] = "optimize"
    result = dataset.assign_splits(cases, None, 42)
    assert {case["split"] for case in result["cases"]} == {"optimize"}
    assert any(warning["code"] == "no_final_test"
               for warning in result["warnings"])


def test_assign_splits_resolves_conflicting_explicit_group_split():
    cases = _split_cases(2, group_size=2)
    cases[0]["split"] = "optimize"
    cases[1]["split"] = "final_test"
    result = dataset.assign_splits(cases, None, 42)
    assert len({case["split"] for case in result["cases"]}) == 1
    assert any(warning["code"] == "group_split_conflict"
               for warning in result["warnings"])


def test_validate_and_prepare_applies_requested_ratios():
    cases = [make_case(index) for index in range(1, 11)]
    for case in cases:
        case["group_id"] = f"group_{case['id']}"
    result = prepare(cases, split_config={"optimize": 0.5,
                                          "validation": 0.5,
                                          "final_test": 0.0})
    assert result["stats"]["splits"] == {"optimize": 5, "validation": 5,
                                         "final_test": 0}
    assert all(case["split"] in dataset.SPLITS for case in result["cases"])


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #
def test_coverage_counts_skills_tools_splits_and_actions():
    cases = [
        {"split": "optimize", "expected": {
            "action": "tool_call",
            "routes": [["task_query"], ["task_search"]],
            "tool_calls": [{"name": "list_tasks", "arguments": {}}]}},
        {"split": "validation", "expected": {
            "action": "clarify", "missing_fields": ["project_id"]}},
        {"split": "final_test", "expected": {"action": "no_action"}},
    ]
    result = dataset.coverage(cases, ["task_query", "task_search", "task_other"],
                             ["list_tasks", "search_tasks"])

    assert result["total"] == 3
    assert result["split_counts"] == {"optimize": 1, "validation": 1,
                                      "final_test": 1}
    assert result["skills"]["task_query"] == {"optimize": 1, "validation": 0,
                                              "final_test": 0, "total": 1}
    assert result["skills"]["task_search"]["optimize"] == 1
    assert result["skills"]["task_other"]["total"] == 0
    assert result["tools"] == {"list_tasks": 1, "search_tasks": 0}
    assert result["multi_route_cases"] == 1
    assert result["actions"] == {"tool_call": 1, "clarify": 1, "no_action": 1}
    assert result["clarify_cases"] == 1
    assert result["no_action_cases"] == 1


# --------------------------------------------------------------------------- #
# fixture matching
# --------------------------------------------------------------------------- #
def test_fixture_matcher_partial_and_deep_matching():
    fixtures = [
        {"name": "list_tasks", "match": {"project_id": "P1"},
         "response": {"tasks": [1]}},
        {"name": "list_tasks",
         "match": {"project_id": 123}, "response": {"tasks": [2]}},
        {"name": "search_tasks",
         "match": {"filters": {"status": "active"}}, "response": {"n": 3}},
        {"name": "search_tasks", "match": {"tags": ["a", "b"]},
         "response": {"n": 4}},
    ]
    matcher = FixtureMatcher(fixtures)

    hit = matcher.match("list_tasks", {"project_id": "P1", "extra": True})
    assert hit["matched"] is True
    assert hit["response"] == {"tasks": [1]}

    # type strict: integer condition does not match a string argument
    assert matcher.match("list_tasks", {"project_id": "123"})["matched"] is False
    assert matcher.match("list_tasks", {"project_id": 123})["response"] == {"tasks": [2]}

    # nested object conditions are deep subsets, arrays compare in order
    assert matcher.match("search_tasks",
                         {"filters": {"status": "active", "x": 1}})["matched"] is True
    assert matcher.match("search_tasks", {"tags": ["b", "a"]})["matched"] is False
    assert matcher.match("search_tasks", {"tags": ["a", "b"]})["matched"] is True

    miss = matcher.match("list_tasks", {"project_id": "missing"})
    assert miss["matched"] is False
    assert miss["reason"] == "no_fixture_match"
    assert miss["tool"] == "list_tasks"


def test_fixture_matcher_consumes_once_in_call_order():
    fixtures = [
        {"name": "list_tasks", "match": {}, "response": {"page": 1},
         "consume": "once"},
        {"name": "list_tasks", "match": {}, "response": {"page": 2},
         "consume": "once"},
        {"name": "list_tasks", "match": {"project_id": "X"},
         "response": {"page": "always"}, "consume": "always"},
    ]
    matcher = FixtureMatcher(fixtures)
    assert matcher.match("list_tasks", {"project_id": "P"})["response"] == {"page": 1}
    assert matcher.match("list_tasks", {"project_id": "P"})["response"] == {"page": 2}
    # both once fixtures are consumed; only the always fixture can answer now
    assert matcher.match("list_tasks", {"project_id": "P"})["matched"] is False
    assert matcher.match("list_tasks", {"project_id": "X"})["response"] == {
        "page": "always"}
    assert matcher.match("list_tasks", {"project_id": "X"})["response"] == {
        "page": "always"}

    matcher.reset()
    assert matcher.match("list_tasks", {"project_id": "P"})["response"] == {"page": 1}


def test_fixture_runtime_records_misses_and_returns_none():
    runtime = FixtureRuntime([
        {"name": "list_tasks", "match": {"project_id": "P1"},
         "response": {"tasks": []}},
    ])
    hit = runtime.resolve("list_tasks", {"project_id": "P1"})
    assert hit is not None and hit["matched"] is True
    assert runtime.resolve("list_tasks", {"project_id": "P2"}) is None
    assert runtime.misses == [{"tool": "list_tasks",
                               "arguments": {"project_id": "P2"}}]
    runtime.reset()
    assert runtime.misses == []


def test_fixture_matching_returns_copies_and_stateless_helper():
    fixture = {"name": "list_tasks", "match": {},
               "response": {"tasks": [{"id": 1}]}}
    matcher = FixtureMatcher([fixture])
    hit = matcher.match("list_tasks", {})
    hit["response"]["tasks"].append({"id": 2})
    assert matcher.match("list_tasks", {})["response"] == {"tasks": [{"id": 1}]}

    assert match_fixture([fixture], "list_tasks", {})["response"] == {
        "tasks": [{"id": 1}]}
    assert match_fixture([fixture], "ghost", {}) is None


def test_compact_shorthand_with_path_alias_function_name_and_auto_fixture():
    item = {
        "query": "帮我修一修眼睛，让它更有神采",
        "skill": "references/task_query.md",
        "tool": {
            "functionName": "list_tasks",
            "arguments": {"project_id": "P1", "status": "in_progress"},
        },
        "mock": {"tasks": []},
    }
    result = prepare([item])
    assert result["errors"] == []
    case = result["cases"][0]
    assert case["expected"]["routes"] == [["task_query"]]
    assert case["expected"]["tool_calls"] == [{
        "name": "list_tasks",
        "arguments": {"project_id": "P1", "status": "in_progress"},
    }]
    assert case["fixtures"] == [{
        "name": "*", "match": {}, "response": {"tasks": []},
        "consume": "always",
    }]


def test_compact_clarify_no_action_and_flat_route():
    items = [
        {"query": "帮我建任务", "action": "clarify", "missing": ["title"],
         "skill": "task_query"},
        {"query": "你好", "action": "no_action"},
        {"query": "查询 P1 的任务", "skill": ["task_query"],
         "tool": "list_tasks", "arguments": {"project_id": "P1"}},
    ]
    result = prepare(items)
    assert result["errors"] == []
    cases = {case["query"]: case for case in result["cases"]}
    clarify = cases["帮我建任务"]["expected"]
    assert clarify["action"] == "clarify"
    assert clarify["missing_fields"] == ["title"]
    assert clarify["routes"] == [["task_query"]]
    no_action = cases["你好"]["expected"]
    assert no_action["action"] == "no_action"
    assert no_action["tool_calls"] == []
    tool_case = cases["查询 P1 的任务"]
    assert tool_case["expected"]["routes"] == [["task_query"]]
    assert tool_case["fixtures"][0]["name"] == "list_tasks"


def test_explicit_empty_fixtures_disables_auto_mock():
    item = {
        "query": "查询 P1 的任务",
        "skill": "task_query",
        "tool": "list_tasks",
        "arguments": {"project_id": "P1"},
        "fixtures": [],
    }
    result = prepare([item])
    assert result["errors"] == []
    assert result["cases"][0]["fixtures"] == []


def test_sample_dataset_is_valid_compact_input():
    from pathlib import Path

    samples = Path(__file__).resolve().parent.parent / "app" / "samples"
    items, fmt = dataset.parse_payload(
        (samples / "dataset.jsonl").read_bytes(), "dataset.jsonl")
    manifest = json.loads((samples / "skill_package" / "manifest.json")
                          .read_text(encoding="utf-8"))
    tools = json.loads((samples / "tool_schema.json").read_text(encoding="utf-8"))
    result = dataset.validate_and_prepare(
        items, manifest=manifest, tool_schema=tools,
        split_config={"optimize": 0.7, "validation": 0.2, "final_test": 0.1},
        seed=42)
    assert fmt == "jsonl"
    assert result["errors"] == []
    assert result["stats"]["valid"] == 45
    assert result["stats"]["splits"] == {
        "optimize": 33, "validation": 8, "final_test": 4}
    clarify = next(case for case in result["cases"]
                   if case["case_id"] == "opt_010")
    assert clarify["expected"]["action"] == "clarify"
    assert clarify["fixtures"], "clarify 基线调用仍应有模拟返回"
    missing = next(case for case in result["cases"]
                   if case["case_id"] == "fin_003")
    assert missing["fixtures"] == []


def test_wildcard_fixture_matches_any_business_tool():
    matcher = FixtureMatcher([{"name": "*", "match": {},
                               "response": {"ok": True}}])
    first = matcher.match("list_tasks", {"project_id": "P1"})
    second = matcher.match("query_stats", {"project_id": "P1"})
    assert first["matched"] and second["matched"]
    assert first["response"] == {"ok": True}
    assert second["tool"] == "query_stats"
