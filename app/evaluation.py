"""Deterministic ``score-v1`` scorer and metric aggregation.

The scorer is a pure function of ``(expected, actual, tool_schema, compare_rules)``.
It follows the frozen rules in ``docs/CONTRACTS.md`` §2.4 and requirement §15/§16:

* Routing (R), tool arguments (A) and joint (J) are scored independently. A wrong
  dispatch does **not** fail A when the arguments happen to be correct (A05), and
  correct dispatch does not rescue wrong arguments.
* Argument comparison is exact JSON deep comparison by default (object key order
  ignored, types strict, missing != null, array order significant, extra
  arguments are errors). JSON-Schema validation for the actual arguments runs
  *before* value comparison.
* Numeric tolerance, datetime normalisation, unordered arrays and per-field
  allowed values only activate through an explicit versioned ``compare-v1``
  rule set on ``expected["compare"]`` (or the ``compare_rules`` argument).
  Unknown rule versions make the case ``invalid`` rather than guessing.
* ``compare-v2`` adds a comparison ``mode`` for tool arguments:
  ``strict`` (default, exact values), ``lenient`` (same keys; scalar values
  compared after normalisation: NFKC/trim/whitespace/case, ``"3" == 3``,
  ``"true" == True``, ISO datetimes, arrays ignore order) and ``keys`` (same
  keys; values ignored). ``allow_extra_keys`` tolerates arguments the
  expectation does not list. ``field_rules`` override the mode for a subtree
  (``strict`` / ``lenient`` / ``keys`` / ``ignore`` — ignore also allows the
  field to be absent), optionally scoped to one tool; ``[*]`` / ``*`` match
  any array index / key. Tool-schema checks keep required fields and explicit
  ``additionalProperties: false`` in every mode; value checks (scalar type,
  enum, range) are normalised in ``lenient`` and skipped in ``keys``.
  Environment rules are the default; case rules (``expected["compare"]``)
  override them.
* ``semantic`` keeps every structural check in code (route, tool name, keys,
  required fields) and only delegates *values* to a judge model. Scoring stays
  a pure function: values that need a verdict are returned as
  ``pending_judgments``; the caller obtains verdicts and scores again with
  ``judgments``. Literal keyword hits (``expected["judge"][path].keywords``)
  and lenient-equal values pass without a verdict. ``semantic_fallback=True``
  (no judge configured) scores semantic values leniently.
* Capability failures (format_error/refused/steps_exhausted/context_overflow/
  truncated/protocol_error/fixture_miss/parse_failed) count in denominators and
  fail the case; infrastructure errors (auth/network/timeout/...) are reported
  as ``status="infra"`` and excluded from every denominator.
* ``capabilities.routing`` / ``capabilities.tool_arguments`` set to ``False``
  make the matching metric ``evaluable=False`` (A13: routing-only adapters show
  A as “未评估” instead of 0%).
* ``variants`` are complete alternative expectations; a case passes a dimension
  when **any** variant matches, while J requires one single variant where action,
  routes (if declared) and tool calls (if declared) all match.

No database, network, global state or debug output.
"""

from __future__ import annotations

import re
import unicodedata

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .event_types import CASE_ERROR_CODES
from .fixtures import json_deep_equal

SCORER_VERSION = "score-v1"
COMPARE_RULE_VERSION = "compare-v1"
COMPARE_RULE_VERSION_V2 = "compare-v2"
KNOWN_COMPARE_RULE_VERSIONS = frozenset({COMPARE_RULE_VERSION, COMPARE_RULE_VERSION_V2})
#: Tool argument comparison modes (compare-v2). ``semantic`` asks the frozen
#: judge model whether values mean the same thing (see ``app/judge.py``).
COMPARE_MODES = ("strict", "lenient", "keys", "semantic")
#: Modes a single field rule may use; ``ignore`` also accepts an absent field.
FIELD_RULE_MODES = COMPARE_MODES + ("ignore",)
COMPARE_MODE_LABELS = {"strict": "严格", "lenient": "宽松", "keys": "只看字段",
                       "semantic": "语义（大模型）", "ignore": "不比较"}
#: Bumped whenever what the judge is asked (or how verdicts are keyed) changes.
JUDGE_PROMPT_VERSION = "judge-v1"

ACTIONS = ("tool_call", "clarify", "no_action")
CALL_MATCH_MODES = ("ordered_exact", "unordered")
INTERNAL_TOOLS = frozenset({"select_skill", "read_skill", "finish"})
DIAGNOSTIC_CATEGORIES = (
    "wrong_route", "missing_route", "extra_route", "missing_tool", "extra_tool",
    "wrong_tool", "missing_arg", "extra_arg", "type_error", "value_error",
    "wrong_order", "protocol_error", "fixture_miss", "clarify_mismatch",
    "unexpected_call", "action_mismatch",
)

_MISSING = object()


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "unknown"


def _diag(category: str, *, case_id: str | None = None,
          skill_id: str | None = None, tool: str | None = None,
          field_path: str | None = None,
          expected_value: Any = _MISSING,
          actual_value: Any = _MISSING,
          note: str | None = None) -> dict[str, Any]:
    """Build one diagnostic entry (all contract fields always present)."""

    entry = {
        "category": category,
        "skill_id": skill_id,
        "tool": tool,
        "field_path": field_path,
        "expected_value": None if expected_value is _MISSING else expected_value,
        "actual_value": None if actual_value is _MISSING else actual_value,
        "case_id": case_id,
    }
    if note:
        entry["note"] = note
    return entry


