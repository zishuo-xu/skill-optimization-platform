"""Configurable argument comparison (compare-v2) and judge-model scoring."""

from __future__ import annotations

import copy
import json

import pytest

from app import event_types, judge as judge_mod, persistence, services
from app.evaluation import compare_rules_error, judgment_key, score_case
from tests.conftest import create_demo_project, environment_payload, run_sync

SCHEMA = [{
    "name": "edit",
    "parameters": {
        "type": "object",
        "required": ["color"],
        "properties": {
            "color": {"type": "string"},
            "strength": {"type": "integer", "minimum": 1, "maximum": 5},
            "method": {"type": "string", "enum": ["colorTransfer", "toneAdjust"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    },
}]
EXPECTED = {
    "action": "tool_call",
    "routes": [["image_color"]],
    "tool_calls": [{"name": "edit", "arguments": {
        "color": "蓝色调", "strength": 3, "method": "colorTransfer", "tags": ["冷色", "清新"]}}],
}


def actual(**arguments) -> dict:
    args = copy.deepcopy(EXPECTED["tool_calls"][0]["arguments"])
    args.update(arguments)
    for key, value in list(args.items()):
        if value is _DROP:
            del args[key]
    return {"case_id": "c1", "selected_skills": ["image_color"], "action": "tool_call",
            "tool_calls": [{"name": "edit", "arguments": args}],
            "capabilities": {"routing": True, "tool_arguments": True}}


_DROP = object()


def rules(mode: str, **extra) -> dict:
    return {"version": "compare-v2", "mode": mode, **extra}


def score(result, compare=None, expected=None, **kwargs):
    return score_case(expected or EXPECTED, result, compare, **kwargs)


# --------------------------------------------------------------------------- #
# code modes
# --------------------------------------------------------------------------- #
def test_strict_is_unchanged_default():
    assert score(actual())["tool_args"]["passed"] is True
    result = score(actual(color="蓝色调 "))
    assert result["tool_args"]["passed"] is False
    assert result["tool_args"]["compare"]["mode"] == "strict"


def test_lenient_normalises_values_but_not_meaning():
    passing = actual(color=" 蓝色调", strength="3", tags=["清新", "冷色"])
    assert score(passing, rules("lenient"))["tool_args"]["passed"] is True
    failing = score(actual(color="红色调"), rules("lenient"))
    assert failing["tool_args"]["passed"] is False
    assert failing["diagnostics"][0]["field_path"] == "$.color"


def test_keys_mode_ignores_values_and_value_schema_checks():
    result = score(actual(color="红色", strength=99, method="somethingElse", tags=["x"]),
                   rules("keys"))
    assert result["tool_args"]["passed"] is True
    missing = score(actual(color=_DROP), rules("keys"))
    assert missing["tool_args"]["passed"] is False
    assert {d["category"] for d in missing["diagnostics"]} == {"missing_arg"}


def test_extra_keys_follow_the_switch():
    extra = actual(note="多给的参数")
    assert score(extra, rules("lenient"))["tool_args"]["passed"] is True
    assert score(extra, rules("lenient", allow_extra_keys=False))["tool_args"]["passed"] is False
    assert score(extra, rules("strict"))["tool_args"]["passed"] is False
    assert score(extra, rules("strict", allow_extra_keys=True))["tool_args"]["passed"] is True


def test_field_rules_override_per_field_and_tool():
    compare = rules("keys", field_rules=[{"path": "$.method", "mode": "strict"}])
    assert score(actual(color="红色"), compare)["tool_args"]["passed"] is True
    assert score(actual(method="toneAdjust"), compare)["tool_args"]["passed"] is False
    other_tool = rules("keys", field_rules=[{"path": "$.method", "mode": "strict", "tool": "other"}])
    assert score(actual(method="toneAdjust"), other_tool)["tool_args"]["passed"] is True
    ignore = rules("strict", field_rules=[{"path": "$.tags", "mode": "ignore"}])
    assert score(actual(tags=_DROP), ignore)["tool_args"]["passed"] is True
    wildcard = rules("strict", field_rules=[{"path": "$.tags[*]", "mode": "keys"}])
    assert score(actual(tags=["a", "b"]), wildcard)["tool_args"]["passed"] is True


def test_case_rules_override_environment_rules():
    expected = dict(EXPECTED, compare=rules("strict"))
    assert score(actual(color="红色"), rules("keys"), expected=expected)["tool_args"]["passed"] is False


@pytest.mark.parametrize("bad, message", [
    ({"version": "compare-v2", "mode": "fuzzy"}, "mode"),
    ({"version": "compare-v1", "mode": "keys"}, "compare-v2"),
    ({"version": "compare-v2", "field_rules": [{"path": "", "mode": "keys"}]}, "path"),
    ({"version": "compare-v2", "field_rules": [{"path": "$.a", "mode": "x"}]}, "mode"),
    ({"version": "compare-v2", "allow_extra_keys": "yes"}, "allow_extra_keys"),
])
def test_invalid_rules_are_reported(bad, message):
    assert message in (compare_rules_error(bad) or "")


# --------------------------------------------------------------------------- #
# semantic mode (judge model)
# --------------------------------------------------------------------------- #
def test_semantic_keyword_and_lenient_hits_need_no_judge():
    expected = dict(EXPECTED, judge={"$.color": {"keywords": ["蓝"]}})
    result = score(actual(color="偏蓝的色调", strength="3"), rules("semantic"), expected=expected)
    assert result["tool_args"]["passed"] is True
    assert "pending_judgments" not in result


def test_semantic_two_pass_with_verdicts():
    first = score(actual(color="天空一样的颜色"), rules("semantic"))
    assert first["tool_args"]["passed"] is False
    [item] = first["pending_judgments"]
    assert item["path"] == "$.color" and item["expected"] == "蓝色调"
    assert item["key"] == judgment_key("$.color", "蓝色调", "天空一样的颜色", None)

    approved = score(actual(color="天空一样的颜色"), rules("semantic"),
                     judgments={item["key"]: {"passed": True, "reason": "天空色即蓝色调"}})
    assert approved["tool_args"]["passed"] is True and approved["joint"] is True
    assert approved["judgments"][0]["reason"] == "天空色即蓝色调"

    rejected = score(actual(color="天空一样的颜色"), rules("semantic"),
                     judgments={item["key"]: {"passed": False, "reason": "不是蓝色"}})
    assert rejected["tool_args"]["passed"] is False
    assert rejected["diagnostics"][0]["note"] == "评分模型：不是蓝色"


def test_semantic_keeps_structure_in_code():
    result = score(actual(color=_DROP), rules("semantic"))
    assert {d["category"] for d in result["diagnostics"]} == {"missing_arg"}
    assert "pending_judgments" not in result
    wrong_tool = dict(actual(), tool_calls=[{"name": "other", "arguments": {"color": "蓝色调"}}])
    assert score(wrong_tool, rules("semantic"))["tool_args"]["passed"] is False


def test_semantic_scalar_arrays_are_judged_as_one_value():
    first = score(actual(tags=["冷调", "小清新"]), rules("semantic"))
    [item] = first["pending_judgments"]
    assert item["path"] == "$.tags" and item["actual"] == ["冷调", "小清新"]


def test_semantic_without_judge_falls_back_to_lenient():
    result = score(actual(color="天空一样的颜色"), rules("semantic"), semantic_fallback=True)
    assert result["tool_args"]["passed"] is False
    assert result["tool_args"]["semantic_fallback"] is True
    assert "pending_judgments" not in result
    assert score(actual(color=" 蓝色调 "), rules("semantic"),
                 semantic_fallback=True)["tool_args"]["passed"] is True


# --------------------------------------------------------------------------- #
# judge module
# --------------------------------------------------------------------------- #
class FakeJudgeAdapter:
    def __init__(self, decide=None, *, exc=None, content=None):
        self.decide = decide or (lambda item: (True, "含义一致"))
        self.exc = exc
        self.content = content
        self.calls: list[dict] = []

    def invoke(self, messages, config, tools, emit=None):
        self.calls.append({"messages": messages, "config": config})
        if self.exc:
            raise self.exc
        items = json.loads(messages[1]["content"])["待判项目"]
        results = []
        for item in items:
            passed, reason = self.decide(item)
            results.append({"id": item["id"], "passed": passed, "reason": reason})
        content = self.content if self.content is not None else json.dumps(
            {"results": results}, ensure_ascii=False)
        return {"content": f"```json\n{content}\n```", "usage": {"input_tokens": 50, "output_tokens": 20},
                "tool_calls": [], "finish_reason": "stop", "model": "judge-x"}


JUDGE_CONFIG = {"provider": "openai_http", "base_url": "https://judge.example/v1",
                "model": "judge-x", "api_key_ref": "sk-judge"}


def test_judge_parses_caches_and_reuses_verdicts():
    item = score(actual(color="天空一样的颜色"), rules("semantic"))["pending_judgments"][0]
    adapter = FakeJudgeAdapter()
    judge = judge_mod.ValueJudge(JUDGE_CONFIG, adapter=adapter)
    verdicts, stats = judge.judge([item], query="把图调成蓝色")
    assert verdicts[item["key"]]["passed"] is True
    assert stats == {"items": 1, "cached": 0, "judged": 1, "passed": 1,
                     "usage": {"input_tokens": 50, "output_tokens": 20}}
    request = json.loads(adapter.calls[0]["messages"][1]["content"])
    assert request["用户原始请求"] == "把图调成蓝色"
    assert adapter.calls[0]["config"]["temperature"] == 0

    again = judge_mod.ValueJudge(JUDGE_CONFIG, adapter=FakeJudgeAdapter(exc=AssertionError("no call")))
    verdicts2, stats2 = again.judge([item])
    assert verdicts2[item["key"]]["passed"] is True and stats2["cached"] == 1

    other_model = judge_mod.ValueJudge(dict(JUDGE_CONFIG, model="judge-y"),
                                       adapter=FakeJudgeAdapter(lambda _: (False, "不同")))
    assert other_model.judge([item])[0][item["key"]]["passed"] is False


def test_judge_rejects_incomplete_or_invalid_answers():
    item = score(actual(color="天空色"), rules("semantic"))["pending_judgments"][0]
    with pytest.raises(judge_mod.JudgeError):
        judge_mod.ValueJudge(JUDGE_CONFIG, adapter=FakeJudgeAdapter(content='{"results": []}')).judge([item])
    with pytest.raises(judge_mod.JudgeError):
        judge_mod.ValueJudge(JUDGE_CONFIG, adapter=FakeJudgeAdapter(content="看起来不错")).judge([item])
    assert persistence.get_judgments([item["key"]]) == {}


def test_keywords_and_requirement_reach_the_judge():
    expected = dict(EXPECTED, judge={"$.color": {"keywords": ["蓝"], "requirement": "表示蓝色系即可"}})
    item = score(actual(color="sky"), rules("semantic"), expected=expected)["pending_judgments"][0]
    messages = judge_mod.build_messages([item])
    payload = json.loads(messages[1]["content"])["待判项目"][0]
    assert payload["关键词"] == ["蓝"] and payload["要求"] == "表示蓝色系即可"


# --------------------------------------------------------------------------- #
# pipeline + environment
# --------------------------------------------------------------------------- #
def _semantic_project(monkeypatch, adapter):
    monkeypatch.setattr(judge_mod.ValueJudge, "adapter", property(lambda self: adapter))
    return create_demo_project(env_overrides={"scorer_config": {
        "version": "score-v1",
        "compare": {"version": "compare-v2", "mode": "semantic"},
        "judge": dict(JUDGE_CONFIG),
    }})


def _complete(project_id: str, mode: str = "baseline") -> dict:
    run = services.create_run({"project_id": project_id, "mode": mode})
    return run_sync(run["id"])


def test_pipeline_uses_judge_and_caches_across_runs(monkeypatch):
    strict = create_demo_project()
    strict_run = _complete(strict["project"]["id"])
    strict_a = strict_run["metrics"]["baseline"]["tool_args"]["passed"]

    adapter = FakeJudgeAdapter()
    demo = _semantic_project(monkeypatch, adapter)
    run = _complete(demo["project"]["id"])
    assert run["status"] == "completed"
    semantic_a = run["metrics"]["baseline"]["tool_args"]["passed"]
    assert semantic_a >= strict_a
    assert adapter.calls, "value differences should reach the judge"
    events = persistence.list_events(run["id"], event_type=event_types.JUDGE_COMPLETED, limit=2000)
    assert events and events[0]["payload"]["verdicts"]
    judged = [row for row in persistence.get_case_results_for_run(run["id"])
              if (row.get("scores") or {}).get("judgments")]
    assert judged and all("pending_judgments" not in row["scores"] for row in judged)
    assert run["metrics"]["baseline"]["execution"]["tokens"] > 0

    calls_before = len(adapter.calls)
    # Identical outputs on a fresh run get the cached verdicts: no new calls.
    services.create_environment(demo["project"]["id"], environment_payload(scorer_config={
        "version": "score-v1", "compare": {"version": "compare-v2", "mode": "semantic"},
        "judge": dict(JUDGE_CONFIG)}), status="published", set_current=True)
    rerun = _complete(demo["project"]["id"])
    assert rerun["metrics"]["baseline"]["tool_args"]["passed"] == semantic_a
    assert len(adapter.calls) == calls_before


def test_judge_failure_makes_case_infrastructure(monkeypatch):
    from app.adapters.base import AdapterError
    demo = _semantic_project(monkeypatch, FakeJudgeAdapter(
        exc=AdapterError("连接被拒绝", code="network_error")))
    run = _complete(demo["project"]["id"])
    infra = [row for row in persistence.get_case_results_for_run(run["id"])
             if (row.get("scores") or {}).get("status") == "infra"]
    assert infra
    issues = services.run_view(run["id"])["issues_summary"]["items"]
    assert any(item["type"] == event_types.JUDGE_FAILED for item in issues)


def test_semantic_without_judge_warns_in_preflight():
    demo = create_demo_project(env_overrides={"scorer_config": {
        "version": "score-v1", "compare": {"version": "compare-v2", "mode": "semantic"}}})
    run = _complete(demo["project"]["id"])
    issues = services.run_view(run["id"])["issues_summary"]["items"]
    assert any(item["error_code"] == "judge_not_configured" for item in issues)


def test_judge_credentials_are_masked_inherited_and_probe_target(client, monkeypatch):
    demo = create_demo_project(env_overrides={"scorer_config": {
        "version": "score-v1", "compare": {"version": "compare-v2", "mode": "semantic"},
        "judge": dict(JUDGE_CONFIG)}})
    env = demo["environment"]
    public = client.get(f"/api/v3/environments/{env['id']}").json()
    assert public["scorer_config"]["judge"]["api_key_ref"] == "***"
    assert public["credential_status"]["judge_config"]["api_key_ref"] is True
    versions = client.get(f"/api/v3/projects/{demo['project']['id']}/versions").text
    assert "sk-judge" not in versions

    draft = services.create_environment(
        demo["project"]["id"], environment_payload(scorer_config=public["scorer_config"]),
        parent_id=env["id"], status="draft")
    assert draft["scorer_config"]["judge"]["api_key_ref"] == "sk-judge"

    with pytest.raises(Exception):
        services.create_environment(
            demo["project"]["id"], environment_payload(scorer_config={
                "version": "score-v1", "judge": {"provider": "flow", "model": "x"}}),
            status="draft")

    seen = {}

    def fake_probe(config, *, target, input_text=None):
        seen.update(config=config, target=target)
        return {"ok": True, "performed": True, "target": target}

    monkeypatch.setattr("app.connection_probe.probe_connection", fake_probe)
    response = client.post("/api/v3/environments/test-connection",
                           json={"target": "judge", "environment_id": env["id"]})
    assert response.status_code == 200, response.text
    assert seen["target"] == "judge" and seen["config"]["api_key_ref"] == "sk-judge"
