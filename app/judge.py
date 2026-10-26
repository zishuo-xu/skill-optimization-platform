"""Judge model for tool-argument values (``compare-v2`` ``semantic`` mode).

The scorer keeps every structural decision in code (route, tool name, keys,
required fields). Only values it cannot settle by literal keyword hits or
lenient equality are sent here, all pending values of one case in a single
request. Verdicts are cached by (judge model identity, prompt version, field,
expected, actual, criteria): an identical output always gets the identical
verdict, which keeps baseline/candidate paired validation stable and avoids
paying twice. A failed judge call is an infrastructure problem for that case
(excluded from metrics), never a silent pass or fail.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Callable, Mapping, Sequence

from . import event_types, persistence
from .adapters.base import AdapterError
from .adapters.registry import build_adapter
from .evaluation import JUDGE_PROMPT_VERSION, json_dumps_compact

__all__ = ["JudgeError", "ValueJudge", "judge_config_from_env", "judge_identity"]

#: Providers that can serve as a judge model.
JUDGE_PROVIDERS = ("openai_http", "enterprise_ws")

SYSTEM_PROMPT = """你是工具调用参数的评分员。每一项给出一个参数字段的「预期值」和模型实际给出的「实际值」，判断实际值是否满足预期。

判断标准：
- 看含义，不看字面：同义词、不同说法、语序、中英文、大小写、单位换算一致的，都算满足；
- 给了「关键词」时，实际值体现了这些关键词的含义即算满足，不要求字面出现；
- 给了「要求」时，以要求为准；
- 实际值与预期含义相反、指向别的对象、缺少关键信息或明显多加了预期没有的意思，判为不满足；
- 不要因为格式、措辞更长或更短而判为不满足。

