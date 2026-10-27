"""Dataset import, validation, deterministic split assignment and coverage.

Preflight pipeline (``docs/CONTRACTS.md`` §2.5, requirement §12–§14):

1. :func:`parse_payload` decodes ``.jsonl`` / ``.json`` payloads (BOM and blank
   lines tolerated). Malformed JSONL lines are *collected* as parse-error items
   carrying their 1-based line number so the UI can show every bad row at once;
   a malformed ``.json`` document raises :class:`ValidationError` with the line
   number from :class:`json.JSONDecodeError`.
2. :func:`validate_and_prepare` validates every case, generates a stable
   ``case_id`` via :func:`app.ids.stable_case_id` when missing, reports errors
   with ``line`` / ``case_id`` / ``code`` / ``message`` / ``field_path``,
   emits warnings (duplicate query, missing final test, ...), assigns splits and
   returns coverage statistics.
3. :func:`assign_splits` groups cases by ``group_id`` (a group never crosses
   splits) and assigns the default 70/20/10 split with a fixed seed. Small
   datasets may end up with optimize+validation only; a ``no_final_test``
   warning is returned in that case.

Everything in this module is pure and in-memory: no database, network, file
system or global state.
"""

from __future__ import annotations

import json
import re
import random
from collections import Counter
from typing import Any, Callable, Iterable, Mapping, Sequence

from .errors import ValidationError
from .evaluation import compare_rules_error, normalise_judge_criteria
from .ids import stable_case_id

SPLITS = ("optimize", "validation", "final_test")
DEFAULT_SPLIT_RATIOS = {"optimize": 0.7, "validation": 0.2, "final_test": 0.1}
DEFAULT_SEED = 42
DEFAULT_MIN_CASES_FOR_FINAL_TEST = 10
CONSUME_MODES = ("always", "once")
ACTIONS = ("tool_call", "clarify", "no_action")
CALL_MATCH_MODES = ("ordered_exact", "unordered")
COMPARE_RULE_VERSION = "compare-v1"

_Add = Callable[[str | None, str, str], None]


# --------------------------------------------------------------------------- #
# payload parsing
# --------------------------------------------------------------------------- #
def _decode(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                "文件不是合法的 UTF-8 文本", code="encoding_error",
                details={"position": exc.start, "reason": exc.reason},
            ) from exc
    if isinstance(raw, str):
        return raw.lstrip("\ufeff")
    raise ValidationError("导入内容必须是文本或字节", code="invalid_payload")


def _detect_format(filename: str) -> str:
    name = (filename or "").strip().lower()
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return "jsonl"
    if name.endswith(".json"):
        return "json"
    if not name:
        return ""
    raise ValidationError(
        f"仅支持 .jsonl 与 .json 文件，收到: {filename}",
        code="unsupported_format", field_path="file",
        details={"filename": filename},
    )


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            items.append({
                "_line": lineno,
                "_parse_error": {
                    "line": lineno,
                    "code": "parse_error",
                    "message": f"第 {lineno} 行 JSON 解析失败：{exc.msg}",
                },
            })
            continue
        if not isinstance(value, dict):
            items.append({
                "_line": lineno,
                "_parse_error": {
                    "line": lineno,
                    "code": "parse_error",
                    "message": f"第 {lineno} 行必须是 JSON 对象",
                },
            })
            continue
        value = dict(value)
        value["_line"] = lineno
        items.append(value)
    return items


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    try:
        root = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"JSON 解析失败（第 {exc.lineno} 行，第 {exc.colno} 列）：{exc.msg}",
            code="parse_error", field_path="file",
            details={"line": exc.lineno, "column": exc.colno},
        ) from exc
    if isinstance(root, Mapping):
        cases = root.get("cases")
        if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
            raise ValidationError(
                "JSON 根节点必须是案例数组（或 {\"cases\": [...]}）",
                code="invalid_structure", field_path="file",
            )
        root = list(cases)
    if not isinstance(root, Sequence) or isinstance(root, (str, bytes)):
        raise ValidationError("JSON 根节点必须是案例数组", code="invalid_structure",
                              field_path="file")
    items: list[dict[str, Any]] = []
    for index, value in enumerate(root):
        lineno = index + 1
        if not isinstance(value, dict):
            items.append({
                "_line": lineno,
                "_parse_error": {
                    "line": lineno,
                    "code": "parse_error",
                    "message": f"第 {lineno} 个案例必须是 JSON 对象",
                },
            })
            continue
        value = dict(value)
        value["_line"] = lineno
        items.append(value)
    return items


