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

A call that matches no fixture MUST be recorded as ``fixture_miss`` by the
executor (``FixtureRuntime.resolve`` returns ``None`` and appends to
``misses``); the executor must never fall back to a real service.

The module is pure and in-memory: no database, network or global state.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping, Sequence

CONSUME_ALWAYS = "always"
CONSUME_ONCE = "once"
CONSUME_MODES = (CONSUME_ALWAYS, CONSUME_ONCE)


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
    return {
        "index": index,
        "name": name,
        "match": dict(match),
        "response": copy.deepcopy(raw.get("response")),
        "consume": consume,
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

    # -- public API -------------------------------------------------------- #
    def reset(self) -> None:
        """Forget ``once`` consumption (useful when repeating a case)."""

        self._consumed.clear()

    @property
    def consumed_indices(self) -> list[int]:
        return sorted(self._consumed)

    def match(self, name: str,
              arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args: dict[str, Any] = dict(arguments) if isinstance(arguments, Mapping) else {}
        for entry in self._entries:
            if entry["name"] not in (name, "*"):
                continue
            if entry["index"] in self._consumed:
                continue
            if not arguments_match(entry["match"], args):
                continue
            if entry["consume"] == CONSUME_ONCE:
                self._consumed.add(entry["index"])
            return {
                "matched": True,
                "tool": name,
                "arguments": copy.deepcopy(args),
                "response": copy.deepcopy(entry["response"]),
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