只输出 JSON，不要输出其他内容，格式：
{"results": [{"id": "<项目 id>", "passed": true 或 false, "reason": "<不超过 40 字的理由>"}]}
每个输入项目都必须有且只有一条结果。"""


class JudgeError(Exception):
    """Judge model unavailable or its answer unusable (infrastructure for the case)."""

    def __init__(self, message: str, *, code: str = "judge_failed"):
        super().__init__(message)
        self.code = code
        self.message = message


def judge_config_from_env(env: Mapping[str, Any]) -> dict[str, Any] | None:
    """The judge model config frozen in the environment, or ``None``."""
    config = ((env.get("scorer_config") or {}).get("judge")) or {}
    if not isinstance(config, Mapping):
        return None
    provider = str(config.get("provider") or "").strip()
    if provider not in JUDGE_PROVIDERS:
        return None
    return dict(config)


def judge_identity(config: Mapping[str, Any]) -> str:
    """Model identity that becomes part of every cache key (no credentials)."""
    return json_dumps_compact({
        "provider": config.get("provider"),
        "endpoint": config.get("base_url") or config.get("url") or config.get("ws_url") or "",
        "model": config.get("model") or "",
        "temperature": config.get("temperature", 0),
        "prompt": JUDGE_PROMPT_VERSION,
    })


def _cache_key(identity: str, item_key: str) -> str:
    return hashlib.sha256(f"{identity}\n{item_key}".encode("utf-8")).hexdigest()


def _item_payload(index: int, item: Mapping[str, Any]) -> dict[str, Any]:
    criteria = item.get("criteria") or {}
    payload: dict[str, Any] = {
        "id": f"v{index + 1}",
        "字段": item.get("path"),
        "预期值": item.get("expected"),
        "实际值": item.get("actual"),
    }
    if item.get("tool"):
        payload["工具"] = item["tool"]
    if criteria.get("keywords"):
        payload["关键词"] = list(criteria["keywords"])
    if criteria.get("requirement"):
        payload["要求"] = criteria["requirement"]
    return payload


def build_messages(items: Sequence[Mapping[str, Any]], *, query: str | None = None) -> list[dict[str, Any]]:
    user = {"待判项目": [_item_payload(index, item) for index, item in enumerate(items)]}
    if query:
        user["用户原始请求"] = query
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=1)},
    ]


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_verdicts(content: str, count: int) -> dict[str, dict[str, Any]]:
    """Parse ``{"results": [...]}``; every id ``v1..vN`` must be answered once."""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        match = _JSON_BLOCK.search(text)
        if not match:
            raise JudgeError("评分模型没有返回 JSON", code="judge_parse_failed")
        try:
            data = json.loads(match.group(0))
        except ValueError as exc:
            raise JudgeError("评分模型返回的 JSON 无法解析", code="judge_parse_failed") from exc
    results = data.get("results") if isinstance(data, Mapping) else None
    if not isinstance(results, list):
        raise JudgeError("评分模型输出缺少 results 数组", code="judge_parse_failed")
    verdicts: dict[str, dict[str, Any]] = {}
    for entry in results:
        if not isinstance(entry, Mapping):
            continue
        ident = str(entry.get("id") or "")
        passed = entry.get("passed")
        if isinstance(passed, str):
            passed = passed.strip().lower() in ("true", "yes", "1", "是", "通过")
        if not ident or not isinstance(passed, bool) or ident in verdicts:
            continue
        verdicts[ident] = {"passed": passed, "reason": str(entry.get("reason") or "").strip()[:200]}
    missing = [f"v{index + 1}" for index in range(count) if f"v{index + 1}" not in verdicts]
    if missing:
        raise JudgeError(f"评分模型漏判了 {len(missing)} 项", code="judge_parse_failed")
    return verdicts


class ValueJudge:
    """Judge pending value comparisons for one environment (thread-safe use)."""

    def __init__(self, config: Mapping[str, Any], *, adapter: Any = None):
        self.config = dict(config)
        self.identity = judge_identity(self.config)
        self.model = str(self.config.get("model") or "")
        self._adapter = adapter

    @classmethod
    def from_env(cls, env: Mapping[str, Any]) -> "ValueJudge | None":
        config = judge_config_from_env(env)
        return cls(config) if config else None

    @property
    def adapter(self) -> Any:
        if self._adapter is None:
            self._adapter = build_adapter(self.config)
        return self._adapter

    def judge(self, items: Sequence[Mapping[str, Any]], *,
              query: str | None = None,
              emit: Callable[..., Any] | None = None,
              runtime: Mapping[str, Any] | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Return ``(verdicts by item key, stats)`` for pending items.

        ``stats``: ``{"items", "cached", "judged", "passed", "usage"}``.
        Raises :class:`JudgeError` when the model cannot be used.
        """
        unique: dict[str, Mapping[str, Any]] = {}
        for item in items:
            if item.get("key") and item["key"] not in unique:
                unique[item["key"]] = item
        cache_keys = {key: _cache_key(self.identity, key) for key in unique}
        cached = persistence.get_judgments(list(cache_keys.values()))
        verdicts: dict[str, dict[str, Any]] = {}
        todo: list[Mapping[str, Any]] = []
        for key, item in unique.items():
            hit = cached.get(cache_keys[key])
            if hit is not None:
                verdicts[key] = {"passed": hit["passed"], "reason": hit["reason"], "cached": True}
            else:
                todo.append(item)
        usage: dict[str, int] = {}
        if todo:
            messages = build_messages(todo, query=query)
            config = {
                "temperature": self.config.get("temperature", 0) or 0,
                "max_tokens": int(self.config.get("max_tokens") or 1024),
                "call_id": f"judge_{uuid.uuid4().hex[:12]}",
                "stream": bool(self.config.get("stream", False)),
                **dict(runtime or {}),
            }
            try:
                result = self.adapter.invoke(messages, config, [], emit=emit)
            except AdapterError as exc:
                raise JudgeError(f"评分模型调用失败：{exc.message}", code=exc.code) from exc
            except Exception as exc:  # noqa: BLE001 - adapters are external code
                raise JudgeError(f"评分模型调用失败：{type(exc).__name__}: {exc}") from exc
            usage = dict((result or {}).get("usage") or {})
            parsed = parse_verdicts(str((result or {}).get("content") or ""), len(todo))
            fresh: dict[str, dict[str, Any]] = {}
            for index, item in enumerate(todo):
                verdict = parsed[f"v{index + 1}"]
                verdicts[item["key"]] = {**verdict, "cached": False}
                fresh[cache_keys[item["key"]]] = verdict
            persistence.save_judgments(fresh, self.model)
        stats = {
            "items": len(unique),
            "cached": len(unique) - len(todo),
            "judged": len(todo),
            "passed": sum(1 for verdict in verdicts.values() if verdict["passed"]),
            "usage": usage,
        }
        return verdicts, stats


def judge_event_payload(stats: Mapping[str, Any], verdicts: Mapping[str, Mapping[str, Any]],
                        items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_key = {item.get("key"): item for item in items}
    return {
        "items": stats.get("items"),
        "cached": stats.get("cached"),
        "judged": stats.get("judged"),
        "passed": stats.get("passed"),
        "usage": stats.get("usage") or None,
        "verdicts": [
            {"path": (by_key.get(key) or {}).get("path"), "passed": verdict.get("passed"),
             "reason": verdict.get("reason"), "cached": verdict.get("cached", False)}
            for key, verdict in verdicts.items()
        ][:20],
    }


EVENT_COMPLETED = event_types.JUDGE_COMPLETED
EVENT_FAILED = event_types.JUDGE_FAILED
