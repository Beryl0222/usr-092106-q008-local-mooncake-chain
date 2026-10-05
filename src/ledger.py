"""只追加的事件账本。

约束：
- 事件通过 validator 校验后才能入账；
- event_id 全局唯一、idempotency_key 全局唯一；
- 同一 aggregate 的 version 从 1 起严格递增；
- 不提供任何修改/删除已入账事件的方法——更正只能追加后继事件。
"""
from __future__ import annotations

import json
from pathlib import Path

from .validator import validate_event


class LedgerError(ValueError):
    """事件违反账本约束。"""


class EventStore:
    def __init__(self) -> None:
        self._events: list[dict] = []
        self._event_ids: set[str] = set()
        self._idempotency_keys: set[str] = set()
        self._next_version: dict[str, int] = {}

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def append(self, event: dict) -> dict:
        errors = validate_event(event)
        if errors:
            raise LedgerError("；".join(errors))

        event_id = event["event_id"]
        if event_id in self._event_ids:
            raise LedgerError(f"event_id 重复：{event_id}")

        key = event.get("idempotency_key")
        if key is not None:
            if key in self._idempotency_keys:
                raise LedgerError(f"idempotency_key 已存在，同一线下单不得重复入账：{key}")

        aggregate = event["aggregate_id"]
        expected = self._next_version.get(aggregate, 1)
        if event["version"] != expected:
            raise LedgerError(
                f"{aggregate} 的下一个版本应为 {expected}，收到 {event['version']}"
            )

        stored = dict(event)
        self._events.append(stored)
        self._event_ids.add(event_id)
        if key is not None:
            self._idempotency_keys.add(key)
        self._next_version[aggregate] = expected + 1
        return stored

    def append_many(self, events: list[dict]) -> None:
        for event in events:
            self.append(event)

    def by_type(self, event_type: str) -> list[dict]:
        return [e for e in self._events if e["event_type"] == event_type]

    def for_aggregate(self, aggregate_id: str) -> list[dict]:
        return [e for e in self._events if e["aggregate_id"] == aggregate_id]

    def save_jsonl(self, path: str | Path) -> None:
        Path(path).write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in self._events) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load_jsonl(cls, path: str | Path) -> "EventStore":
        store = cls()
        text = Path(path).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                store.append(json.loads(line))
        return store