def _case_id(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> str | None:
    for source in (actual, expected):
        value = source.get("case_id")
        if isinstance(value, str) and value:
            return value
    return None


def _child_path(path: str, key: str) -> str:
    if path in ("", "$"):
        return f"$.{key}"
    return f"{path}.{key}"


# --------------------------------------------------------------------------- #
# tool schema helpers
# --------------------------------------------------------------------------- #
def _schema_from_spec(spec: Any) -> dict[str, Any]:
    if isinstance(spec, Mapping):
        for key in ("parameters", "input_schema", "schema", "json_schema"):
            nested = spec.get(key)
            if isinstance(nested, Mapping):
                return dict(nested)
        return dict(spec)
    return {}


def tool_schema_map(tool_schema: Any) -> dict[str, dict[str, Any]]:
    """Normalise the environment tool schema into ``{tool_name: json_schema}``.

    Accepts an OpenAI-style tool list, ``{"tools": [...]}``, a mapping of tool
    name to schema/spec, or a plain list of names (schema checks are then skipped).
    """

    result: dict[str, dict[str, Any]] = {}
    if tool_schema is None:
        return result
    if isinstance(tool_schema, Mapping):
        tools = tool_schema.get("tools")
        if _is_sequence(tools):
            return tool_schema_map(tools)
        for name, spec in tool_schema.items():
            if isinstance(name, str) and name:
                result[name] = _schema_from_spec(spec)
        return result
    if _is_sequence(tool_schema):
        for entry in tool_schema:
            if isinstance(entry, str):
                result[entry] = {}
                continue
            if not isinstance(entry, Mapping):
                continue
            function = entry.get("function")
            fn = function if isinstance(function, Mapping) else {}
            name = entry.get("name") or fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            spec = entry.get("parameters")
            if spec is None:
                spec = entry.get("input_schema")
            if spec is None:
                spec = fn.get("parameters")
            if spec is None:
                spec = fn.get("input_schema")
            if spec is None:
                spec = fn.get("schema")
            result[name] = _schema_from_spec(spec)
    return result


def tool_names(tool_schema: Any) -> list[str]:
    """Return declared tool names in declaration order."""

    return list(tool_schema_map(tool_schema))


# --------------------------------------------------------------------------- #
# compare rules
# --------------------------------------------------------------------------- #
def _normalise_path(path: str) -> str:
    path = str(path)
    if path == "$":
        return ""
    if path.startswith("$."):
        return path[2:]
    if path.startswith("$"):
        return path[1:]
    return path.lstrip(".")


_COMPARE_RULE_KEYS = ("numeric_tolerance", "normalize_datetime",
                      "unordered_arrays", "allowed_values")
_COMPARE_V2_KEYS = ("mode", "allow_extra_keys", "field_rules")
_DEFAULT_COMPARE_RULES = {
    "version": None,
    "numeric_tolerance": None,
    "normalize_datetime": False,
    "unordered_arrays": False,
    "allowed_values": {},
    "mode": "strict",
    "allow_extra_keys": False,
    "field_rules": [],
}


def _rule_version_error(version: Any) -> str | None:
    if version is None:
        return "参数比较规则必须显式声明 version"
    if version not in KNOWN_COMPARE_RULE_VERSIONS:
        return (f"未知的参数比较规则版本: {version!r}"
                f"（支持 {COMPARE_RULE_VERSION} / {COMPARE_RULE_VERSION_V2}）")
    return None


def _compile_field_path(path: str) -> re.Pattern[str]:
    """``args.items[*].name`` / ``$.args.*.id`` -> regex over normalised paths."""
    pattern = re.escape(_normalise_path(path))
    pattern = pattern.replace(r"\[\*\]", r"\[\d+\]").replace(r"\*", r"[^.\[\]]+")
    return re.compile(pattern)


def _normalise_field_rules(raw: Any) -> tuple[list[dict[str, Any]], str | None]:
    if raw is None:
        return [], None
    if not _is_sequence(raw):
        return [], "field_rules 必须是数组"
    rules: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            return [], f"field_rules[{index}] 必须是对象"
        path = item.get("path")
        if not isinstance(path, str) or not _normalise_path(path.strip()):
            return [], f"field_rules[{index}].path 必须是非空字段路径，如 $.args.color"
        mode = item.get("mode")
        if mode not in FIELD_RULE_MODES:
            return [], (f"field_rules[{index}].mode 必须是 "
                        f"{' / '.join(FIELD_RULE_MODES)}")
        tool = item.get("tool")
        if tool is not None and (not isinstance(tool, str) or not tool.strip()):
            return [], f"field_rules[{index}].tool 必须是工具名"
        rules.append({"path": path.strip(), "mode": mode,
                      "tool": tool.strip() if isinstance(tool, str) else None,
                      "pattern": _compile_field_path(path.strip())})
    return rules, None


def _normalise_compare_rules(compare_rules: Any,
                             expected: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return ``(rules, error_message)`` for the effective versioned rule set.

    Two different objects reach the fourth ``score_case`` argument in practice:

    * the frozen environment scorer config, e.g. ``{"version": "score-v1",
      "route_compare": "set", "param_compare": "strict_json"}`` — it selects the
      scorer but does **not** activate relaxed comparison rules;
    * an optional versioned comparison rule set (``compare-v1`` / ``compare-v2``).

    Case-level rules live in ``expected["compare"]`` and must always carry a
    supported version. Rule keys passed through ``compare_rules`` are only
    applied when they carry an explicit supported version; otherwise they are
    ignored as scorer configuration and strict comparison stays the default.
    ``compare-v2`` keys (mode / allow_extra_keys / field_rules) from the case
    override the environment; case field rules are consulted first.
    """

    raw: dict[str, Any] = {}
    env_v2: dict[str, Any] = {}
    case_v2: dict[str, Any] = {}
    expected_compare = expected.get("compare")
    if isinstance(expected_compare, Mapping) and expected_compare:
        error = _rule_version_error(expected_compare.get("version"))
        if error:
            return {}, error
        raw.update({key: expected_compare[key] for key in _COMPARE_RULE_KEYS
                    if key in expected_compare})
        case_v2 = {key: expected_compare[key] for key in _COMPARE_V2_KEYS
                   if key in expected_compare}
        if case_v2 and expected_compare.get("version") != COMPARE_RULE_VERSION_V2:
            return {}, f"mode / allow_extra_keys / field_rules 需要 version {COMPARE_RULE_VERSION_V2}"
    if isinstance(compare_rules, Mapping) and compare_rules:
        rule_keys = [key for key in _COMPARE_RULE_KEYS if key in compare_rules]
        v2_keys = [key for key in _COMPARE_V2_KEYS if key in compare_rules]
        if rule_keys or v2_keys:
            error = _rule_version_error(compare_rules.get("version"))
            if error:
                return {}, error
            if v2_keys and compare_rules.get("version") != COMPARE_RULE_VERSION_V2:
                return {}, f"mode / allow_extra_keys / field_rules 需要 version {COMPARE_RULE_VERSION_V2}"
            for key in rule_keys:
                raw[key] = compare_rules[key]
            env_v2 = {key: compare_rules[key] for key in v2_keys}
        # No comparison-rule keys: treat the input as the environment scorer
        # config (e.g. {"version": "score-v1"}) and keep strict comparison.
    if not raw and not env_v2 and not case_v2:
        return dict(_DEFAULT_COMPARE_RULES), None
    tolerance = raw.get("numeric_tolerance")
    if tolerance is not None and (not _is_number(tolerance) or tolerance < 0):
        return {}, "numeric_tolerance 必须是大于等于 0 的数值"
    normalize_dt = raw.get("normalize_datetime", False)
    if not isinstance(normalize_dt, bool):
        return {}, "normalize_datetime 必须是布尔值"
    unordered = raw.get("unordered_arrays", False)
    if not isinstance(unordered, bool):
        return {}, "unordered_arrays 必须是布尔值"
    allowed_raw = raw.get("allowed_values")
    allowed: dict[str, list[Any]] = {}
    if allowed_raw is not None:
        if not isinstance(allowed_raw, Mapping):
            return {}, "allowed_values 必须是 {字段路径: [允许值]} 对象"
        for key, values in allowed_raw.items():
            if not _is_sequence(values):
                return {}, "allowed_values 的每个值必须是数组"
            allowed[_normalise_path(key)] = list(values)

    mode = case_v2.get("mode", env_v2.get("mode", "strict"))
    if mode not in COMPARE_MODES:
        return {}, f"mode 必须是 {' / '.join(COMPARE_MODES)}"
    allow_extra = case_v2.get("allow_extra_keys",
                              env_v2.get("allow_extra_keys", mode != "strict"))
    if not isinstance(allow_extra, bool):
        return {}, "allow_extra_keys 必须是布尔值"
    case_fields, error = _normalise_field_rules(case_v2.get("field_rules"))
    if error:
        return {}, error
    env_fields, error = _normalise_field_rules(env_v2.get("field_rules"))
    if error:
        return {}, error
    return {
        "version": COMPARE_RULE_VERSION_V2 if (env_v2 or case_v2) else COMPARE_RULE_VERSION,
        "numeric_tolerance": float(tolerance) if tolerance is not None else None,
        "normalize_datetime": normalize_dt,
        "unordered_arrays": unordered,
        "allowed_values": allowed,
        "mode": mode,
        "allow_extra_keys": allow_extra,
        "field_rules": case_fields + env_fields,
    }, None


def compare_rules_error(rules: Any) -> str | None:
    """Validate a ``compare`` object (dataset import / environment save)."""
    if rules is None:
        return None
    if not isinstance(rules, Mapping):
        return "compare 必须是版本化比较规则对象"
    _, error = _normalise_compare_rules(None, {"compare": rules})
    return error


def compare_summary(rules: Mapping[str, Any]) -> dict[str, Any]:
    """Public, JSON-safe description of the effective comparison rules."""
    return {
        "mode": rules.get("mode", "strict"),
        "allow_extra_keys": bool(rules.get("allow_extra_keys")),
        "field_rules": [{"path": item["path"], "mode": item["mode"],
                         **({"tool": item["tool"]} if item.get("tool") else {})}
                        for item in rules.get("field_rules") or []],
    }


def _mode_at(path: str, ctx: Mapping[str, Any]) -> str:
    """Comparison mode for ``path``: a matching field rule, else the inherited mode."""
    rules = ctx.get("rules") or {}
    inherited = ctx.get("mode") or rules.get("mode") or "strict"
    field_rules = rules.get("field_rules") or ()
    if not field_rules:
        return inherited
    normalised = _normalise_path(path)
    tool = ctx.get("tool")
    for rule in field_rules:
        if rule.get("tool") and rule["tool"] != tool:
            continue
        if rule["pattern"].fullmatch(normalised):
            return rule["mode"]
    return inherited


_NUMERIC_TEXT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _lenient_key(value: Any) -> tuple[str, Any] | None:
    """Normalised comparison key for a scalar; ``None`` for containers."""
    if isinstance(value, bool):
        return ("bool", value)
    if _is_number(value):
        return ("num", float(value))
    if value is None:
        return ("null", None)
    if isinstance(value, str):
        text = unicodedata.normalize("NFKC", value).strip()
        text = re.sub(r"\s+", " ", text).casefold()
        if text in ("true", "false"):
            return ("bool", text == "true")
        if _NUMERIC_TEXT.match(text):
            return ("num", float(text))
        return ("str", text)
    return None


def _lenient_equal(expected: Any, actual: Any, rules: Mapping[str, Any]) -> bool:
    if json_deep_equal(expected, actual):
        return True
    left, right = _lenient_key(expected), _lenient_key(actual)
    if left is not None and left == right:
        return True
    tolerance = rules.get("numeric_tolerance")
    if tolerance is not None and left and right and left[0] == right[0] == "num":
        return abs(left[1] - right[1]) <= float(tolerance)
    if isinstance(expected, str) and isinstance(actual, str):
        parsed_left, parsed_right = _parse_datetime(expected.strip()), _parse_datetime(actual.strip())
        if parsed_left is not None and parsed_right is not None:
            return parsed_left == parsed_right
    return False


def _allowed_value(path: str, actual: Any, rules: Mapping[str, Any]) -> bool:
    allowed = rules.get("allowed_values") or {}
    values = allowed.get(_normalise_path(path))
    if not values:
        return False
    return any(json_deep_equal(actual, value) for value in values)


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _values_equal(expected: Any, actual: Any, path: str,
                  rules: Mapping[str, Any]) -> bool:
    if json_deep_equal(expected, actual):
        return True
    tolerance = rules.get("numeric_tolerance")
    if tolerance is not None and _is_number(expected) and _is_number(actual):
        return abs(float(expected) - float(actual)) <= float(tolerance)
    if rules.get("normalize_datetime") and isinstance(expected, str) \
            and isinstance(actual, str):
        left, right = _parse_datetime(expected), _parse_datetime(actual)
        if left is not None and right is not None:
            return left == right
    return False


# --------------------------------------------------------------------------- #
# expected / actual normalisation
# --------------------------------------------------------------------------- #
def _normalise_routes(routes: Any) -> list[list[str]]:
    if not _is_sequence(routes):
        return []
    result: list[list[str]] = []
    for route in routes:
        if isinstance(route, str):
            result.append([route])
        elif _is_sequence(route):
            result.append([item for item in route if isinstance(item, str)])
    return result


def _normalise_calls(calls: Any) -> list[dict[str, Any]]:
    if not _is_sequence(calls):
        return []
    result: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, Mapping):
            result.append({"name": None, "arguments": {}})
            continue
        name = call.get("name")
        arguments = call.get("arguments")
        path = call.get("arguments_path")
        if not isinstance(path, str) or not path.strip():
            path = None
        else:
            path = path.strip()
            while path.startswith("$"):
                path = path[1:]
            path = path.lstrip(".")
        result.append({
            "name": name if isinstance(name, str) else None,
            "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
            "arguments_path": path,
            "raw": call,
        })
    return result


def _actual_business_calls(actual: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(calls, had_invalid_entry)`` for business calls only."""

    raw = actual.get("tool_calls")
    if not _is_sequence(raw):
        return [], raw is not None
    calls: list[dict[str, Any]] = []
    invalid = False
    for entry in raw:
        if not isinstance(entry, Mapping):
            invalid = True
            calls.append({"name": None, "arguments": {}})
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name in INTERNAL_TOOLS:
            if not isinstance(name, str):
                invalid = True
            continue
        arguments = entry.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            invalid = True
            arguments = {}
        calls.append({"name": name, "arguments": dict(arguments), "raw": entry})
    return calls, invalid


def _actual_route(actual: Mapping[str, Any]) -> list[str]:
    events = actual.get("route_events")
    if _is_sequence(events) and events:
        entries: list[tuple[float, int, str]] = []
        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                continue
            skill_id = event.get("skill_id")
            if not isinstance(skill_id, str) or not skill_id:
                continue
            seq = event.get("seq")
            sort_key = float(seq) if _is_number(seq) else float(index)
            entries.append((sort_key, index, skill_id))
        if entries:
            entries.sort(key=lambda item: (item[0], item[1]))
            return [skill_id for _, _, skill_id in entries]
    selected = actual.get("selected_skills")
    if _is_sequence(selected):
        return [skill_id for skill_id in selected if isinstance(skill_id, str)]
    return []


def _actual_action(actual: Mapping[str, Any],
                   business_calls: Sequence[Mapping[str, Any]]) -> str:
    action = actual.get("action")
    if isinstance(action, str) and action in ACTIONS:
        return action
    if business_calls:
        return "tool_call"
    if actual.get("missing_fields"):
        return "clarify"
    return "no_action"


def _normalise_fields(value: Any) -> list[str]:
    if not _is_sequence(value):
        return []
    return sorted({str(item) for item in value if isinstance(item, (str, int, float))})


def _expected_candidates(expected: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build complete alternative expectations (base first, then variants)."""

    base = {
        "index": 0,
        "routes": _normalise_routes(expected.get("routes")),
        "route_ordered": expected.get("route_ordered") is True,
        "call_match": (expected.get("call_match")
                       if expected.get("call_match") in CALL_MATCH_MODES
                       else "ordered_exact"),
        "tool_calls": _normalise_calls(expected.get("tool_calls")),
    }
    variants_raw = expected.get("variants")
    variants: list[Mapping[str, Any]] = [
        variant for variant in variants_raw
        if isinstance(variant, Mapping)
    ] if _is_sequence(variants_raw) else []

    base_empty = not base["routes"] and not base["tool_calls"]
    candidates: list[dict[str, Any]] = []
    if not (base_empty and variants):
        candidates.append(base)
    for index, variant in enumerate(variants, start=1):
        merged = {
            "index": index,
            "routes": (_normalise_routes(variant.get("routes"))
                       if "routes" in variant else list(base["routes"])),
            "route_ordered": (variant.get("route_ordered") is True
                              if "route_ordered" in variant
                              else base["route_ordered"]),
            "call_match": (variant.get("call_match")
                           if variant.get("call_match") in CALL_MATCH_MODES
                           else base["call_match"]),
            "tool_calls": (_normalise_calls(variant.get("tool_calls"))
                           if "tool_calls" in variant
                           else list(base["tool_calls"])),
        }
        if not merged["routes"] and not merged["tool_calls"]:
            continue
        candidates.append(merged)
    if not candidates:
        candidates.append(base)
    return candidates


def _has_expected_routes(expected: Mapping[str, Any]) -> bool:
    if _normalise_routes(expected.get("routes")):
        return True
    variants = expected.get("variants")
    if _is_sequence(variants):
        return any(isinstance(v, Mapping) and _normalise_routes(v.get("routes"))
                   for v in variants)
    return False


def _has_expected_calls(expected: Mapping[str, Any]) -> bool:
    if _normalise_calls(expected.get("tool_calls")):
        return True
    variants = expected.get("variants")
    if _is_sequence(variants):
        return any(isinstance(v, Mapping) and _normalise_calls(v.get("tool_calls"))
                   for v in variants)
    return False


# --------------------------------------------------------------------------- #
# argument comparison
# --------------------------------------------------------------------------- #
def _project_arguments(arguments: Mapping[str, Any],
                      path: str | None) -> tuple[bool, Any]:
    """Return the subtree addressed by a dotted ``arguments_path``."""
    if not path:
        return True, arguments
    current: Any = arguments
    for segment in path.split("."):
        if not segment:
            continue
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _compare_call_arguments(expect_call: Mapping[str, Any],
                           actual_arguments: Mapping[str, Any],
                           ctx: dict[str, Any], prefix: str = "$") -> bool:
    """Compare one expected business call, optionally scoped to ``arguments_path``."""
    path = expect_call.get("arguments_path")
    if path:
        found, projected = _project_arguments(actual_arguments, path)
        if not found:
            ctx["diags"].append(_diag(
                "missing_arg", case_id=ctx.get("case_id"),
                tool=expect_call.get("name"),
                field_path=f"{prefix}.{path}" if prefix else path,
                expected_value=expect_call.get("arguments")))
            return False
        return _compare_arguments(expect_call.get("arguments"), projected,
                                  f"{prefix}.{path}", ctx)
    return _compare_arguments(expect_call.get("arguments"), actual_arguments,
                              prefix, ctx)


def _compare_arguments(expected: Any, actual: Any, path: str,
                       ctx: dict[str, Any]) -> bool:
    mode = _mode_at(path, ctx)
    if mode == "ignore":
        return True
    if mode != ctx.get("mode"):
        ctx = dict(ctx, mode=mode)
    diags: list[dict[str, Any]] = ctx["diags"]
    rules: Mapping[str, Any] = ctx["rules"]
    tool = ctx.get("tool")
    case_id = ctx.get("case_id")

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            diags.append(_diag("type_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=expected,
                               actual_value=actual))
            return False
        ok = True
        for key, value in expected.items():
            child = _child_path(path, str(key))
            if key not in actual:
                if _mode_at(child, ctx) == "ignore":
                    continue
                diags.append(_diag("missing_arg", case_id=case_id, tool=tool,
                                   field_path=child, expected_value=value))
                ok = False
            else:
                ok = _compare_arguments(value, actual[key], child, ctx) and ok
        for key in actual:
            if key not in expected:
                child = _child_path(path, str(key))
                if rules.get("allow_extra_keys") or _mode_at(child, ctx) == "ignore":
                    continue
                diags.append(_diag("extra_arg", case_id=case_id, tool=tool,
                                   field_path=child, actual_value=actual[key]))
                ok = False
        return ok

    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            diags.append(_diag("type_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=expected,
                               actual_value=actual))
            return False
        if mode == "keys":
            return True
        if mode == "semantic" and all(_is_scalar(item) for item in list(expected) + list(actual)):
            return _semantic_value(list(expected), list(actual), path, ctx)
        if rules.get("unordered_arrays") or mode == "lenient":
            return _compare_arguments_unordered(list(expected), list(actual), path, ctx)
        ok = True
        common = min(len(expected), len(actual))
        if len(expected) != len(actual):
            ok = False
            for index in range(common, len(expected)):
                diags.append(_diag("missing_arg", case_id=case_id, tool=tool,
                                   field_path=f"{path}[{index}]",
                                   expected_value=expected[index]))
            for index in range(common, len(actual)):
                diags.append(_diag("extra_arg", case_id=case_id, tool=tool,
                                   field_path=f"{path}[{index}]",
                                   actual_value=actual[index]))
        for index in range(common):
            ok = _compare_arguments(expected[index], actual[index],
                                    f"{path}[{index}]", ctx) and ok
        return ok

    if mode == "keys":
        return True
    if mode == "semantic":
        if _allowed_value(path, actual, rules):
            return True
        return _semantic_value(expected, actual, path, ctx)
    if mode == "lenient":
        equal = _lenient_equal(expected, actual, rules)
    else:
        equal = _values_equal(expected, actual, path, rules)
    if equal or _allowed_value(path, actual, rules):
        return True
    category = ("type_error" if _json_type(expected) != _json_type(actual)
                and not (mode == "lenient" and _lenient_key(actual) is not None
                         and _lenient_key(expected) is not None)
                else "value_error")
    diags.append(_diag(category, case_id=case_id, tool=tool, field_path=path,
                       expected_value=expected, actual_value=actual))
    return False


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (Mapping, list, tuple))


def normalise_judge_criteria(raw: Any) -> tuple[list[dict[str, Any]], str | None]:
    """``expected["judge"]``: ``{path: {"keywords": [...], "requirement": "..."}}``."""
    if raw is None:
        return [], None
    if not isinstance(raw, Mapping):
        return [], "judge 必须是 {字段路径: {keywords, requirement}} 对象"
    out: list[dict[str, Any]] = []
    for path, spec in raw.items():
        if not isinstance(path, str) or not _normalise_path(path.strip()):
            return [], "judge 的键必须是字段路径，如 $.args.color"
        if not isinstance(spec, Mapping):
            return [], f"judge[{path!r}] 必须是对象"
        keywords = spec.get("keywords", [])
        if not _is_sequence(keywords) or not all(isinstance(item, str) and item.strip()
                                                   for item in keywords):
            return [], f"judge[{path!r}].keywords 必须是非空字符串数组"
        requirement = spec.get("requirement", "")
        if not isinstance(requirement, str):
            return [], f"judge[{path!r}].requirement 必须是字符串"
        if not keywords and not requirement.strip():
            return [], f"judge[{path!r}] 至少需要 keywords 或 requirement"
        out.append({"path": path.strip(), "pattern": _compile_field_path(path.strip()),
                    "keywords": [item.strip() for item in keywords],
                    "requirement": requirement.strip()})
    return out, None


def _criteria_at(path: str, rules: Mapping[str, Any]) -> dict[str, Any] | None:
    normalised = _normalise_path(path)
    for item in rules.get("criteria") or ():
        if item["pattern"].fullmatch(normalised):
            return {"keywords": item["keywords"], "requirement": item["requirement"]}
    return None


def _keywords_hit(actual: Any, keywords: Sequence[str]) -> bool:
    if not keywords:
        return False
    text = actual if isinstance(actual, str) else json_dumps_compact(actual)
    haystack = unicodedata.normalize("NFKC", text).casefold()
    return all(unicodedata.normalize("NFKC", word).casefold() in haystack
               for word in keywords)


def json_dumps_compact(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def judgment_key(path: str, expected: Any, actual: Any,
                 criteria: Mapping[str, Any] | None) -> str:
    """Stable key of one value judgment (independent of judge model)."""
    import hashlib
    payload = json_dumps_compact({"v": JUDGE_PROMPT_VERSION, "path": _normalise_path(path),
                                  "expected": expected, "actual": actual,
                                  "criteria": criteria or {}})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _lenient_multiset_equal(expected: Sequence[Any], actual: Sequence[Any],
                            rules: Mapping[str, Any]) -> bool:
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for item in expected:
        for index, candidate in enumerate(remaining):
            if _lenient_equal(item, candidate, rules):
                del remaining[index]
                break
        else:
            return False
    return True


def _semantic_value(expected: Any, actual: Any, path: str, ctx: dict[str, Any]) -> bool:
    """Judge one scalar (or array of scalars) value in ``semantic`` mode."""
    rules: Mapping[str, Any] = ctx["rules"]
    criteria = _criteria_at(path, rules)
    if criteria and _keywords_hit(actual, criteria["keywords"]):
        return True
    if isinstance(expected, (list, tuple)):
        if isinstance(actual, (list, tuple)) and _lenient_multiset_equal(expected, actual, rules):
            return True
    elif _lenient_equal(expected, actual, rules):
        return True
    if rules.get("semantic_fallback"):
        ctx["diags"].append(_diag("value_error", case_id=ctx.get("case_id"), tool=ctx.get("tool"),
                                  field_path=path, expected_value=expected, actual_value=actual,
                                  note="未配置评分模型，按宽松规则判定"))
        return False
    key = judgment_key(path, expected, actual, criteria)
    verdict = (rules.get("judgments") or {}).get(key)
    if verdict is None:
        pending = rules.get("pending")
        if isinstance(pending, dict) and key not in pending:
            pending[key] = {"key": key, "path": path, "tool": ctx.get("tool"),
                            "expected": expected, "actual": actual,
                            "criteria": criteria or {}}
        ctx["diags"].append(_diag("value_error", case_id=ctx.get("case_id"), tool=ctx.get("tool"),
                                  field_path=path, expected_value=expected, actual_value=actual,
                                  note="等待评分模型判定"))
        return False
    used = rules.get("used_judgments")
    if isinstance(used, dict):
        used[key] = {"path": path, "tool": ctx.get("tool"), "passed": bool(verdict.get("passed")),
                     "reason": verdict.get("reason") or "", "expected": expected, "actual": actual}
    if verdict.get("passed"):
        return True
    ctx["diags"].append(_diag("value_error", case_id=ctx.get("case_id"), tool=ctx.get("tool"),
                              field_path=path, expected_value=expected, actual_value=actual,
                              note=f"评分模型：{verdict.get('reason') or '语义不一致'}"))
    return False


def _compare_arguments_unordered(expected: list[Any], actual: list[Any], path: str,
                                 ctx: dict[str, Any]) -> bool:
    diags: list[dict[str, Any]] = ctx["diags"]
    used = [False] * len(actual)
    unmatched: list[int] = []
    for index, expected_item in enumerate(expected):
        found = None
        for candidate_index, actual_item in enumerate(actual):
            if used[candidate_index]:
                continue
            probe: list[dict[str, Any]] = []
            probe_ctx = dict(ctx, diags=probe)
            if _compare_arguments(expected_item, actual_item,
                                  f"{path}[{index}]", probe_ctx):
                found = candidate_index
                break
        if found is None:
            unmatched.append(index)
        else:
            used[found] = True
    leftover = [index for index, hit in enumerate(used) if not hit]
    ok = not unmatched and not leftover
    for expected_index in unmatched:
        if leftover:
            actual_index = leftover.pop(0)
            _compare_arguments(expected[expected_index], actual[actual_index],
                               f"{path}[{expected_index}]", ctx)
        else:
            diags.append(_diag("missing_arg",
                               case_id=ctx.get("case_id"), tool=ctx.get("tool"),
                               field_path=f"{path}[{expected_index}]",
                               expected_value=expected[expected_index]))
    for actual_index in leftover:
        diags.append(_diag("extra_arg", case_id=ctx.get("case_id"),
                           tool=ctx.get("tool"),
                           field_path=f"{path}[{actual_index}]",
                           actual_value=actual[actual_index]))
    return ok


# --------------------------------------------------------------------------- #
# JSON-schema validation (runs before value comparison)
# --------------------------------------------------------------------------- #
_SCHEMA_TYPE_CHECKS = {
    "object": lambda value: isinstance(value, Mapping),
    "array": lambda value: isinstance(value, (list, tuple)),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": _is_number,
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


def _check_schema_type(value: Any, declared: Any) -> bool:
    if isinstance(declared, str):
        check = _SCHEMA_TYPE_CHECKS.get(declared)
        return check(value) if check else True
    if _is_sequence(declared):
        return any(_check_schema_type(value, item) for item in declared)
    return True


_CONTAINER_TYPES = ("object", "array")


def _lenient_type_ok(value: Any, declared: Any) -> bool:
    """Scalar type check after lenient normalisation ("3" for integer etc.)."""
    if _check_schema_type(value, declared):
        return True
    key = _lenient_key(value)
    if key is None:
        return False
    names = [declared] if isinstance(declared, str) else list(declared) if _is_sequence(declared) else []
    kind, normalised = key
    for name in names:
        if name == "number" and kind == "num":
            return True
        if name == "integer" and kind == "num" and float(normalised).is_integer():
            return True
        if name == "boolean" and kind == "bool":
            return True
        if name == "string" and kind in ("num", "bool"):
            return True
    return False


def _validate_schema(schema: Any, value: Any, path: str,
                     ctx: dict[str, Any]) -> bool:
    if not isinstance(schema, Mapping) or not schema:
        return True
    mode = _mode_at(path, ctx) if ctx.get("rules") else "strict"
    if mode == "ignore":
        return True
    if mode != ctx.get("mode"):
        ctx = dict(ctx, mode=mode)
    diags: list[dict[str, Any]] = ctx["diags"]
    tool = ctx.get("tool")
    case_id = ctx.get("case_id")
    ok = True

    declared_type = schema.get("type")
    if declared_type is not None and not _check_schema_type(value, declared_type):
        declared = [declared_type] if isinstance(declared_type, str) else list(declared_type) \
            if _is_sequence(declared_type) else []
        container_declared = any(name in _CONTAINER_TYPES for name in declared)
        if mode == "keys" and not container_declared:
            pass  # values (including their scalar type) are not compared
        elif mode in ("lenient", "semantic") and _lenient_type_ok(value, declared_type):
            pass
        else:
            diags.append(_diag("type_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=declared_type,
                               actual_value=_json_type(value)))
            return False
    if mode == "keys" and not isinstance(value, (Mapping, list, tuple)):
        return ok  # enum / range are value checks

    if isinstance(value, Mapping):
        required = schema.get("required")
        if _is_sequence(required):
            for key in required:
                if isinstance(key, str) and key not in value \
                        and _mode_at(_child_path(path, key), ctx) != "ignore":
                    diags.append(_diag("missing_arg", case_id=case_id, tool=tool,
                                       field_path=_child_path(path, key),
                                       expected_value="required"))
                    ok = False
        properties = schema.get("properties")
        props = properties if isinstance(properties, Mapping) else {}
        for key, sub_schema in props.items():
            if key in value:
                ok = _validate_schema(sub_schema, value[key],
                                      _child_path(path, str(key)), ctx) and ok
        additional = schema.get("additionalProperties")
        if additional is False:
            for key in value:
                if str(key) in props:
                    continue
                diags.append(_diag("extra_arg", case_id=case_id, tool=tool,
                                   field_path=_child_path(path, str(key)),
                                   actual_value=value[key]))
                ok = False
        elif isinstance(additional, Mapping):
            for key in value:
                if str(key) in props:
                    continue
                ok = _validate_schema(additional, value[key],
                                      _child_path(path, str(key)), ctx) and ok

    if isinstance(value, (list, tuple)):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                ok = _validate_schema(items, item, f"{path}[{index}]", ctx) and ok
        if mode == "keys":
            return ok
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            diags.append(_diag("value_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=f">={min_items}",
                               actual_value=len(value)))
            ok = False
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            diags.append(_diag("value_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=f"<={max_items}",
                               actual_value=len(value)))
            ok = False

    enum = schema.get("enum")
    if mode == "semantic" and _is_sequence(enum):
        return ok  # the judge decides whether the value means one of them
    if mode == "lenient":
        enum_hit = _is_sequence(enum) and any(_lenient_equal(item, value, ctx.get("rules") or {})
                                              for item in enum)
    else:
        enum_hit = _is_sequence(enum) and any(json_deep_equal(value, item) for item in enum)
    if _is_sequence(enum) and not enum_hit:
        diags.append(_diag("value_error", case_id=case_id, tool=tool,
                           field_path=path, expected_value=list(enum),
                           actual_value=value))
        ok = False

    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if mode in ("lenient", "semantic") and not _is_number(value):
        key = _lenient_key(value)
        if key and key[0] == "num":
            value = key[1]
    if _is_number(value):
        if _is_number(minimum) and value < minimum:
            diags.append(_diag("value_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=f">={minimum}",
                               actual_value=value))
            ok = False
        if _is_number(maximum) and value > maximum:
            diags.append(_diag("value_error", case_id=case_id, tool=tool,
                               field_path=path, expected_value=f"<={maximum}",
                               actual_value=value))
            ok = False
    return ok


# --------------------------------------------------------------------------- #
# tool-call sequence comparison
# --------------------------------------------------------------------------- #
def _compare_calls_ordered_strict(expected: list[dict[str, Any]],
                                  actual: list[dict[str, Any]],
                                  ctx: dict[str, Any]) -> bool:
    diags: list[dict[str, Any]] = ctx["diags"]
    ok = True
    if len(actual) < len(expected):
        ok = False
        for index in range(len(actual), len(expected)):
            diags.append(_diag("missing_tool", case_id=ctx.get("case_id"),
                               tool=expected[index]["name"],
                               field_path=f"$.tool_calls[{index}]",
                               expected_value=expected[index]["name"]))
    if len(actual) > len(expected):
        ok = False
        for index in range(len(expected), len(actual)):
            diags.append(_diag("extra_tool", case_id=ctx.get("case_id"),
                               tool=actual[index]["name"],
                               field_path=f"$.tool_calls[{index}]",
                               actual_value=actual[index]["name"]))
    for index in range(min(len(expected), len(actual))):
        expect_call, actual_call = expected[index], actual[index]
        if expect_call["name"] != actual_call["name"]:
            ok = False
            diags.append(_diag("wrong_tool", case_id=ctx.get("case_id"),
                               tool=actual_call["name"] or expect_call["name"],
                               field_path=f"$.tool_calls[{index}].name",
                               expected_value=expect_call["name"],
                               actual_value=actual_call["name"]))
            continue
        call_ctx = dict(ctx, tool=expect_call["name"])
        if not _compare_call_arguments(expect_call, actual_call["arguments"],
                                       call_ctx):
            ok = False
    return ok


def _compare_calls_ordered(expected: list[dict[str, Any]],
                           actual: list[dict[str, Any]],
                           ctx: dict[str, Any]) -> bool:
    """Ordered comparison that reports a pure permutation as ``wrong_order``."""

    trial: list[dict[str, Any]] = []
    if _compare_calls_ordered_strict(expected, actual,
                                     dict(ctx, diags=trial)):
        return True
    if len(expected) == len(actual):
        probe: list[dict[str, Any]] = []
        probe_ctx = dict(ctx, diags=probe)
        if _compare_calls_unordered(expected, actual, probe_ctx):
            ctx["diags"].append(_diag(
                "wrong_order", case_id=ctx.get("case_id"),
                field_path="$.tool_calls",
                expected_value=[call["name"] for call in expected],
                actual_value=[call["name"] for call in actual],
            ))
            return False
    ctx["diags"].extend(trial)
    return False


def _compare_calls_unordered(expected: list[dict[str, Any]],
                             actual: list[dict[str, Any]],
                             ctx: dict[str, Any]) -> bool:
    diags: list[dict[str, Any]] = ctx["diags"]
    used = [False] * len(actual)
    ok = True
    for expect_call in expected:
        candidates = [
            index for index, actual_call in enumerate(actual)
            if not used[index] and actual_call["name"] == expect_call["name"]
        ]
        if not candidates:
            ok = False
            diags.append(_diag("missing_tool", case_id=ctx.get("case_id"),
                               tool=expect_call["name"],
                               expected_value=expect_call["name"]))
            continue
        matched = None
        best: tuple[int, int, list[dict[str, Any]]] | None = None
        for index in candidates:
            probe: list[dict[str, Any]] = []
            call_ctx = dict(ctx, tool=expect_call["name"], diags=probe)
            if _compare_call_arguments(expect_call, actual[index]["arguments"],
                                       call_ctx):
                matched = index
                break
            if best is None or len(probe) < best[0]:
                best = (len(probe), index, probe)
        if matched is not None:
            used[matched] = True
            continue
        ok = False
        _, index, probe = best  # type: ignore[misc]
        used[index] = True
        diags.extend(probe)
    for index, actual_call in enumerate(actual):
        if not used[index]:
            ok = False
            diags.append(_diag("extra_tool", case_id=ctx.get("case_id"),
                               tool=actual_call["name"],
                               actual_value=actual_call["name"]))
    return ok


# --------------------------------------------------------------------------- #
# route comparison
# --------------------------------------------------------------------------- #
def _route_matches(actual_route: Sequence[str], allowed: Sequence[Sequence[str]],
                   ordered: bool) -> bool:
    if ordered:
        return any(list(actual_route) == list(route) for route in allowed)
    actual_set = set(actual_route)
    return any(set(route) == actual_set for route in allowed)


def _best_allowed_route(actual_route: Sequence[str],
                        allowed: Sequence[Sequence[str]]) -> list[str]:
    actual_set = set(actual_route)
    best = list(allowed[0]) if allowed else []
    best_score: tuple[int, int] | None = None
    for route in allowed:
        route_list = list(route)
        route_set = set(route_list)
        score = (len(actual_set ^ route_set), len(route_list))
        if best_score is None or score < best_score:
            best, best_score = route_list, score
    return best


def _diagnose_routes(actual_route: Sequence[str], best: Sequence[str], ordered: bool,
                     ctx: dict[str, Any]) -> None:
    diags: list[dict[str, Any]] = ctx["diags"]
    case_id = ctx.get("case_id")
    actual_list = list(actual_route)
    best_list = list(best)
    actual_set, expected_set = set(actual_list), set(best_list)
    if not actual_list:
        for skill_id in best_list:
            diags.append(_diag("missing_route", case_id=case_id, skill_id=skill_id,
                               field_path="selected_skills",
                               expected_value=skill_id))
        return
    if not actual_set & expected_set:
        diags.append(_diag("wrong_route", case_id=case_id,
                           skill_id=actual_list[0],
                           field_path="selected_skills",
                           expected_value=best_list, actual_value=actual_list))
        return
    missing = [skill_id for skill_id in best_list if skill_id not in actual_set]
    extra = [skill_id for skill_id in actual_list if skill_id not in expected_set]
    for skill_id in missing:
        diags.append(_diag("missing_route", case_id=case_id, skill_id=skill_id,
                           field_path="selected_skills",
                           expected_value=skill_id))
    for skill_id in extra:
        diags.append(_diag("extra_route", case_id=case_id, skill_id=skill_id,
                           field_path="selected_skills",
                           actual_value=skill_id))
    if not missing and not extra:
        actual_counts = Counter(actual_list)
        expected_counts = Counter(best_list)
        if actual_counts == expected_counts:
            diags.append(_diag("wrong_order", case_id=case_id,
                               skill_id=actual_list[0] if actual_list else None,
                               field_path="selected_skills",
                               expected_value=best_list, actual_value=actual_list))
            return
        for skill_id, count in (actual_counts - expected_counts).items():
            for _ in range(count):
                diags.append(_diag("extra_route", case_id=case_id,
                                   skill_id=skill_id,
                                   field_path="selected_skills",
                                   actual_value=skill_id))
        for skill_id, count in (expected_counts - actual_counts).items():
            for _ in range(count):
                diags.append(_diag("missing_route", case_id=case_id,
                                   skill_id=skill_id,
                                   field_path="selected_skills",
                                   expected_value=skill_id))


# --------------------------------------------------------------------------- #
# per-candidate evaluation
# --------------------------------------------------------------------------- #
def _evaluate_routing(candidate: Mapping[str, Any], actual_route: Sequence[str],
                      expected_action: str, routing_capable: bool,
                      case_id: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    allowed = [list(route) for route in candidate["routes"]]
    diags: list[dict[str, Any]] = []
    base = {
        "evaluable": False,
        "passed": False,
        "reason": "",
        "actual_routes": [list(actual_route)],
        "allowed_routes": allowed,
    }
    if expected_action != "tool_call":
        return dict(base, reason="该案例不评估分发准确率"), diags
    if not allowed:
        return dict(base, reason="该案例未声明允许的子技能路线"), diags
    if not routing_capable:
        return dict(base, reason="执行器未声明分发能力（routing=false），R 未评估"), diags

    ordered = bool(candidate["route_ordered"])
    passed = _route_matches(actual_route, allowed, ordered)
    if passed:
        reason = f"实际路线 {list(actual_route)} 匹配允许路线"
    else:
        ctx = {"diags": diags, "case_id": case_id}
        _diagnose_routes(actual_route, _best_allowed_route(actual_route, allowed),
                         ordered, ctx)
        mode = "按顺序" if ordered else "集合"
        reason = (f"实际路线 {list(actual_route)} 不在允许路线 {allowed} 中"
                  f"（{mode}比较）")
    return dict(base, evaluable=True, passed=passed, reason=reason), diags


def _evaluate_tool_args(candidate: Mapping[str, Any],
                        actual_calls: Sequence[Mapping[str, Any]],
                        schema_ok: bool, case_id: str | None,
                        rules: Mapping[str, Any],
                        tool_capable: bool = True) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    expected_calls = candidate["tool_calls"]
    mode = candidate["call_match"]
    diags: list[dict[str, Any]] = []
    if not expected_calls:
        return ({
            "evaluable": False,
            "passed": False,
            "reason": "该案例未预期业务工具调用，A 不评估",
            "call_match": mode,
        }, diags)
    if not tool_capable:
        return ({
            "evaluable": False,
            "passed": False,
            "reason": "执行器未声明工具入参能力（tool_arguments=false），A 未评估",
            "call_match": mode,
        }, diags)
    ctx: dict[str, Any] = {"diags": diags, "rules": rules, "case_id": case_id}
    if mode == "unordered":
        compared = _compare_calls_unordered(list(expected_calls), list(actual_calls), ctx)
    else:
        compared = _compare_calls_ordered(list(expected_calls), list(actual_calls), ctx)
    passed = bool(compared and schema_ok)
    if passed:
        reason = "业务工具调用与参数完全匹配"
    elif not compared:
        reason = "业务工具调用名、数量、顺序或参数不匹配"
    else:
        reason = "业务工具参数未通过 JSON Schema 校验"
    return ({
        "evaluable": True,
        "passed": passed,
        "reason": reason,
        "call_match": mode,
    }, diags)


def _candidate_joint(candidate: Mapping[str, Any], expected_action: str,
                     action_ok: bool, routing: Mapping[str, Any],
                     tool_args: Mapping[str, Any],
                     actual_calls: Sequence[Mapping[str, Any]],
                     actual_missing: list[str],
                     expected_missing: list[str] | None,
                     routing_capable: bool, tool_capable: bool,
                     case_id: str | None,
                     diags: list[dict[str, Any]]) -> bool:
    if not action_ok:
        return False
    if expected_action == "tool_call":
        if candidate["routes"]:
            if not routing_capable or not routing.get("passed"):
                return False
        if candidate["tool_calls"]:
            if not tool_capable or not tool_args.get("passed"):
                return False
        return True
    if expected_action == "clarify":
        if actual_calls:
            for index, call in enumerate(actual_calls):
                diags.append(_diag("unexpected_call", case_id=case_id,
                                   tool=call.get("name"),
                                   field_path=f"$.tool_calls[{index}]",
                                   actual_value=call.get("name")))
            return False
        if expected_missing is None:
            return True
        if set(expected_missing) != set(actual_missing):
            diags.append(_diag("clarify_mismatch", case_id=case_id,
                               field_path="missing_fields",
                               expected_value=sorted(expected_missing),
                               actual_value=sorted(actual_missing)))
            return False
        return True
    # no_action
    if actual_calls:
        for index, call in enumerate(actual_calls):
            diags.append(_diag("unexpected_call", case_id=case_id,
                               tool=call.get("name"),
                               field_path=f"$.tool_calls[{index}]",
                               actual_value=call.get("name")))
        return False
    return True


# --------------------------------------------------------------------------- #
# error classification
# --------------------------------------------------------------------------- #
def _classify_error(actual: Mapping[str, Any]) -> tuple[str | None, str | None]:
    error = actual.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    if not isinstance(code, str) or not code:
        code = None
    kind = actual.get("error_kind")
    if kind not in ("capability", "infrastructure", "cancelled"):
        kind = CASE_ERROR_CODES.get(code) if code else None
    if kind is None and code is not None:
        kind = "capability"
    misses = actual.get("fixture_misses")
    if kind is None and _is_sequence(misses) and misses:
        kind, code = "capability", "fixture_miss"
    return kind, code


def _error_diagnostics(actual: Mapping[str, Any], kind: str | None,
                       code: str | None, case_id: str | None) -> list[dict[str, Any]]:
    diags: list[dict[str, Any]] = []
    misses = actual.get("fixture_misses")
    if _is_sequence(misses):
        for miss in misses:
            if isinstance(miss, Mapping):
                diags.append(_diag("fixture_miss", case_id=case_id,
                                   tool=miss.get("tool"),
                                   actual_value=miss.get("arguments")))
            else:
                diags.append(_diag("fixture_miss", case_id=case_id))
    if kind == "capability" and code and code != "fixture_miss":
        diags.append(_diag("protocol_error", case_id=case_id,
                           field_path="error.code",
                           expected_value=None, actual_value=code))
    return diags


# --------------------------------------------------------------------------- #
# public entry points
# --------------------------------------------------------------------------- #
def _dedupe_diagnostics(diagnostics: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate ``(category, skill, tool, field, case)`` diagnostics.

    The same problem can be reported by both schema validation and value
    comparison; the aggregated diagnostics should count the field once.
    """

    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        key = (diagnostic.get("category"), diagnostic.get("skill_id"),
               diagnostic.get("tool"), diagnostic.get("field_path"),
               diagnostic.get("case_id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(diagnostic)
    return result


def _invalid_result(reason: str, case_id: str | None,
                    field_path: str | None = None,
                    error_kind: str | None = None) -> dict[str, Any]:
    return {
        "status": "invalid",
        "passed": False,
        "error_kind": error_kind,
        "routing": {
            "evaluable": False,
            "passed": False,
            "reason": reason,
            "actual_routes": [],
            "allowed_routes": [],
        },
        "tool_args": {
            "evaluable": False,
            "passed": False,
            "reason": reason,
            "call_match": "ordered_exact",
        },
        "action_ok": False,
        "joint": False,
        "diagnostics": [_diag("protocol_error", case_id=case_id,
                              field_path=field_path, expected_value=None,
                              actual_value=reason)],
    }


def _expected_structure_errors(expected: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate the structure the scorer depends on (dataset validation is stricter)."""

    errors: list[dict[str, Any]] = []
    action = expected.get("action", "tool_call")
    if action not in ACTIONS:
        errors.append({"field_path": "expected.action",
                       "message": f"未知的预期动作: {action!r}"})
    routes = expected.get("routes")
    if routes is not None:
        if not _is_sequence(routes):
            errors.append({"field_path": "expected.routes",
                           "message": "routes 必须是二维字符串数组"})
        else:
            for route_index, route in enumerate(routes):
                if not _is_sequence(route) or not all(isinstance(s, str) for s in route):
                    errors.append({
                        "field_path": f"expected.routes[{route_index}]",
                        "message": "每条路线必须是非空的 skill_id 字符串数组",
                    })
    call_match = expected.get("call_match")
    if call_match is not None and call_match not in CALL_MATCH_MODES:
        errors.append({"field_path": "expected.call_match",
                       "message": f"未知的 call_match: {call_match!r}"})
    route_ordered = expected.get("route_ordered")
    if route_ordered is not None and not isinstance(route_ordered, bool):
        errors.append({"field_path": "expected.route_ordered",
                       "message": "route_ordered 必须是布尔值"})
    tool_calls = expected.get("tool_calls")
    if tool_calls is not None:
        if not _is_sequence(tool_calls):
            errors.append({"field_path": "expected.tool_calls",
                           "message": "tool_calls 必须是数组"})
        else:
            for index, call in enumerate(tool_calls):
                if not isinstance(call, Mapping):
                    errors.append({"field_path": f"expected.tool_calls[{index}]",
                                   "message": "工具调用必须是 JSON 对象"})
                elif not isinstance(call.get("name"), str):
                    errors.append({"field_path": f"expected.tool_calls[{index}].name",
                                   "message": "工具调用缺少 name"})
                elif call.get("arguments") is not None \
                        and not isinstance(call.get("arguments"), Mapping):
                    errors.append({
                        "field_path": f"expected.tool_calls[{index}].arguments",
                        "message": "arguments 必须是 JSON 对象",
                    })
                path = call.get("arguments_path")
                if path is not None:
                    if not isinstance(path, str) or not path.strip():
                        errors.append({
                            "field_path": f"expected.tool_calls[{index}].arguments_path",
                            "message": "arguments_path 必须是非空字符串",
                        })
                    elif not re.match(r"^\$?\.?[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$",
                                      path.strip()):
                        errors.append({
                            "field_path": f"expected.tool_calls[{index}].arguments_path",
                            "message": "arguments_path 必须是点分字段路径，如 args 或 args.params",
                        })
    variants = expected.get("variants")
    if variants is not None:
        if not _is_sequence(variants):
            errors.append({"field_path": "expected.variants",
                           "message": "variants 必须是数组"})
        else:
            for index, variant in enumerate(variants):
                if not isinstance(variant, Mapping):
                    errors.append({"field_path": f"expected.variants[{index}]",
                                   "message": "variant 必须是 JSON 对象"})
    return errors


def score_case(expected: Any, actual: Any, tool_schema: Any = None,
               compare_rules: Any = None, *,
               judgments: Mapping[str, Mapping[str, Any]] | None = None,
               semantic_fallback: bool = False) -> dict[str, Any]:
    """Score one case with ``score-v1`` (see module docstring for the rules).

    ``judgments`` maps :func:`judgment_key` to ``{"passed": bool, "reason": str}``
    verdicts from the judge model; values still lacking a verdict are returned
    in ``pending_judgments`` (the case is then provisional).
    """

    if not isinstance(expected, Mapping):
        return _invalid_result("预期结果必须是 JSON 对象", None,
                               field_path="expected")
    if not isinstance(actual, Mapping):
        return _invalid_result("执行结果必须是 JSON 对象", None,
                               field_path="actual")

    case_id = _case_id(expected, actual)
    structure_errors = _expected_structure_errors(expected)
    if structure_errors:
        first = structure_errors[0]
        return _invalid_result(first["message"], case_id,
                               field_path=first["field_path"])

    rules, rules_error = _normalise_compare_rules(compare_rules, expected)
    if rules_error:
        return _invalid_result(rules_error, case_id,
                               field_path="expected.compare")
    criteria, criteria_error = normalise_judge_criteria(expected.get("judge"))
    if criteria_error:
        return _invalid_result(criteria_error, case_id, field_path="expected.judge")
    pending: dict[str, dict[str, Any]] = {}
    used: dict[str, dict[str, Any]] = {}
    rules = dict(rules, criteria=criteria, judgments=dict(judgments or {}),
                 pending=pending, used_judgments=used,
                 semantic_fallback=bool(semantic_fallback))

    action = expected.get("action") or "tool_call"
    actual_calls, calls_invalid = _actual_business_calls(actual)
    actual_route = _actual_route(actual)
    actual_action = _actual_action(actual, actual_calls)
    actual_missing = _normalise_fields(actual.get("missing_fields"))
    expected_missing = (None if expected.get("missing_fields") is None
                        else _normalise_fields(expected.get("missing_fields")))

    capabilities = actual.get("capabilities")
    caps = capabilities if isinstance(capabilities, Mapping) else {}
    routing_capable = caps.get("routing", True) is not False
    tool_capable = caps.get("tool_arguments", True) is not False

    error_kind, error_code = _classify_error(actual)

    schema_map = tool_schema_map(tool_schema)
    schema_diags: list[dict[str, Any]] = []
    schema_ok = True
    for index, call in enumerate(actual_calls):
        schema = schema_map.get(call.get("name") or "")
        if not isinstance(schema, Mapping) or not schema:
            continue
        call_ctx = {"diags": schema_diags, "rules": rules, "case_id": case_id,
                    "tool": call.get("name")}
        schema_ok = _validate_schema(schema, call["arguments"], "$", call_ctx) and schema_ok

    action_ok = actual_action == action

    candidates = _expected_candidates(expected)
    evaluations: list[dict[str, Any]] = []
    for candidate in candidates:
        routing, routing_diags = _evaluate_routing(
            candidate, actual_route, action, routing_capable, case_id)
        tool_args, tool_diags = _evaluate_tool_args(
            candidate, actual_calls, schema_ok, case_id, rules, tool_capable)
        joint_diags: list[dict[str, Any]] = []
        joint = _candidate_joint(
            candidate, action, action_ok, routing, tool_args,
            actual_calls, actual_missing, expected_missing,
            routing_capable, tool_capable, case_id, joint_diags)
        evaluations.append({
            "candidate": candidate,
            "routing": routing,
            "tool_args": tool_args,
            "joint": joint,
            "diags": routing_diags + tool_diags + joint_diags,
        })

    routing_evaluable = any(item["routing"]["evaluable"] for item in evaluations)
    routing_passed = any(item["routing"]["evaluable"] and item["routing"]["passed"]
                         for item in evaluations)
    tool_evaluable = any(item["tool_args"]["evaluable"] for item in evaluations)
    tool_passed = any(item["tool_args"]["evaluable"] and item["tool_args"]["passed"]
                      for item in evaluations)
    joint = any(item["joint"] for item in evaluations)

    def _rank(item: Mapping[str, Any]) -> tuple[int, int, int, int]:
        return (
            1 if item["joint"] else 0,
            1 if item["routing"]["passed"] else 0,
            1 if item["tool_args"]["passed"] else 0,
            1 if action_ok else 0,
        )

    chosen = max(evaluations, key=_rank)
    chosen_routing = chosen["routing"]
    chosen_tool_args = chosen["tool_args"]

    final_routing = {
        "evaluable": routing_evaluable,
        "passed": routing_passed,
        "reason": (chosen_routing["reason"] if not routing_passed
                   else "实际路线匹配允许路线"),
        "actual_routes": [list(actual_route)],
        "allowed_routes": [list(route)
                           for item in evaluations
                           for route in item["candidate"]["routes"]],
    }
    # de-duplicate allowed routes while keeping declaration order
    deduped_routes: list[list[str]] = []
    for route in final_routing["allowed_routes"]:
        if route not in deduped_routes:
            deduped_routes.append(route)
    final_routing["allowed_routes"] = deduped_routes

    final_tool_args = {
        "evaluable": tool_evaluable,
        "passed": tool_passed,
        "reason": (chosen_tool_args["reason"] if not tool_passed
                   else {"strict": "业务工具调用与参数完全匹配",
                         "lenient": "业务工具调用与参数按宽松规则匹配",
                         "keys": "业务工具调用与参数字段一致（只看字段）",
                         "semantic": "业务工具调用与参数语义一致"}.get(
                             rules.get("mode", "strict"), "业务工具调用与参数完全匹配")),
        "compare": compare_summary(rules),
        "call_match": (chosen_tool_args.get("call_match")
                       if not tool_passed else
                       (next((item["tool_args"].get("call_match")
                              for item in evaluations
                              if item["tool_args"]["evaluable"]
                              and item["tool_args"]["passed"]), "ordered_exact"))),
    }

    diagnostics: list[dict[str, Any]] = list(chosen["diags"])
    diagnostics.extend(schema_diags)
    diagnostics.extend(_error_diagnostics(actual, error_kind, error_code, case_id))
    if not action_ok:
        diagnostics.append(_diag("action_mismatch", case_id=case_id,
                                 field_path="action", expected_value=action,
                                 actual_value=actual_action))
    if calls_invalid:
        diagnostics.append(_diag("protocol_error", case_id=case_id,
                                 field_path="$.tool_calls",
                                 expected_value="工具调用数组",
                                 actual_value=actual.get("tool_calls")))

    if error_kind == "infrastructure":
        status, passed = "infra", False
        joint = False
        infra_reason = "基础设施错误，不计入指标分母"
        final_routing = dict(final_routing, evaluable=False, passed=False,
                             reason=infra_reason)
        final_tool_args = dict(final_tool_args, evaluable=False, passed=False,
                               reason=infra_reason)
    elif error_kind == "cancelled":
        status, passed, joint = "invalid", False, False
    elif error_kind == "capability":
        status, passed = "fail", False
        joint = False
    else:
        status = "pass" if joint else "fail"
        passed = bool(joint)

    result = {
        "status": status,
        "passed": passed,
        "error_kind": error_kind,
        "routing": final_routing,
        "tool_args": final_tool_args,
        "action_ok": bool(action_ok),
        "joint": bool(joint),
        "diagnostics": _dedupe_diagnostics(diagnostics),
    }
    if pending and error_kind is None:
        result["pending_judgments"] = list(pending.values())
    if used:
        result["judgments"] = list(used.values())
    if semantic_fallback and _uses_semantic(rules):
        result["tool_args"]["semantic_fallback"] = True
    return result


def _uses_semantic(rules: Mapping[str, Any]) -> bool:
    return rules.get("mode") == "semantic" or any(
        item.get("mode") == "semantic" for item in rules.get("field_rules") or ())


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _iter_results(results: Iterable[Any]):
    for item in results or []:
        if isinstance(item, Mapping) and "score" in item:
            case = item.get("case")
            score = item.get("score")
            yield (case if isinstance(case, Mapping) else {},
                   score if isinstance(score, Mapping) else {})
            continue
        if isinstance(item, (tuple, list)) and len(item) == 2:
            case, score = item
            yield (case if isinstance(case, Mapping) else {},
                   score if isinstance(score, Mapping) else {})


def _metric(passed: int, evaluated: int, total_cases: int) -> dict[str, Any]:
    if evaluated <= 0:
        return {"value": None, "status": "not_evaluated", "evaluated": 0,
                "passed": 0, "total_cases": total_cases}
    return {"value": round(passed / evaluated, 6), "status": "ok",
            "evaluated": evaluated, "passed": passed,
            "total_cases": total_cases}


def _block_evaluable(score: Mapping[str, Any], key: str) -> bool:
    block = score.get(key)
    if isinstance(block, Mapping) and "evaluable" in block:
        return bool(block.get("evaluable"))
    return True


def _sum_tokens(usage: Any) -> int:
    if isinstance(usage, Mapping):
        total = 0
        for name in ("input_tokens", "output_tokens",
                     "prompt_tokens", "completion_tokens"):
            value = usage.get(name)
            if _is_number(value):
                total += int(value)
        return total
    if _is_sequence(usage):
        return sum(_sum_tokens(item) for item in usage)
    return 0


def _normalise_cost(cost: Any) -> Any:
    if isinstance(cost, Mapping):
        for key in ("amount", "total", "value"):
            value = cost.get(key)
            if _is_number(value):
                return value
        return None
    return cost if _is_number(cost) else None


def _count_fixture_misses(value: Any) -> int:
    if value is None:
        return 0
    if _is_sequence(value):
        return len(value)
    if _is_number(value):
        return int(value)
    return 0


def aggregate_metrics(results: Iterable[Any], planned: int | None = None,
                      duration_ms: int = 0, usage: Any = None,
                      cost: Any = None,
                      fixture_misses: Any = None) -> dict[str, Any]:
    """Aggregate per-case scores into the run metrics payload (§2.4/§15)."""

    routing_evaluated = routing_passed = 0
    tool_evaluated = tool_passed = 0
    joint_total = joint_passed = 0
    valid = infra_errors = capability_failures = 0
    skill_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    detected_fixture_misses = 0

    entries = list(_iter_results(results))
    for case, score in entries:
        expected = case.get("expected")
        expected = expected if isinstance(expected, Mapping) else {}
        action = expected.get("action") or "tool_call"
        status = score.get("status")

        for diagnostic in score.get("diagnostics") or []:
            if not isinstance(diagnostic, Mapping):
                continue
            skill_id = diagnostic.get("skill_id")
            tool = diagnostic.get("tool")
            field_path = diagnostic.get("field_path")
            category = diagnostic.get("category")
            if isinstance(skill_id, str) and skill_id:
                skill_counts[skill_id] += 1
            if isinstance(tool, str) and tool:
                tool_counts[tool] += 1
            if isinstance(field_path, str) and field_path.startswith("$"):
                # "fields" aggregates argument field paths only ($.a.b[0]);
                # route/action diagnostics use non-JSON markers and are counted
                # through skills/tools/categories instead.
                field_counts[field_path] += 1
            if isinstance(category, str) and category:
                category_counts[category] += 1
                if category == "fixture_miss":
                    detected_fixture_misses += 1

        if status == "infra":
            infra_errors += 1
            continue
        if status not in ("pass", "fail"):
            continue
        valid += 1
        if score.get("error_kind") == "capability":
            capability_failures += 1
        if action == "tool_call" and _has_expected_routes(expected) \
                and _block_evaluable(score, "routing"):
            routing_evaluated += 1
            routing_block = score.get("routing")
            if isinstance(routing_block, Mapping) and routing_block.get("passed"):
                routing_passed += 1
        if action == "tool_call" and _has_expected_calls(expected) \
                and _block_evaluable(score, "tool_args"):
            tool_evaluated += 1
            tool_block = score.get("tool_args")
            if isinstance(tool_block, Mapping) and tool_block.get("passed"):
                tool_passed += 1
        joint_total += 1
        if score.get("joint") is True:
            joint_passed += 1

    total_cases = len(entries)
    if fixture_misses is None:
        misses_count = detected_fixture_misses
    else:
        misses_count = _count_fixture_misses(fixture_misses)

    return {
        "routing": _metric(routing_passed, routing_evaluated, total_cases),
        "tool_args": _metric(tool_passed, tool_evaluated, total_cases),
        "joint": _metric(joint_passed, joint_total, total_cases),
        "diagnostics": {
            "skills": dict(skill_counts),
            "tools": dict(tool_counts),
            "fields": dict(field_counts),
            "categories": dict(category_counts),
        },
        "execution": {
            "planned": planned if isinstance(planned, int) and planned >= 0
            else total_cases,
            "completed": total_cases,
            "valid": valid,
            "infra_errors": infra_errors,
            "duration_ms": int(duration_ms) if _is_number(duration_ms) else 0,
            "tokens": _sum_tokens(usage),
            "cost": _normalise_cost(cost),
            "fixture_misses": misses_count,
            "capability_failures": capability_failures,
        },
    }


# Contract §2.4 names the aggregation entry point ``aggregate``; keep an alias so
# integration code may import either name.
aggregate = aggregate_metrics


def mark_infrastructure(score: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Turn a provisional score into an infrastructure result (excluded from metrics)."""
    result = dict(score)
    result.pop("pending_judgments", None)
    result.update(status="infra", passed=False, joint=False, error_kind="infrastructure")
    result["routing"] = dict(score.get("routing") or {}, evaluable=False, passed=False, reason=reason)
    result["tool_args"] = dict(score.get("tool_args") or {}, evaluable=False, passed=False, reason=reason)
    return result
