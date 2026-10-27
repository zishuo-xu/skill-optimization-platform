"""Auto-generated fixtures cover every declared expected tool variant."""

from __future__ import annotations

import pytest

from app import dataset, persistence, services
from app.fixtures import FixtureMatcher


def _prepare(item):
    report = dataset.validate_and_prepare(
        [item], manifest={"version": 1, "subskills": []},
        split_config={"optimize": 1, "validation": 0, "final_test": 0},
    )
    assert report["errors"] == []
    return report["cases"][0]


def test_auto_fixtures_include_top_level_and_variant_tools():
    case = _prepare({
        "case_id": "variant-tools",
        "query": "执行操作",
        "expected": {
            "action": "tool_call",
            "tool_calls": [{"name": "tool_a", "arguments": {}}],
            "variants": [{"tool_calls": [{"name": "tool_b", "arguments": {}}]}],
        },
    })

    names = [fixture["name"] for fixture in case["fixtures"]]
    assert names == ["tool_a", "tool_b"]
    matcher = FixtureMatcher(case["fixtures"])
    assert matcher.resolve("tool_a", {}) is not None
    assert matcher.resolve("tool_b", {}) is not None


def test_explicit_empty_fixtures_still_disable_auto_generation_for_variants():
    case = _prepare({
        "case_id": "variant-empty-fixtures",
        "query": "执行操作",
        "expected": {
            "action": "tool_call",
            "variants": [{"tool_calls": [{"name": "tool_b", "arguments": {}}]}],
        },
        "fixtures": [],
    })
    assert case["fixtures"] == []


def test_mock_still_generates_one_wildcard_for_variants():
    case = _prepare({
        "case_id": "variant-mock",
        "query": "执行操作",
        "expected": {
            "action": "tool_call",
            "variants": [{"tool_calls": [{"name": "tool_b", "arguments": {}}]}],
        },
        "mock": {"code": 0},
    })
    assert case["fixtures"] == [{
        "name": "*", "match": {}, "response": {"code": 0}, "consume": "always",
    }]


def test_invalid_tool_calls_and_variants_report_validation_errors_without_crashing():
    report = dataset.validate_and_prepare([{
        "case_id": "invalid-variant-shape",
        "query": "执行操作",
        "expected": {
            "action": "tool_call",
            "tool_calls": {"name": "tool_a"},
            "variants": [{"tool_calls": {"name": "tool_b"}}],
        },
    }], manifest={"version": 1, "subskills": []},
        split_config={"optimize": 1, "validation": 0, "final_test": 0})
    codes = {error["code"] for error in report["errors"]}
    assert {"invalid_tool_calls", "invalid_tool_calls"} <= codes


def test_variant_fixtures_survive_service_persistence():
    project = persistence.create_project("variant-fixtures")
    dataset_version = services.create_dataset(project["id"], {
        "source_name": "variant.json",
        "cases": [{
            "case_id": "variant-persisted",
            "query": "执行操作",
            "expected": {
                "action": "tool_call",
                "tool_calls": [{"name": "tool_a", "arguments": {}}],
                "variants": [{"tool_calls": [{"name": "tool_b", "arguments": {}}]}],
            },
        }],
    })
    stored = persistence.get_case(dataset_version["id"], "variant-persisted")
    assert [fixture["name"] for fixture in stored["fixtures"]] == ["tool_a", "tool_b"]


@pytest.mark.parametrize("bad_variants", [1, True, "not-an-array"])
def test_invalid_variants_type_is_a_validation_error(bad_variants):
    report = dataset.validate_and_prepare([{
        "query": "example", "expected": {"action": "tool_call", "variants": bad_variants},
    }])
    assert any(error["code"] == "invalid_variants" for error in report["errors"])


def test_variant_only_auto_fixtures_deduplicate_and_honor_per_tool_mock():
    case = _prepare({"query": "example", "expected": {"action": "tool_call", "variants": [
        {"tool_calls": [{"name": "imageEditWorkflow", "arguments": {}}]},
        {"tool_calls": [{"name": "imageEditWorkflow", "arguments": {"x": 1}}]},
    ]}, "mocks": {"imageEditWorkflow": {"result": "fixture-owned"}}})
    assert case["fixtures"] == [{"name": "imageEditWorkflow", "match": {},
                                 "response": {"result": "fixture-owned"}, "consume": "always"}]
