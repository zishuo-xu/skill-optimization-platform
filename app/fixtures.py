"""Deterministic fixture (simulated business-tool return) matching.

The platform never calls real business services during evaluation. Instead each
case carries a list of ``fixtures`` that map a tool name plus argument
conditions to a canned response (see ``docs/CONTRACTS.md`` §2.2/§2.5).

Matching semantics (frozen for this release):

* tool name must be equal, or the fixture name may be ``"*"`` as a fallback
  for any business tool;
* ``match`` is a *deep subset* of the actual arguments: every key declared in
  ``match`` must exist in the arguments and match recursively. Extra argument
  keys are allowed (a fixture may cover many call variants of one tool);
* types are strict (``true`` is not ``1``, ``"1"`` is not ``1``);
* object key order is irrelevant, array length and order must match;
* ``consume="once"`` fixtures are consumed in call order: the first
  still-unconsumed matching fixture wins; ``consume="always"`` may hit again.

Write-then-read chains (optional, per case execution):

* ``capture: [{"var": "mem_text", "path": "args.content"}]`` stores values from
  the *arguments* of the call that hit this fixture;
* a string that is exactly ``"${var}"`` inside a later ``match`` or
  ``response`` is replaced by the captured value (whole value only: no
  concatenation, conditions or loops). A fixture whose ``match`` or
  ``response`` uses a variable that is not captured yet does not match, so a
  model that reads before writing (or never writes) gets a ``fixture_miss``;
  put a variable-free fixture after it to model "not found yet" instead;
* captured state lives for one case execution and is cleared by ``reset``.

Fixtures without ``capture`` and ``${...}`` behave exactly as before.

A call that matches no fixture MUST be recorded as ``fixture_miss`` by the
executor (``FixtureRuntime.resolve`` returns ``None`` and appends to
``misses``); the executor must never fall back to a real service.

The module is pure and in-memory: no database, network or global state.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Mapping, Sequence

CONSUME_ALWAYS = "always"
CONSUME_ONCE = "once"
CONSUME_MODES = (CONSUME_ALWAYS, CONSUME_ONCE)

VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VAR_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


class _Unbound:
    """Placeholder for a ``${var}`` that has not been captured yet."""

    def __init__(self, name: str) -> None:
        self.name = name


def parse_path(path: str) -> list[str | int] | None:
    """``"args.items[0].id"`` (optionally ``$.``-prefixed) -> keys / indices."""
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:].lstrip(".")
    if not text:
        return None
    tokens: list[str | int] = []
    position = 0
    for found in _PATH_TOKEN.finditer(text):
        gap = text[position:found.start()]
        if gap not in ("", "."):
            return None
        tokens.append(int(found.group(2)) if found.group(2) is not None else found.group(1))
        position = found.end()
    if text[position:] not in ("",):
        return None
    return tokens or None


def lookup_path(value: Any, tokens: Sequence[str | int]) -> tuple[bool, Any]:
    current = value
    for token in tokens:
        if isinstance(token, int):
            if not isinstance(current, (list, tuple)) or token >= len(current):
                return False, None
            current = current[token]
        else:
            if not isinstance(current, Mapping) or token not in current:
                return False, None
            current = current[token]
    return True, current


def substitute(value: Any, state: Mapping[str, Any]) -> Any:
    """Replace whole-string ``"${var}"`` values; unknown vars become ``_Unbound``."""
    if isinstance(value, str):
        found = _VAR_REF.match(value)
        if not found:
            return value
        name = found.group(1)
        return copy.deepcopy(state[name]) if name in state else _Unbound(name)
    if isinstance(value, Mapping):
        return {key: substitute(item, state) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [substitute(item, state) for item in value]
    return value


def unbound_vars(value: Any) -> list[str]:
    if isinstance(value, _Unbound):
        return [value.name]
    if isinstance(value, Mapping):
        return [name for item in value.values() for name in unbound_vars(item)]
    if isinstance(value, (list, tuple)):
        return [name for item in value for name in unbound_vars(item)]
    return []


def _restore_unbound(value: Any) -> Any:
    """Responses keep an uncaptured ``${var}`` literally (visible authoring error)."""
    if isinstance(value, _Unbound):
        return "${" + value.name + "}"
    if isinstance(value, Mapping):
        return {key: _restore_unbound(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_restore_unbound(item) for item in value]
    return value


def capture_error(capture: Any) -> str | None:
    """Validation message for a fixture ``capture`` list, or ``None``."""
    if capture is None:
        return None
    if not isinstance(capture, (list, tuple)):
        return "capture 必须是数组"
    for index, item in enumerate(capture):
        if not isinstance(item, Mapping):
            return f"capture[{index}] 必须是对象"
        if not isinstance(item.get("var"), str) or not VAR_NAME.match(item["var"]):
            return f"capture[{index}].var 必须是字母、数字或下划线组成的变量名"
        source = item.get("from", "arguments")
        if source != "arguments":
            return f"capture[{index}].from 只支持 arguments（从这次调用的参数取值）"
        if not isinstance(item.get("path"), str) or parse_path(item["path"]) is None:
            return f"capture[{index}].path 必须是参数路径，如 args.content 或 items[0].id"
    return None


def json_deep_equal(left: Any, right: Any) -> bool:
    """Strict, order-sensitive JSON deep equality (used for exact comparisons).

    Object key order is ignored; types are strict except that all JSON numbers
    compare by value (``1 == 1.0``); ``True`` is never equal to ``1``; a missing
    key is never equal to an explicit ``None``.
    """

    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if len(left) != len(right):
            return False
        return all(key in right and json_deep_equal(value, right[key])
                   for key, value in left.items())
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(
            json_deep_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def deep_match(condition: Any, value: Any) -> bool:
    """Deep-subset match: *condition* describes the required part of *value*.

    Objects may omit keys; arrays must have the same length and match
    positionally; scalars use strict equality.
    """

    if isinstance(condition, Mapping):
        if not isinstance(value, Mapping):
            return False
        return all(key in value and deep_match(expected, value[key])
                   for key, expected in condition.items())
    if isinstance(condition, (list, tuple)):
        if not isinstance(value, (list, tuple)) or len(condition) != len(value):
            return False
        return all(deep_match(expected, actual)
                   for expected, actual in zip(condition, value))
    return json_deep_equal(condition, value)


def arguments_match(condition: Mapping[str, Any] | None,
                    arguments: Mapping[str, Any] | None) -> bool:
    """Return whether *arguments* satisfies the fixture argument *condition*."""

    if condition is None:
        return True
    if not isinstance(condition, Mapping):
        return False
    args = arguments if isinstance(arguments, Mapping) else {}
    return deep_match(condition, args)


def _mismatch_paths(condition: Any, value: Any, path: str = "$") -> list[str]:
    """Return a small, value-free list of paths that prevented a match."""
    if isinstance(condition, Mapping):
        if not isinstance(value, Mapping):
            return [path]
        paths: list[str] = []
        for key, expected in condition.items():
            child = f"{path}.{key}"
            if key not in value:
                paths.append(child)
            else:
                paths.extend(_mismatch_paths(expected, value[key], child))
        return paths
    if isinstance(condition, (list, tuple)):
        if not isinstance(value, (list, tuple)) or len(condition) != len(value):
            return [path]
        paths: list[str] = []
        for index, (expected, actual) in enumerate(zip(condition, value)):
            paths.extend(_mismatch_paths(expected, actual, f"{path}[{index}]"))
        return paths
    return [] if json_deep_equal(condition, value) else [path]


def _fixture_entry(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"fixture[{index}] 缺少字符串字段 name")
    match = raw.get("match")
    if match is None:
        match = {}
    if not isinstance(match, Mapping):
        raise ValueError(f"fixture[{index}].match 必须是 JSON 对象")
    consume = raw.get("consume") or CONSUME_ALWAYS
    if consume not in CONSUME_MODES:
        raise ValueError(f"fixture[{index}].consume 非法: {consume!r}")
    error = capture_error(raw.get("capture"))
    if error:
        raise ValueError(f"fixture[{index}].{error}")
    return {
        "index": index,
        "name": name,
        "match": dict(match),
        "response": copy.deepcopy(raw.get("response")),
        "consume": consume,
        "capture": [{"var": item["var"], "tokens": parse_path(item["path"])}
                    for item in raw.get("capture") or []],
    }


class FixtureMatcher:
    """Stateful fixture matcher for one case execution.

    ``match()`` always returns a dict: ``{"matched": True, ...}`` on a hit and
    ``{"matched": False, "reason": "no_fixture_match", ...}`` on a miss, so the
    caller can turn a miss into a visible ``fixture_miss`` record.
    """

    def __init__(self, fixtures: Iterable[Mapping[str, Any]] | None = None):
        self._entries: list[dict[str, Any]] = [
            _fixture_entry(raw, index) for index, raw in enumerate(fixtures or [])
        ]
        self._consumed: set[int] = set()
        # Values captured from earlier calls of this case execution.
        self.state: dict[str, Any] = {}

    # -- public API -------------------------------------------------------- #
    def reset(self) -> None:
        """Forget ``once`` consumption and captured values (repeating a case)."""

        self._consumed.clear()
        self.state.clear()

    def _condition(self, entry: Mapping[str, Any]) -> Any:
        return substitute(entry["match"], self.state)

    def _response_unbound(self, entry: Mapping[str, Any]) -> list[str]:
        """Variables the response needs that neither state nor this hit provides."""
        own = {item["var"] for item in entry.get("capture") or ()}
        return [name for name in unbound_vars(substitute(entry["response"], self.state))
                if name not in own]

    @property
    def consumed_indices(self) -> list[int]:
        return sorted(self._consumed)

    def diagnose_miss(self, name: str,
                      arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Explain a miss without consuming or exposing fixture responses."""
        args = dict(arguments) if isinstance(arguments, Mapping) else {}
        if not self._entries:
            return {"reason": "empty_fixtures", "fixture_count": 0,
                    "available_tools": [], "candidate_indices": []}
        candidates = [entry for entry in self._entries
                      if entry["name"] in (name, "*")]
        available = sorted({entry["name"] for entry in self._entries
                            if entry["name"] != "*"})
        if not candidates:
            return {"reason": "tool_name_mismatch", "fixture_count": len(self._entries),
                    "available_tools": available, "candidate_indices": []}
        active = [entry for entry in candidates if entry["index"] not in self._consumed]
        matching_consumed = [entry for entry in candidates
                             if entry["index"] in self._consumed
                             and arguments_match(self._condition(entry), args)]
        matching_active = [entry for entry in active
                           if arguments_match(self._condition(entry), args)
                           and not self._response_unbound(entry)]
        if matching_active:
            return {"reason": "no_miss", "fixture_count": len(self._entries),
                    "available_tools": available,
                    "candidate_indices": [entry["index"] for entry in matching_active]}
        if matching_consumed:
            return {"reason": "fixtures_consumed", "fixture_count": len(self._entries),
                    "available_tools": available,
                    "candidate_indices": [entry["index"] for entry in matching_consumed],
                    "consumed_matching_indices": [entry["index"] for entry in matching_consumed]}
        mismatches = {str(entry["index"]): _mismatch_paths(self._condition(entry), args)
                      for entry in active}
        unbound = sorted({name for entry in active
                          for name in unbound_vars(self._condition(entry))
                          + self._response_unbound(entry)})
        awaiting = bool(unbound) and any(not paths for paths in mismatches.values())
        diagnosis = {"reason": "awaiting_capture" if awaiting else "arguments_mismatch",
                     "fixture_count": len(self._entries),
                     "available_tools": available,
                     "candidate_indices": [entry["index"] for entry in active],
                     "mismatch_paths": mismatches}
        if unbound:
            # The fixture expects a value captured by an earlier call (e.g. a
            # write before this read) that never happened in this execution.
            diagnosis["unbound_vars"] = unbound
        return diagnosis

    def match(self, name: str,
              arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args: dict[str, Any] = dict(arguments) if isinstance(arguments, Mapping) else {}
        for entry in self._entries:
            if entry["name"] not in (name, "*"):
                continue
            if entry["index"] in self._consumed:
                continue
            if not arguments_match(self._condition(entry), args):
                continue
            if self._response_unbound(entry):
                continue  # its response depends on a value not captured yet
            if entry["consume"] == CONSUME_ONCE:
                self._consumed.add(entry["index"])
            captured: dict[str, Any] = {}
            for item in entry.get("capture") or ():
                found, value = lookup_path(args, item["tokens"])
                if found:
                    captured[item["var"]] = copy.deepcopy(value)
            self.state.update(captured)
            response = _restore_unbound(substitute(entry["response"], self.state))
            return {
                "matched": True,
                "tool": name,
                "arguments": copy.deepcopy(args),
                "response": response,
                **({"captured": captured} if captured else {}),
                "fixture_index": entry["index"],
                "consume": entry["consume"],
                "consumed": entry["consume"] == CONSUME_ONCE,
            }
        return {
            "matched": False,
            "tool": name,
            "arguments": copy.deepcopy(args),
            "reason": "no_fixture_match",
        }

    def resolve(self, name: str,
                arguments: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """Alias of :meth:`match` returning ``None`` on a miss."""

        result = self.match(name, arguments)
        return result if result["matched"] else None


class FixtureRuntime(FixtureMatcher):
    """Executor-facing wrapper that also records misses.

    ``resolve(name, arguments)`` returns the matched fixture dict or ``None``;
    every miss is appended to :attr:`misses` as ``{"tool", "arguments"}`` which
    the executor copies into ``CaseExecutionResult.fixture_misses``.
    """

    def __init__(self, fixtures: Iterable[Mapping[str, Any]] | None = None):
        super().__init__(fixtures)
        self.misses: list[dict[str, Any]] = []

    def resolve(self, name: str,
                arguments: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        result = self.match(name, arguments)
        if not result["matched"]:
            self.misses.append({
                "tool": name,
                "arguments": copy.deepcopy(result["arguments"]),
            })
            return None
        return result

    def reset(self) -> None:
        super().reset()
        self.misses.clear()


def match_fixture(fixtures: Sequence[Mapping[str, Any]] | None, name: str,
                  arguments: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """One-shot match helper; does not keep ``once`` consumption state.

    Use :class:`FixtureMatcher` / :class:`FixtureRuntime` for multi-call cases.
    """

    result = FixtureMatcher(fixtures).match(name, arguments)
    return result if result["matched"] else None
