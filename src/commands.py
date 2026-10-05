"""应用层命令：把领域决策翻译成带正确版本号的事件信封。

事件 id 规则：{aggregate_id}#{version}（聚合内唯一即可，账本另有全局校验）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from .ledger import EventStore
from .readmodel import World
from .traceability import freeze_scope

# event_type -> aggregate_type，与契约一致
TYPE_TO_AGGREGATE = {
    "SUPPLIER_REGISTERED": "supplier",
    "WORKSHOP_QUALIFIED": "workshop",
    "INGREDIENT_LOT_RECEIVED": "ingredient_lot",
    "INGREDIENT_LOT_INSPECTED": "ingredient_lot",
    "INGREDIENT_LOT_QUARANTINED": "ingredient_lot",
    "RECIPE_VERSION_PUBLISHED": "recipe",
    "PRODUCTION_SHIFT_OPENED": "production_shift",
    "PRODUCTION_SHIFT_CLOSED": "production_shift",
    "BATCH_PRODUCED": "production_batch",
    "BATCH_ALLERGEN_LABEL_FIXED": "production_batch",
    "BATCH_RELEASED": "production_batch",
    "BATCH_BLOCKED": "production_batch",
    "ORDER_RESERVED": "sales_order",
    "ORDER_CANCELLED": "sales_order",
    "ORDER_RUSH_ACCEPTED": "sales_order",
    "PLAN_REARRANGED": "production_plan",
    "SHIPMENT_DISPATCHED": "shipment",
    "SHIPMENT_RETURNED": "shipment",
    "STOCK_RECHANNELLED": "production_batch",
    "WORK_ATTENDANCE_RECORDED": "worker_settlement",
    "WORK_HOURS_SUPPLEMENTED": "worker_settlement",
    "WEIGH_IN_RECORDED": "worker_settlement",
    "SETTLEMENT_PAID": "worker_settlement",
    "RECALL_STARTED": "ingredient_lot",
    "RECALL_CLOSED": "ingredient_lot",
    "RISK_FLAG_RAISED": "production_batch",
}


def build_event(
    store: EventStore,
    *,
    event_type: str,
    aggregate_id: str,
    payload: dict,
    summary: str,
    occurred_at: datetime | str,
    idempotency_key: str | None = None,
) -> dict:
    """按账本现状构造下一个版本的事件（尚未入账）。"""
    aggregate_type = TYPE_TO_AGGREGATE[event_type]
    version = max(
        (e["version"] for e in store.events if e["aggregate_id"] == aggregate_id),
        default=0,
    ) + 1
    event = {
        "event_id": f"{aggregate_id}#{version}",
        "event_type": event_type,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "occurred_at": occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at,
        "version": version,
        "summary": summary,
        "payload": payload,
    }
    if idempotency_key is not None:
        event["idempotency_key"] = idempotency_key
    return event


def append(store: EventStore, drafts: list[dict]) -> None:
    """入账 planning 等模块产出的草案（草稿已自带信封）。"""
    store.append_many(drafts)


def commit_drafts(
    store: EventStore,
    drafts: list[dict],
    *,
    occurred_at: datetime,
    summaries: dict[str, str] | None = None,
) -> None:
    """planning.reassign 产出的草案只含类型/聚合/载荷，这里补齐信封并入账。"""
    summaries = summaries or {}
    for d in drafts:
        event = build_event(
            store,
            event_type=d["event_type"],
            aggregate_id=d["aggregate_id"],
            payload=d["payload"],
            summary=summaries.get(
                d["event_type"],
                f"重排自动生成：{d['event_type']}",
            ),
            occurred_at=occurred_at,
        )
        store.append(event)


def freeze_recall_batches(store: EventStore, recall_id: str, *, at: datetime) -> list[str]:
    """按召回谱系精确冻结：只为真正受影响且尚未冻结的批次追加 BATCH_BLOCKED。"""
    world = World(store.events)
    scope = freeze_scope(world, recall_id)
    blocked = []
    reason = world.recalls[recall_id]["reason"]
    for batch_id in scope["to_block_batch_ids"]:
        event = build_event(
            store,
            event_type="BATCH_BLOCKED",
            aggregate_id=batch_id,
            payload={"reason": f"召回 {recall_id}：{reason}", "at": at.isoformat(),
                     "recall_id": recall_id},
            summary=f"因召回 {recall_id} 精确冻结成品 {batch_id}",
            occurred_at=at,
        )
        store.append(event)
        blocked.append(batch_id)
    return blocked


def settle(store: EventStore, payment_draft: dict) -> None:
    """结算前最后一道防线：来源号全局只能被支付一次。"""
    world = World(store.events)
    paid: set[str] = set()
    for payment in world.payments.values():
        paid.update(payment["source_refs"])
    overlap = paid.intersection(payment_draft["payload"]["source_refs"])
    if overlap:
        raise ValueError(f"来源已付款，拒绝重复支付：{sorted(overlap)}")
    store.append(payment_draft)


def assert_event_sequence_integrity(events: list[dict]) -> None:
    """供巡检：每个聚合的版本号连续，event_id / 幂等键无重复。"""
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    versions: dict[str, int] = {}
    counts = Counter()
    for e in events:
        if e["event_id"] in seen_ids:
            raise AssertionError(f"event_id 重复：{e['event_id']}")
        seen_ids.add(e["event_id"])
        key = e.get("idempotency_key")
        if key is not None:
            if key in seen_keys:
                raise AssertionError(f"幂等键重复：{key}")
            seen_keys.add(key)
        agg = e["aggregate_id"]
        counts[agg] += 1
        expected = versions.get(agg, 0) + 1
        if e["version"] != expected:
            raise AssertionError(f"{agg} 版本不连续：期望 {expected}，实际 {e['version']}")
        versions[agg] = expected