def parse_payload(raw: bytes | str,
                  filename: str = "") -> tuple[list[dict[str, Any]], str]:
    """Parse a JSONL/JSON payload.

    Returns ``(items, format)`` where ``format`` is ``"jsonl"`` or ``"json"``.
    Each normalised item is a dict with a private ``_line`` key; unparseable
    JSONL rows become ``{"_line", "_parse_error"}`` placeholder items so
    preflight can report every bad line instead of only the first one.
    """

    fmt = _detect_format(filename)
    text = _decode(raw)
    if not fmt:
        stripped = text.lstrip()
        fmt = "json" if stripped.startswith("[") else "jsonl"
    if fmt == "jsonl":
        return _parse_jsonl(text), "jsonl"
    return _parse_json_array(text), "json"


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _manifest_skill_ids(manifest: Any) -> list[str]:
    if manifest is None:
        return []
    raw = manifest
    if isinstance(manifest, Mapping):
        raw = manifest.get("subskills")
        if raw is None:
            raw = manifest.get("skills")
        if raw is None:
            raw = manifest.get("references")
    if not _is_sequence(raw):
        return []
    result: list[str] = []
    for entry in raw:
        if isinstance(entry, str) and entry:
            skill_id = entry
        elif isinstance(entry, Mapping):
            skill_id = entry.get("skill_id") or entry.get("id")
        else:
            continue
        if isinstance(skill_id, str) and skill_id and skill_id not in result:
            result.append(skill_id)
    return result


def _skill_aliases(manifest: Any) -> dict[str, str]:
    """Map reference paths / basenames to stable skill_id (unique entries only)."""
    if not isinstance(manifest, Mapping):
        return {}
    raw = manifest.get("subskills")
    if not _is_sequence(raw):
        return {}
    candidates: dict[str, set[str]] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        skill_id = entry.get("skill_id") or entry.get("id")
        path = entry.get("path")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        names = {skill_id}
        if isinstance(path, str) and path:
            norm = path[2:] if path.startswith("./") else path
            names.add(norm)
            if norm.endswith((".md", ".markdown")):
                names.add(norm.rsplit(".", 1)[0])
            names.add(norm.split("/")[-1])
            names.add(norm.split("/")[-1].rsplit(".", 1)[0])
        for name in names:
            candidates.setdefault(name, set()).add(skill_id)
    return {name: next(iter(ids)) for name, ids in candidates.items()
            if len(ids) == 1}


def _split_ratios(config: Any) -> dict[str, float]:
    ratios = dict(DEFAULT_SPLIT_RATIOS)
    if isinstance(config, Mapping):
        nested = (config.get("ratios") or config.get("proportions")
                  or config.get("split_ratios"))
        source = nested if isinstance(nested, Mapping) else config
        for key in SPLITS:
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and value >= 0:
                ratios[key] = float(value)
    total = sum(ratios.values())
    if total <= 0:
        return dict(DEFAULT_SPLIT_RATIOS)
    return {key: value / total for key, value in ratios.items()}


def _min_cases_for_final_test(config: Any) -> int:
    if isinstance(config, Mapping):
        value = config.get("min_cases_for_final_test")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return DEFAULT_MIN_CASES_FOR_FINAL_TEST


def _split_targets(total: int, group_count: int, ratios: Mapping[str, float],
                   min_final_cases: int) -> dict[str, int]:
    optimize = max(1, int(round(total * ratios["optimize"])))
    validation = (max(1, int(round(total * ratios["validation"])))
                  if total >= 2 else 0)
    final = 0
    if (total >= min_final_cases and group_count >= 3
            and ratios["final_test"] > 0):
        final = max(1, int(round(total * ratios["final_test"])))
    while optimize + validation + final > total:
        if final > 0:
            final -= 1
        elif validation > 0:
            validation -= 1
        else:
            optimize = max(1, min(optimize, total))
            break
    if optimize + validation + final < total and final == 0:
        optimize = total - validation
    return {"optimize": optimize, "validation": validation, "final_test": final}


def _choose_split(counts: Mapping[str, int], targets: Mapping[str, int],
                  remaining_after: int) -> str:
    validation_needed = (1 if targets["validation"] > 0
                         and counts["validation"] == 0 else 0)
    final_needed = (1 if targets["final_test"] > 0
                    and counts["final_test"] == 0 else 0)
    if counts["optimize"] < targets["optimize"] \
            and remaining_after >= validation_needed + final_needed:
        return "optimize"
    if targets["validation"] > 0 and counts["validation"] < targets["validation"]:
        reserve = final_needed
        if remaining_after >= reserve:
            return "validation"
    if targets["final_test"] > 0:
        return "final_test"
    if targets["validation"] > 0:
        return "validation"
    return "optimize"


def _case_expected(case: Mapping[str, Any]) -> Mapping[str, Any]:
    expected = case.get("expected")
    return expected if isinstance(expected, Mapping) else {}


def _case_routes(case: Mapping[str, Any]) -> list[list[str]]:
    routes = _case_expected(case).get("routes")
    if not _is_sequence(routes):
        return []
    result: list[list[str]] = []
    for route in routes:
        if isinstance(route, str):
            result.append([route])
        elif _is_sequence(route):
            result.append([item for item in route if isinstance(item, str)])
    return result


