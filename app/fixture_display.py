"""Read-only fixture diagnostics for humans; never used as model input."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .fixtures import deep_match
from .model_output import redact_output


def _differences(condition: Any, actual: Any, path: str = "$", present: bool = True, keys: tuple = ()):
    if present and deep_match(condition, actual):
        return
    if isinstance(condition, Mapping) and isinstance(actual, Mapping) and present:
        for key, value in condition.items():
            yield from _differences(value, actual.get(key), f"{path}.{key}", key in actual, (*keys, key))
    elif isinstance(condition, list) and isinstance(actual, list) and len(condition) == len(actual) and present:
        for index, value in enumerate(condition):
            yield from _differences(value, actual[index], f"{path}[{index}]", True, (*keys, index))
    else:
        yield path, actual, condition, present, keys


def _declared_values(case: Mapping, tool: str, keys: tuple) -> list:
    """Collect declared alternatives; never guess the ordinal of repeated calls."""
    expected = case.get("expected") or {}
    candidates = [expected, *(expected.get("variants") or [])]
    values = []
    for candidate in candidates:
        for call in candidate.get("tool_calls") or []:
            if call.get("name") != tool:
                continue
            prefix = tuple(str(call.get("arguments_path") or "").split(".")) if call.get("arguments_path") else ()
            if keys[:len(prefix)] != prefix:
                continue
            value = call.get("arguments") or {}
            try:
                for key in keys[len(prefix):]:
                    value = value[key]
            except (KeyError, IndexError, TypeError):
                continue
            if not any(type(value) is type(item) and value == item for item in values):
                values.append(value)
    return values


def _preview(value: Any, path: str, credentials: Mapping) -> str:
    safe = redact_output({path: value}, credentials)[path]
    text = json.dumps(safe, ensure_ascii=False)
    return text if len(text) <= 320 else text[:320] + "…（已截断）"


def project_event(event: Mapping, case: Mapping | None, credentials: Mapping) -> dict:
    """Preserve the original event and append a derived display_issue."""
    payload = event.get("payload") or {}
    original = payload.get("issue") or {}
    if event.get("type") != "fixture_miss" or not original:
        return dict(event)
    detail = original.get("technical_detail") or {}
    diagnosis = detail.get("fixture_diagnosis") or {}
    reason = diagnosis.get("reason")
    tool = payload.get("tool") or detail.get("tool") or "未知工具"
    arguments = payload.get("arguments", detail.get("arguments"))
    issue = dict(original)
    issue["title"] = {
        "arguments_mismatch": "工具调用参数不匹配",
        "empty_fixtures": "案例未配置模拟返回",
        "tool_name_mismatch": "调用的工具没有对应模拟返回",
        "fixtures_consumed": "本次调用的模拟返回次数已用尽",
    }.get(reason, "工具调用未匹配模拟返回")
    issue["impact"] = "当前案例未取得本次工具返回并按能力失败计分；不代表整轮运行异常。未访问真实业务服务，无法判断真实业务结果。"
    issue["diagnostic_kind"] = reason or "unknown"
    issue["comparison_rows"] = []
    if reason == "arguments_mismatch":
        paths = sorted({path for values in (diagnosis.get("mismatch_paths") or {}).values() for path in values})
        issue["message"] = f"工具 {tool} 的调用参数不匹配模拟返回条件" + (f"（字段：{', '.join(paths[:8])}）" if paths else "") + "；尚未取得业务返回。"
        issue["next_action"] = "先在案例对比中核对实际调用、业务预期与 Skill 规则。调用错误时修正 Skill；调用符合规则而模拟条件错误时修正 fixture 并发布新数据集。不要仅为消除报错放宽匹配条件。"
        fixtures = (case or {}).get("fixtures") or []
        if isinstance(arguments, Mapping):
            for index in diagnosis.get("candidate_indices") or []:
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(fixtures):
                    continue
                fixture = fixtures[index]
                if fixture.get("name") not in {tool, "*"}:
                    continue
                recorded_paths = (diagnosis.get("mismatch_paths") or {}).get(str(index)) or []
                for path, actual, condition, present, keys in _differences(fixture.get("match") or {}, arguments):
                    if path not in recorded_paths or len(issue["comparison_rows"]) >= 8:
                        continue
                    row = {
                        "fixture_index": index, "field_path": path,
                        "actual": _preview(actual, path, credentials) if present else "未提供该字段",
                        "condition": _preview(condition, path, credentials),
                    }
                    declared = _declared_values(case or {}, tool, keys)
                    if declared:
                        row["expected"] = (_preview(declared[0], path, credentials) if len(declared) == 1
                                           else "声明值（可能来自不同调用/备选路线）：" + _preview(declared, path, credentials))
                    issue["comparison_rows"].append(row)
        issue["comparison_note"] = (
            "对照来自本次运行绑定的数据集（最多展示 8 项）。业务预期列展示案例声明；模拟匹配条件不是评分标准答案。最终按评分规则及完整调用序列判断，不应直接把模拟条件当作正确调用。"
            if issue["comparison_rows"] else
            "当前记录无法可靠恢复两边的值；请在案例对比中查看实际参数，并核对本次运行数据集中的 fixture.match。")
    elif reason == "tool_name_mismatch":
        issue["next_action"] = "先对照业务预期与 Skill 规则确认工具名；模型选错工具时修正 Skill，调用正确但数据未覆盖时补充对应模拟返回。"
    elif reason == "fixtures_consumed":
        issue["next_action"] = "先检查模型是否重复调用；业务预期允许重复时，再核对 consume=once 和模拟返回次数。"
    elif reason == "empty_fixtures":
        issue["next_action"] = "核对案例是否需要业务工具；需要时补充对应模拟返回并发布新数据集。"
    return {**event, "display_issue": redact_output(issue, credentials)}