def _case_tool_calls(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = _case_expected(case).get("tool_calls")
    if not _is_sequence(calls):
        return []
    return [call for call in calls if isinstance(call, Mapping)]


def _error(line: int | None, case_id: str | None, code: str, message: str,
           field_path: str | None) -> dict[str, Any]:
    return {"line": line, "case_id": case_id, "code": code,
            "message": message, "field_path": field_path}


def _warning(line: int | None, code: str, message: str) -> dict[str, Any]:
    return {"line": line, "code": code, "message": message}


# --------------------------------------------------------------------------- #
# case validation
# --------------------------------------------------------------------------- #
def _normalise_case_id(raw_id: Any, query: str, group_id: str) -> str:
    if raw_id is None:
        return stable_case_id(query, group_id)
    if isinstance(raw_id, str):
        value = raw_id.strip()
        return value or stable_case_id(query, group_id)
    return ""


def _validate_routes_value(routes: Any, field_path: str,
                           skill_ids: Sequence[str], add: _Add,
                           line: int | None, case_id: str | None,
                           skill_aliases: Mapping[str, str] | None = None) -> list[list[str]]:
    result: list[list[str]] = []
    if routes is None:
        return result
    if not _is_sequence(routes):
        add(field_path, "invalid_routes", "routes 必须是二维字符串数组")
        return result
    for route_index, route in enumerate(routes):
        route_path = f"{field_path}[{route_index}]"
        if isinstance(route, str):
            add(route_path, "invalid_routes", "每条路线必须是 skill_id 字符串数组")
            continue
        if not _is_sequence(route) or not route:
            add(route_path, "invalid_routes", "每条路线必须是非空数组")
            continue
        normalised: list[str] = []
        for skill_index, skill_id in enumerate(route):
            skill_path = f"{route_path}[{skill_index}]"
            if not isinstance(skill_id, str) or not skill_id:
                add(skill_path, "invalid_routes", "skill_id 必须是非空字符串")
                continue
            resolved = skill_id
            if skill_ids and resolved not in skill_ids:
                alias = (skill_aliases or {}).get(skill_id)
                if alias is None:
                    norm = skill_id[2:] if skill_id.startswith("./") else skill_id
                    alias = (skill_aliases or {}).get(norm)
                if alias is not None and alias in skill_ids:
                    resolved = alias
                else:
                    add(skill_path, "unknown_skill_id",
                        f"未知的 skill_id: {skill_id}（可写 manifest skill_id 或 reference 路径）")
                    continue
            normalised.append(resolved)
        if len(normalised) == len(route):
            result.append(normalised)
    return result


def _validate_calls_value(calls: Any, field_path: str,
                          add: _Add, line: int | None,
                          case_id: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if calls is None:
        return result
    if not _is_sequence(calls):
        add(field_path, "invalid_tool_calls", "tool_calls 必须是数组")
        return result
    for index, call in enumerate(calls):
        call_path = f"{field_path}[{index}]"
        if not isinstance(call, Mapping):
            add(call_path, "invalid_tool_call", "工具调用必须是 JSON 对象")
            continue
        name = call.get("name") or call.get("functionName")
        if not isinstance(name, str) or not name:
            add(f"{call_path}.name", "invalid_tool_call",
                "工具调用缺少 name/functionName")
            continue
        arguments = call.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            add(f"{call_path}.arguments", "invalid_tool_arguments",
                "arguments 必须是 JSON 对象")
            continue
        arguments_path = call.get("arguments_path")
        normalised_path = None
        if arguments_path is not None:
            if not isinstance(arguments_path, str) or not arguments_path.strip():
                add(f"{call_path}.arguments_path", "invalid_arguments_path",
                    "arguments_path 必须是非空字符串")
                continue
            normalised_path = arguments_path.strip()
            while normalised_path.startswith("$"):
                normalised_path = normalised_path[1:]
            normalised_path = normalised_path.lstrip(".")
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$",
                            normalised_path):
                add(f"{call_path}.arguments_path", "invalid_arguments_path",
                    "arguments_path 必须是点分字段路径，如 args")
                continue
        entry = {"name": name, "arguments": dict(arguments)}
        if normalised_path:
            entry["arguments_path"] = normalised_path
        result.append(entry)
    return result


def _validate_fixtures(fixtures: Any, line: int | None,
                       case_id: str | None,
                       add: _Add) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if fixtures is None:
        return result
    if not _is_sequence(fixtures):
        add("fixtures", "invalid_fixtures", "fixtures 必须是数组")
        return result
    for index, fixture in enumerate(fixtures):
        fixture_path = f"fixtures[{index}]"
        if not isinstance(fixture, Mapping):
            add(fixture_path, "invalid_fixture", "fixture 必须是 JSON 对象")
            continue
        name = fixture.get("name")
        if not isinstance(name, str) or not name:
            add(f"{fixture_path}.name", "invalid_fixture", "fixture 缺少 name")
            continue
        match = fixture.get("match")
        if match is None:
            match = {}
        if not isinstance(match, Mapping):
            add(f"{fixture_path}.match", "invalid_fixture_match",
                "fixture.match 必须是 JSON 对象")
            continue
        consume = fixture.get("consume", "always")
        if consume not in CONSUME_MODES:
            add(f"{fixture_path}.consume", "invalid_consume",
                f"consume 必须是 always/once，收到: {consume!r}")
            continue
        result.append({"name": name, "match": dict(match),
                       "response": fixture.get("response"), "consume": consume})
    return result


def _route_entry_value(entry: Any) -> str | None:
    if isinstance(entry, str) and entry.strip():
        return entry.strip()
    if isinstance(entry, Mapping):
        for key in ("path", "skill_id", "skillId", "skill"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _as_routes(value: Any) -> Any:
    """Normalise ``route``/``skill``/flat ``routes`` shorthand to nested routes."""
    if value is None:
        return None
    if isinstance(value, str):
        return [[value]] if value else []
    if isinstance(value, Mapping):
        single = _route_entry_value(value)
        return [[single]] if single else []
    if not _is_sequence(value):
        return value
    items = list(value)
    if not items:
        return []
    if all(isinstance(item, str) for item in items):
        return [[item for item in items if item]]
    if all(_is_sequence(item) for item in items):
        routes: list[list[str]] = []
        for route in items:
            values = [_route_entry_value(entry) if isinstance(entry, Mapping) else entry
                      for entry in route]
            routes.append([entry for entry in values
                           if isinstance(entry, str) and entry])
        return routes
    return value


def _normalise_tool_entry(entry: Any, fallback_arguments: Any = None) -> Any:
    if isinstance(entry, str):
        return {"name": entry, "arguments": dict(fallback_arguments or {})}
    if not isinstance(entry, Mapping):
        return entry
    name = entry.get("name") or entry.get("functionName") or entry.get("tool")
    arguments = entry.get("arguments")
    scoped_args = entry.get("args")
    arguments_path = entry.get("arguments_path")
    if arguments is None and isinstance(scoped_args, Mapping) \
            and not isinstance(arguments_path, str):
        arguments = scoped_args
        arguments_path = "args"
    if arguments is None and fallback_arguments is not None:
        arguments = fallback_arguments
    if arguments is None:
        arguments = {}
    if isinstance(name, str) and name:
        call = {"name": name, "arguments": arguments}
        if isinstance(arguments_path, str) and arguments_path.strip():
            call["arguments_path"] = arguments_path.strip()
        return call
    return entry


def _normalise_tool_list(value: Any, fallback_arguments: Any = None) -> Any:
    if not _is_sequence(value):
        return value
    return [_normalise_tool_entry(entry, fallback_arguments) for entry in value]


def _auto_fixtures(calls: Any, raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    mocks = raw.get("mocks")
    has_default_mock = "mock" in raw
    default_response = raw["mock"] if has_default_mock else {"ok": True}
    names: list[str] = []
    if _is_sequence(calls):
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            name = call.get("name") or call.get("functionName")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    fixtures: list[dict[str, Any]] = []
    if has_default_mock:
        # One wildcard fixture covers every business tool, including wrong-route
        # calls; per-tool mocks are kept as higher-priority overrides.
        if isinstance(mocks, Mapping):
            for name, response in mocks.items():
                if isinstance(name, str) and name:
                    fixtures.append({"name": name, "match": {},
                                     "response": response, "consume": "always"})
        fixtures.append({"name": "*", "match": {},
                         "response": default_response, "consume": "always"})
        return fixtures
    if isinstance(mocks, Mapping):
        for name in mocks.keys():
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    for name in names:
        response = default_response
        if isinstance(mocks, Mapping) and name in mocks:
            response = mocks[name]
        fixtures.append({"name": name, "match": {},
                         "response": response, "consume": "always"})
    return fixtures


def _normalise_fixtures_shorthand(value: Any) -> Any:
    if isinstance(value, Mapping):
        fixture_keys = {"name", "match", "response", "consume"}
        if fixture_keys & set(value.keys()):
            # Looks like a single malformed fixture object rather than a
            # tool-name -> response map; keep it for the validator to reject.
            if isinstance(value.get("name"), str) and (
                    "response" in value or "match" in value or "consume" in value):
                return [dict(value)]
            return value
        return [{"name": name, "match": {}, "response": response,
                 "consume": "always"}
                for name, response in value.items()]
    return value


def _normalise_compact_case(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Expand compact dataset shorthand into the canonical case shape.

    Compact example::

        {"query": "...", "skill": "references/task_query.md",
         "tool": {"functionName": "list_tasks", "arguments": {"project_id": "P1"}},
         "mock": {"tasks": []}}

    The canonical ``expected`` / ``fixtures`` fields remain fully supported and
    take precedence whenever both forms are present.
    """
    if not isinstance(raw, Mapping):
        return raw
    data = dict(raw)
    expected_raw = data.get("expected")
    if expected_raw is not None and not isinstance(expected_raw, Mapping):
        return data
    compact_keys = {"route", "skill", "tool", "tools", "arguments",
                    "missing", "mock", "mocks", "action", "call_match",
                    "route_ordered", "variants", "compare", "judge"}
    has_compact = any(key in data for key in compact_keys)
    if expected_raw is None and not has_compact:
        return data
    exp = dict(expected_raw or {})

    # ---- routes -------------------------------------------------------
    route_source = None
    if "routes" not in exp and "route" not in exp and "skill" not in exp:
        for key in ("route", "skill", "routes"):
            if key in data:
                route_source = data[key]
                break
    if "routes" not in exp and route_source is not None:
        exp["routes"] = _as_routes(route_source)
    elif "routes" not in exp:
        for key in ("route", "skill"):
            if key in exp:
                exp["routes"] = _as_routes(exp[key])
                break
    elif _is_sequence(exp.get("routes")):
        exp["routes"] = _as_routes(exp["routes"])

    # ---- business tool calls -----------------------------------------
    if "tool_calls" not in exp:
        tool_source = data.get("tools", data.get("tool"))
        if tool_source is None:
            for key in ("tools", "tool"):
                if key in exp:
                    tool_source = exp[key]
                    break
        if tool_source is not None:
            if _is_sequence(tool_source):
                exp["tool_calls"] = _normalise_tool_list(
                    tool_source, data.get("arguments"))
            else:
                exp["tool_calls"] = [
                    _normalise_tool_entry(tool_source, data.get("arguments"))]
    else:
        exp["tool_calls"] = _normalise_tool_list(exp["tool_calls"])

    # ---- action / clarify fields -------------------------------------
    if "action" not in exp and "action" in data:
        exp["action"] = data["action"]
    if "missing_fields" not in exp:
        for key in ("missing_fields", "missing"):
            if key in data:
                exp["missing_fields"] = data[key]
                break
            if key in exp:
                exp["missing_fields"] = exp[key]
                break
    if "action" not in exp and exp.get("missing_fields"):
        exp["action"] = "clarify"
    for key in ("call_match", "route_ordered", "variants", "compare", "judge"):
        if key not in exp and key in data:
            exp[key] = data[key]

    data["expected"] = exp

    # ---- fixtures -----------------------------------------------------
    if "fixtures" not in data:
        # Variants are alternative valid executions.  Their business tools
        # still need simulated returns when fixtures are auto-generated;
        # otherwise a valid variant can reach execution and fail with an
        # unexplained fixture_miss.  Explicit fixtures (including []) keep
        # their intentional opt-out semantics below.
        top_calls = exp.get("tool_calls")
        calls = list(top_calls) if _is_sequence(top_calls) else []
        variants = exp.get("variants")
        for variant in variants if _is_sequence(variants) else []:
            if isinstance(variant, Mapping):
                variant_calls = variant.get("tool_calls")
                if _is_sequence(variant_calls):
                    calls.extend(variant_calls)
        if (isinstance(calls, list) and calls) or "mock" in data or "mocks" in data:
            data["fixtures"] = _auto_fixtures(calls, data)
    else:
        data["fixtures"] = _normalise_fixtures_shorthand(data["fixtures"])
    return data


def _validate_case(raw: Mapping[str, Any], skill_ids: Sequence[str],
                   line_fallback: int | None = None,
                   skill_aliases: Mapping[str, str] | None = None
                   ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Validate and normalise one raw case object."""

    raw = _normalise_compact_case(raw)
    aliases = skill_aliases or {}
    errors: list[dict[str, Any]] = []
    line = raw.get("_line") if isinstance(raw.get("_line"), int) else line_fallback

    def add(field_path: str | None, code: str, message: str) -> None:
        errors.append(_error(line, case_id, code, message, field_path))

    case_id: str | None = None

    query = raw.get("query")
    if not isinstance(query, str) or not query.strip():
        add("query", "missing_query", "缺少 query 或 query 为空")
        query = query if isinstance(query, str) else ""

    group_id = raw.get("group_id")
    if group_id is None:
        group_id = ""
    elif not isinstance(group_id, str):
        add("group_id", "invalid_group_id", "group_id 必须是字符串")
        group_id = ""
    else:
        group_id = group_id.strip()

    raw_id = raw.get("case_id")
    if raw_id is None:
        raw_id = raw.get("id")
    if raw_id is not None and not isinstance(raw_id, str):
        add("case_id", "invalid_case_id", "case_id 必须是字符串")
        raw_id = None
    if isinstance(raw_id, str) and not raw_id.strip():
        raw_id = None
    case_id = _normalise_case_id(raw_id, query, group_id)
    if not case_id:
        add("case_id", "invalid_case_id", "无法生成 case_id")

    expected = raw.get("expected")
    expected_out: dict[str, Any] = {}
    if expected is None:
        add("expected", "missing_expected", "缺少 expected 预期结果")
        expected = {}
    elif not isinstance(expected, Mapping):
        add("expected", "invalid_expected", "expected 必须是 JSON 对象")
        expected = {}

    context = raw.get("context")
    if context is None:
        context = {}
    elif not isinstance(context, Mapping):
        add("context", "invalid_context", "context 必须是 JSON 对象")
        context = {}

    tags = raw.get("tags")
    if tags is None:
        tags = []
    elif not _is_sequence(tags) or not all(isinstance(tag, str) for tag in tags):
        add("tags", "invalid_tags", "tags 必须是字符串数组")
        tags = []
    else:
        tags = list(tags)

    split = raw.get("split")
    if split is not None and split not in SPLITS:
        add("split", "invalid_split",
            f"split 必须是 {list(SPLITS)} 之一，收到: {split!r}")

    action = expected.get("action", "tool_call")
    if action is None:
        action = "tool_call"
    if action not in ACTIONS:
        add("expected.action", "invalid_action",
            f"action 必须是 {list(ACTIONS)} 之一，收到: {action!r}")

    call_match = expected.get("call_match", "ordered_exact")
    if call_match not in CALL_MATCH_MODES:
        add("expected.call_match", "invalid_call_match",
            f"call_match 必须是 {list(CALL_MATCH_MODES)} 之一，收到: {call_match!r}")
        call_match = "ordered_exact"

    route_ordered = expected.get("route_ordered", False)
    if not isinstance(route_ordered, bool):
        add("expected.route_ordered", "invalid_route_ordered",
            "route_ordered 必须是布尔值")
        route_ordered = False

    routes_out = _validate_routes_value(expected.get("routes"),
                                        "expected.routes", skill_ids,
                                        add, line, case_id, aliases)
    calls_out = _validate_calls_value(expected.get("tool_calls"),
                                      "expected.tool_calls", add, line, case_id)

    missing_fields_raw = expected.get("missing_fields")
    missing_fields: list[str] = []
    if missing_fields_raw is not None:
        if not _is_sequence(missing_fields_raw) \
                or not all(isinstance(field, str) for field in missing_fields_raw):
            add("expected.missing_fields", "invalid_missing_fields",
                "missing_fields 必须是字符串数组")
        else:
            missing_fields = list(missing_fields_raw)

    variants_out: list[dict[str, Any]] = []
    variants_raw = expected.get("variants")
    if variants_raw is not None:
        if not _is_sequence(variants_raw):
            add("expected.variants", "invalid_variants", "variants 必须是数组")
        else:
            for index, variant in enumerate(variants_raw):
                variant_path = f"expected.variants[{index}]"
                if not isinstance(variant, Mapping):
                    add(variant_path, "invalid_variant",
                        "variant 必须是 JSON 对象")
                    continue
                variant_out: dict[str, Any] = {}
                if "routes" in variant:
                    variant_out["routes"] = _validate_routes_value(
                        variant.get("routes"), f"{variant_path}.routes",
                        skill_ids, add, line, case_id, aliases)
                if "tool_calls" in variant:
                    variant_out["tool_calls"] = _validate_calls_value(
                        variant.get("tool_calls"), f"{variant_path}.tool_calls",
                        add, line, case_id)
                if "call_match" in variant:
                    mode = variant.get("call_match")
                    if mode not in CALL_MATCH_MODES:
                        add(f"{variant_path}.call_match", "invalid_call_match",
                            f"call_match 必须是 {list(CALL_MATCH_MODES)} 之一")
                    else:
                        variant_out["call_match"] = mode
                if "route_ordered" in variant:
                    ordered = variant.get("route_ordered")
                    if not isinstance(ordered, bool):
                        add(f"{variant_path}.route_ordered",
                            "invalid_route_ordered", "route_ordered 必须是布尔值")
                    else:
                        variant_out["route_ordered"] = ordered
                if not variant_out:
                    add(variant_path, "invalid_variant",
                        "variant 未包含 routes/tool_calls/call_match/route_ordered")
                    continue
                variants_out.append(variant_out)

    compare_out: dict[str, Any] | None = None
    compare_raw = expected.get("compare")
    if compare_raw is not None:
        if not isinstance(compare_raw, Mapping):
            add("expected.compare", "invalid_compare",
                "compare 必须是版本化比较规则对象")
        elif compare_raw.get("version") not in (COMPARE_RULE_VERSION, "compare-v2"):
            add("expected.compare.version", "invalid_compare_version",
                f"compare.version 必须是 {COMPARE_RULE_VERSION} 或 compare-v2")
        elif compare_rules_error(compare_raw):
            add("expected.compare", "invalid_compare", compare_rules_error(compare_raw))
        else:
            compare_out = dict(compare_raw)

    judge_out: dict[str, Any] | None = None
    judge_raw = expected.get("judge")
    if judge_raw is not None:
        _, judge_error = normalise_judge_criteria(judge_raw)
        if judge_error:
            add("expected.judge", "invalid_judge", judge_error)
        else:
            judge_out = {str(path): dict(spec) for path, spec in judge_raw.items()}

    critical = raw.get("critical", expected.get("critical", False))
    if not isinstance(critical, bool):
        add("critical", "invalid_critical", "critical 必须是布尔值")
        critical = False

    # ---- conflicting expectations (§13/§22) -------------------------------- #
    # clarify cases may keep routes (and variants.routes) so the optimizer knows
    # which sub-skill should have asked for clarification; they must not expect
    # business tool calls. no_action must not carry routes/tool_calls/missing
    # fields, and a tool_call case cannot require clarification.
    variant_calls = any(variant.get("tool_calls") for variant in variants_out)
    variant_routes = any(variant.get("routes") for variant in variants_out)
    if action == "clarify":
        if not missing_fields:
            add("expected.missing_fields", "clarify_missing_fields",
                "clarify 案例必须声明非空的 missing_fields")
        if calls_out or variant_calls:
            add("expected.action", "conflicting_expected",
                "clarify 案例不能同时预期业务工具调用")
    elif action == "no_action":
        if calls_out or variant_calls:
            add("expected.tool_calls", "no_action_with_tool_calls",
                "no_action 案例不能预期业务工具调用")
        if routes_out or variant_routes:
            add("expected.action", "conflicting_expected",
                "no_action 案例不能同时声明子技能路线")
        if missing_fields:
            add("expected.action", "conflicting_expected",
                "no_action 案例不能声明 missing_fields")
    elif action == "tool_call" and missing_fields:
        add("expected.action", "conflicting_expected",
            "tool_call 案例不能声明 missing_fields")

    fixtures_out = _validate_fixtures(raw.get("fixtures"), line, case_id, add)

    if errors:
        return None, errors

    expected_out = {
        "action": action,
        "routes": routes_out,
        "route_ordered": route_ordered,
        "call_match": call_match,
        "tool_calls": calls_out,
        "missing_fields": missing_fields,
    }
    if variants_out:
        expected_out["variants"] = variants_out
    if compare_out is not None:
        expected_out["compare"] = compare_out
    if judge_out:
        expected_out["judge"] = judge_out
    if critical:
        expected_out["critical"] = True

    case: dict[str, Any] = {
        "case_id": case_id,
        "query": query,
        "context": dict(context),
        "expected": expected_out,
        "fixtures": fixtures_out,
        "split": split if split in SPLITS else None,
        "group_id": group_id,
        "tags": tags,
    }
    if critical:
        case["critical"] = True
    return case, []


# --------------------------------------------------------------------------- #
# split assignment
# --------------------------------------------------------------------------- #
def assign_splits(cases: Iterable[Mapping[str, Any]], config: Mapping[str, Any] | None = None,
                  seed: int | None = None) -> dict[str, Any]:
    """Assign ``split`` per group with a fixed seed.

    Returns ``{"cases": [...], "warnings": [{"line", "code", "message"}]}``.
    Cases that already declare a valid split keep it (a group with conflicting
    explicit splits is re-assigned and reported with ``group_split_conflict``).
    """

    copied = [dict(case) for case in cases]
    warnings: list[dict[str, Any]] = []
    if not copied:
        return {"cases": copied, "warnings": warnings}

    ratios = _split_ratios(config)
    min_final = _min_cases_for_final_test(config)
    seed_value = seed
    if not isinstance(seed_value, int) or isinstance(seed_value, bool):
        candidate_seed = config.get("seed") if isinstance(config, Mapping) else None
        seed_value = (candidate_seed if isinstance(candidate_seed, int)
                      and not isinstance(candidate_seed, bool) else DEFAULT_SEED)

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for index, case in enumerate(copied):
        key = case.get("group_id") or case.get("case_id") or f"__row_{index}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(case)

    assigned: dict[str, str] = {}
    for key in order:
        explicit = {member.get("split") for member in groups[key]
                    if member.get("split") in SPLITS}
        if len(explicit) == 1:
            assigned[key] = explicit.pop()
        elif len(explicit) > 1:
            warnings.append(_warning(
                None, "group_split_conflict",
                f"分组 {key} 内存在多个显式 split，已按固定种子重新划分"))

    counts: Counter[str] = Counter()
    for key in order:
        if key in assigned:
            counts[assigned[key]] += len(groups[key])

    targets = _split_targets(len(copied), len(order), ratios, min_final)
    unassigned = [key for key in order if key not in assigned]
    if unassigned:
        shuffled = list(unassigned)
        random.Random(seed_value).shuffle(shuffled)
        for position, key in enumerate(shuffled):
            remaining_after = len(shuffled) - position - 1
            split = _choose_split(counts, targets, remaining_after)
            assigned[key] = split
            counts[split] += len(groups[key])

    for key in order:
        split = assigned.get(key, "optimize")
        for member in groups[key]:
            member["split"] = split

    split_counts = Counter(case.get("split") for case in copied)
    if split_counts.get("final_test", 0) == 0:
        warnings.append(_warning(
            None, "no_final_test",
            "数据集没有独立最终测试集，只能使用优化集与验证集评估；"
            "增加样本后可补齐 final_test"))
    if split_counts.get("validation", 0) == 0 and len(copied) > 1:
        warnings.append(_warning(
            None, "no_validation", "数据集没有验证集，无法进行候选晋级验证"))
    return {"cases": copied, "warnings": warnings}


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #
def _skill_id_list(skill_ids: Any) -> list[str]:
    if skill_ids is None:
        return []
    if isinstance(skill_ids, Mapping):
        declared = _manifest_skill_ids(skill_ids)
        if declared:
            return declared
    if isinstance(skill_ids, str):
        return [skill_ids]
    if _is_sequence(skill_ids):
        result: list[str] = []
        for entry in skill_ids:
            if isinstance(entry, str) and entry:
                name = entry
            elif isinstance(entry, Mapping):
                name = entry.get("skill_id") or entry.get("id")
            else:
                continue
            if isinstance(name, str) and name and name not in result:
                result.append(name)
        return result
    return []


def coverage(cases: Iterable[Mapping[str, Any]], skill_ids: Any = None) -> dict[str, Any]:
    """Coverage statistics; mirrors ``persistence.dataset_coverage`` structure."""

    rows = list(cases)
    skill_list = _skill_id_list(skill_ids)
    # Tool names are data-set expectations, not environment declarations.
    tool_list: list[str] = []
    for case in rows:
        for call in _case_tool_calls(case):
            name = call.get("name")
            if isinstance(name, str) and name and name not in tool_list:
                tool_list.append(name)

    skill_counts: dict[str, dict[str, int]] = {
        skill_id: {"optimize": 0, "validation": 0, "final_test": 0, "total": 0}
        for skill_id in skill_list
    }
    for case in rows:
        split = case.get("split") or "optimize"
        seen: set[str] = set()
        for route in _case_routes(case):
            for skill_id in route:
                if skill_id in skill_counts and skill_id not in seen:
                    seen.add(skill_id)
                    skill_counts[skill_id][split] = skill_counts[skill_id].get(split, 0) + 1
                    skill_counts[skill_id]["total"] += 1

    tool_counts: dict[str, int] = {tool: 0 for tool in tool_list}
    for case in rows:
        for call in _case_tool_calls(case):
            name = call.get("name")
            if name in tool_counts:
                tool_counts[name] += 1

    multi_route_cases = sum(1 for case in rows if len(_case_routes(case)) > 1)
    actions = {"tool_call": 0, "clarify": 0, "no_action": 0}
    for case in rows:
        action = _case_expected(case).get("action") or "tool_call"
        actions[action] = actions.get(action, 0) + 1
    split_counts: dict[str, int] = {}
    for case in rows:
        split = case.get("split") or "optimize"
        split_counts[split] = split_counts.get(split, 0) + 1

    return {
        "total": len(rows),
        "split_counts": split_counts,
        "skills": skill_counts,
        "tools": tool_counts,
        "multi_route_cases": multi_route_cases,
        "actions": actions,
        "clarify_cases": actions.get("clarify", 0),
        "no_action_cases": actions.get("no_action", 0),
    }


# --------------------------------------------------------------------------- #
# preflight entry point
# --------------------------------------------------------------------------- #
def validate_and_prepare(items: Iterable[Mapping[str, Any]], *,
                         manifest: Any = None,
                         split_config: Mapping[str, Any] | None = None,
                         seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """Validate raw items, generate ids, assign splits and compute statistics.

    Returns ``{"cases", "errors", "warnings", "stats"}``:
    ``cases`` contains only valid cases (with splits assigned), ``errors`` is
    the preflight error list, ``stats`` includes ``total/valid/invalid/splits``
    and ``coverage``. Invalid rows must be fixed or explicitly removed before a
    dataset version can be published.
    """

    skill_ids = _manifest_skill_ids(manifest)
    skill_aliases = _skill_aliases(manifest)
    # Tool names and argument rules come from the selected skill at execution
    # time. Dataset validation only checks the call shape and expected values.

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    valid_cases: list[dict[str, Any]] = []
    seen_ids: dict[str, int | None] = {}
    seen_queries: dict[str, tuple[int | None, str]] = {}
    total = 0

    for index, raw in enumerate(items):
        total += 1
        if not isinstance(raw, Mapping):
            errors.append(_error(index + 1, None, "invalid_case",
                                 "案例必须是 JSON 对象", "case"))
            continue
        line = raw.get("_line") if isinstance(raw.get("_line"), int) else index + 1
        parse_error = raw.get("_parse_error")
        if isinstance(parse_error, Mapping):
            message = parse_error.get("message") or "JSON 解析失败"
            errors.append(_error(parse_error.get("line", line), None,
                                 parse_error.get("code", "parse_error"),
                                 str(message), None))
            continue

        case, case_errors = _validate_case(raw, skill_ids, index + 1,
                                            skill_aliases)
        if case_errors:
            errors.extend(case_errors)
            continue
        assert case is not None
        case_id = case["case_id"]
        if case_id in seen_ids:
            first_line = seen_ids[case_id]
            errors.append(_error(
                line, case_id, "duplicate_case_id",
                f"case_id 重复: {case_id}（首次出现在第 {first_line} 行）"
                if first_line is not None else f"case_id 重复: {case_id}",
                "case_id"))
            continue
        seen_ids[case_id] = line

        query_key = case["query"].strip()
        if query_key in seen_queries:
            first_line = seen_queries[query_key][0]
            warnings.append(_warning(
                line, "duplicate_query",
                f"query 与第 {first_line} 行案例重复（允许保留不同上下文的合理重复）"))
        else:
            seen_queries[query_key] = (line, case_id)
        valid_cases.append(case)

    split_result = assign_splits(valid_cases, split_config, seed)
    cases = split_result["cases"]
    warnings.extend(split_result["warnings"])

    if total == 0:
        warnings.append(_warning(None, "empty_dataset", "数据集为空，没有可发布案例"))

    split_counts = {split: 0 for split in SPLITS}
    for case in cases:
        split = case.get("split") or "optimize"
        split_counts[split] = split_counts.get(split, 0) + 1

    coverage_stats = coverage(cases, skill_ids)
    stats = {
        "total": total,
        "valid": len(cases),
        "invalid": total - len(cases),
        "splits": split_counts,
        "coverage": coverage_stats,
    }
    return {"cases": cases, "errors": errors, "warnings": warnings, "stats": stats}
