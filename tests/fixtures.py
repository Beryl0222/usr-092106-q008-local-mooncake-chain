"""端到端场景夹具：2026 年中秋季两家工坊的协作链。

供应户 → 原料批次/检验 → 配方版本 → 班次 → 成品/标签/放行
→ 预留订单/发货/退货 → 称重工时与结算 → 异常原料召回与精确冻结。
"""
from __future__ import annotations

from datetime import datetime

from src.commands import build_event
from src.ledger import EventStore

TZ = "+08:00"


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def emit(
    store: EventStore,
    event_type: str,
    aggregate_id: str,
    payload: dict,
    when: str,
    *,
    summary: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    event = build_event(
        store,
        event_type=event_type,
        aggregate_id=aggregate_id,
        payload=payload,
        summary=summary or event_type,
        occurred_at=_dt(when),
        idempotency_key=idempotency_key,
    )
    store.append(event)
    return event


def build_store(*, with_recall: bool = True, with_payment: bool = True) -> EventStore:
    store = EventStore()

    # ---- 供应户 -------------------------------------------------------------
    emit(store, "SUPPLIER_REGISTERED", "SUP-S01", {
        "name": "王秀兰", "village": "屯城村", "supplies": ["花生仁"],
        "bank_account_last4": "1032",
    }, "2026-09-01T09:00:00+08:00", summary="登记花生仁供应户")
    emit(store, "SUPPLIER_REGISTERED", "SUP-S02", {
        "name": "李茂才", "village": "枫木村", "supplies": ["叉烧"],
    }, "2026-09-01T09:10:00+08:00", summary="登记叉烧供应户")
    emit(store, "SUPPLIER_REGISTERED", "SUP-S03", {
        "name": "张燕", "village": "屯城村", "supplies": ["芝麻"],
    }, "2026-09-01T09:20:00+08:00", summary="登记芝麻供应户")

    # ---- 工坊资质 -----------------------------------------------------------
    emit(store, "WORKSHOP_QUALIFIED", "WS-A", {
        "name": "屯城老街手作工坊", "qualification_no": "TC-SC-2026-018",
        "valid_until": "2026-12-31", "daily_capacity_kg": 60,
    }, "2026-09-01T10:00:00+08:00", summary="屯城工坊资质备案")
    emit(store, "WORKSHOP_QUALIFIED", "WS-B", {
        "name": "枫木圩饼社", "qualification_no": "TC-SC-2026-021",
        "valid_until": "2026-12-31", "daily_capacity_kg": 40,
    }, "2026-09-01T10:10:00+08:00", summary="枫木工坊资质备案")

    # ---- 原料批次按天进场与检验 ---------------------------------------------
    def receive(lot_id, ingredient, supplier, qty, day, shelf, price=None):
        payload = {
            "ingredient": ingredient, "supplier_id": supplier, "quantity_kg": qty,
            "received_on": day, "shelf_life_days": shelf,
        }
        if price is not None:
            payload["unit_price"] = price
        emit(store, "INGREDIENT_LOT_RECEIVED", lot_id, payload,
             f"{day}T08:30:00+08:00", summary=f"{ingredient}进场 {qty}kg")

    def inspect(lot_id, result, when, note=None):
        payload = {"result": result, "inspected_at": when}
        if note:
            payload["note"] = note
        emit(store, "INGREDIENT_LOT_INSPECTED", lot_id, payload, when,
             summary=f"检验{result}")

    receive("LOT-PN-0915", "花生仁", "SUP-S01", 20, "2026-09-15", 90, 18.0)
    inspect("LOT-PN-0915", "合格", "2026-09-15T10:00:00+08:00")
    receive("LOT-FL-0915", "面粉", "SUP-S03", 25, "2026-09-15", 120, 6.0)
    inspect("LOT-FL-0915", "合格", "2026-09-15T10:10:00+08:00")
    receive("LOT-CS-0916", "叉烧", "SUP-S02", 15, "2026-09-16", 12, 40.0)
    inspect("LOT-CS-0916", "合格", "2026-09-16T09:30:00+08:00")
    receive("LOT-SES-0916", "芝麻", "SUP-S03", 5, "2026-09-16", 90, 22.0)
    inspect("LOT-SES-0916", "合格", "2026-09-16T09:40:00+08:00")
    receive("LOT-CS-0918", "叉烧", "SUP-S02", 8, "2026-09-18", 12, 40.0)
    inspect("LOT-CS-0918", "合格", "2026-09-18T09:00:00+08:00",
            note="外观略深，检后放行，后被供应户自查发现腌制温控异常")
    receive("LOT-PN-0918", "花生仁", "SUP-S01", 8, "2026-09-18", 90, 18.0)
    inspect("LOT-PN-0918", "合格", "2026-09-18T09:10:00+08:00")

    # ---- 地方配方（各自获准，过敏原按各自实际声明） --------------------------
    emit(store, "RECIPE_VERSION_PUBLISHED", "RECIPE-CSWR", {
        "recipe_name": "叉烧五仁月饼", "recipe_version": 1, "workshop_id": "WS-A",
        "ingredients": [
            {"ingredient": "叉烧", "quantity_kg": 6},
            {"ingredient": "花生仁", "quantity_kg": 8},
            {"ingredient": "芝麻", "quantity_kg": 1.5},
            {"ingredient": "面粉", "quantity_kg": 8},
        ],
        "declared_allergens": ["花生", "芝麻", "大豆", "小麦麸质"],
        "approved": True, "published_at": "2026-09-18T15:00:00+08:00",
    }, "2026-09-18T15:00:00+08:00", summary="屯城坊叉烧五仁配方 v1 获准")
    emit(store, "RECIPE_VERSION_PUBLISHED", "RECIPE-CSWR-B", {
        "recipe_name": "枫木叉烧五仁月饼（少芝麻版）", "recipe_version": 1,
        "workshop_id": "WS-B",
        "ingredients": [
            {"ingredient": "叉烧", "quantity_kg": 6},
            {"ingredient": "花生仁", "quantity_kg": 7},
            {"ingredient": "面粉", "quantity_kg": 8},
        ],
        "declared_allergens": ["花生", "大豆", "小麦麸质"],
        "approved": True, "published_at": "2026-09-18T15:30:00+08:00",
    }, "2026-09-18T15:30:00+08:00", summary="枫木坊地方变体配方 v1 获准")

    # ---- 班次（师傅产能） ----------------------------------------------------
    emit(store, "PRODUCTION_SHIFT_OPENED", "SHIFT-A-0920", {
        "workshop_id": "WS-A", "shift_date": "2026-09-20",
        "master_id": "MST-01", "planned_kg": 44,
    }, "2026-09-20T06:00:00+08:00", summary="屯城坊早班开班")
    emit(store, "PRODUCTION_SHIFT_OPENED", "SHIFT-B-0920", {
        "workshop_id": "WS-B", "shift_date": "2026-09-20",
        "master_id": "MST-09", "planned_kg": 18,
    }, "2026-09-20T06:10:00+08:00", summary="枫木坊早班开班")

    # ---- 成品批次（实际投料钉到批次） ----------------------------------------
    def produce(batch_id, ws, shift, recipe, version, qty, at, best_before, lots):
        emit(store, "BATCH_PRODUCED", batch_id, {
            "workshop_id": ws, "shift_id": shift, "recipe_id": recipe,
            "recipe_version": version, "quantity_kg": qty,
            "produced_at": at, "best_before": best_before,
            "ingredient_lots": [
                {"lot_id": lid, "ingredient": ing, "quantity_kg": q}
                for lid, ing, q in lots
            ],
        }, at, summary=f"手工成型 {qty}kg")

    produce("BATCH-A-0920-01", "WS-A", "SHIFT-A-0920", "RECIPE-CSWR", 1, 28,
            "2026-09-20T11:00:00+08:00", "2026-10-05", [
                ("LOT-CS-0916", "叉烧", 8),
                ("LOT-PN-0915", "花生仁", 10),
                ("LOT-SES-0916", "芝麻", 2),
                ("LOT-FL-0915", "面粉", 8),
            ])
    produce("BATCH-A-0920-02", "WS-A", "SHIFT-A-0920", "RECIPE-CSWR", 1, 16,
            "2026-09-20T14:00:00+08:00", "2026-10-05", [
                ("LOT-CS-0918", "叉烧", 6),
                ("LOT-PN-0918", "花生仁", 6),
                ("LOT-FL-0915", "面粉", 4),
            ])
    produce("BATCH-B-0920-01", "WS-B", "SHIFT-B-0920", "RECIPE-CSWR-B", 1, 18,
            "2026-09-20T11:30:00+08:00", "2026-10-05", [
                ("LOT-CS-0916", "叉烧", 6),
                ("LOT-PN-0915", "花生仁", 6),
                ("LOT-FL-0915", "面粉", 6),
            ])

    # ---- 标签按实际投料固定 --------------------------------------------------
    def fix_label(batch_id, allergens, recipe_version, when):
        emit(store, "BATCH_ALLERGEN_LABEL_FIXED", batch_id, {
            "label_allergens": allergens, "fixed_at": when,
            "recipe_version_snapshot": recipe_version,
        }, when, summary="固定过敏原标签")

    fix_label("BATCH-A-0920-01", ["花生", "芝麻", "大豆", "小麦麸质"], 1,
              "2026-09-20T12:00:00+08:00")
    fix_label("BATCH-A-0920-02", ["花生", "大豆", "小麦麸质"], 1,
              "2026-09-20T15:00:00+08:00")
    fix_label("BATCH-B-0920-01", ["花生", "大豆", "小麦麸质"], 1,
              "2026-09-20T12:30:00+08:00")

    # ---- 放行与公开码 --------------------------------------------------------
    for bid, token, when in [
        ("BATCH-A-0920-01", "qr-A092001", "2026-09-20T16:00:00+08:00"),
        ("BATCH-A-0920-02", "qr-A092002", "2026-09-20T16:10:00+08:00"),
        ("BATCH-B-0920-01", "qr-B092001", "2026-09-20T16:20:00+08:00"),
    ]:
        emit(store, "BATCH_RELEASED", bid, {
            "released_at": when, "qr_public_token": token,
        }, when, summary="质检放行，赋公开码")

    emit(store, "PRODUCTION_SHIFT_CLOSED", "SHIFT-A-0920",
         {"closed_at": "2026-09-20T18:00:00+08:00"},
         "2026-09-20T18:00:00+08:00", summary="屯城坊收班")
    emit(store, "PRODUCTION_SHIFT_CLOSED", "SHIFT-B-0920",
         {"closed_at": "2026-09-20T17:30:00+08:00"},
         "2026-09-20T17:30:00+08:00", summary="枫木坊收班")

    # ---- 电商预售预留 --------------------------------------------------------
    def reserve(oid, channel, qty, batch, needed, when, plan="PLAN-0921"):
        emit(store, "ORDER_RESERVED", oid, {
            "channel": channel, "quantity_kg": qty, "needed_by": needed,
            "reserved_batch_id": batch, "reserved_at": when, "plan_id": plan,
        }, when, summary=f"{channel}预留 {qty}kg")

    reserve("O-ECOM-1001", "电商预售", 12, "BATCH-A-0920-01", "2026-09-25",
            "2026-09-21T09:00:00+08:00")
    reserve("O-ECOM-1002", "电商预售", 8, "BATCH-A-0920-02", "2026-09-25",
            "2026-09-21T09:05:00+08:00")
    reserve("O-GROUP-2001", "线下团购", 10, "BATCH-B-0920-01", "2026-09-26",
            "2026-09-21T09:10:00+08:00")
    reserve("O-ECOM-1003", "电商预售", 10, "BATCH-A-0920-01", "2026-09-28",
            "2026-09-21T09:15:00+08:00")

    # ---- 发货与消费者退货 ----------------------------------------------------
    emit(store, "SHIPMENT_DISPATCHED", "SHIP-3001", {
        "order_id": "O-ECOM-1001", "batch_id": "BATCH-A-0920-01",
        "quantity_kg": 12, "channel": "电商预售",
        "dispatched_at": "2026-09-22T08:00:00+08:00",
    }, "2026-09-22T08:00:00+08:00", summary="电商仓发车 12kg")
    emit(store, "SHIPMENT_RETURNED", "SHIP-3001", {
        "order_id": "O-ECOM-1001", "batch_id": "BATCH-A-0920-01",
        "quantity_kg": 1.5, "returned_at": "2026-09-23T14:00:00+08:00",
        "reason": "消费者七天无理由退货",
    }, "2026-09-23T14:00:00+08:00", summary="消费者退回 1.5kg")

    # ---- 线下称重、记工、工时补录 --------------------------------------------
    emit(store, "WEIGH_IN_RECORDED", "SETTLE-LEDGER", {
        "weigh_no": "WEIGH-5001", "supplier_id": "SUP-S01",
        "lot_id": "LOT-PN-0915", "quantity_kg": 10, "unit_price": 18.0,
        "weighed_at": "2026-09-23T17:00:00+08:00",
    }, "2026-09-23T17:00:00+08:00", idempotency_key="WEIGH-5001",
         summary="花生仁线下称重 10kg")
    emit(store, "WEIGH_IN_RECORDED", "SETTLE-LEDGER", {
        "weigh_no": "WEIGH-5002", "supplier_id": "SUP-S02",
        "lot_id": "LOT-CS-0916", "quantity_kg": 14, "unit_price": 40.0,
        "weighed_at": "2026-09-23T17:10:00+08:00",
    }, "2026-09-23T17:10:00+08:00", idempotency_key="WEIGH-5002",
         summary="叉烧线下称重 14kg")
    emit(store, "WORK_ATTENDANCE_RECORDED", "SETTLE-LEDGER", {
        "worker_id": "WKR-01", "shift_id": "SHIFT-A-0920",
        "hours": 8, "hourly_rate": 25.0,
        "recorded_at": "2026-09-20T18:10:00+08:00",
    }, "2026-09-20T18:10:00+08:00", summary="当班记工 8 小时")
    emit(store, "WORK_HOURS_SUPPLEMENTED", "SETTLE-LEDGER", {
        "worker_id": "WKR-02", "shift_id": "SHIFT-A-0920",
        "hours": 3, "hourly_rate": 20.0, "supplement_no": "SUPP-6001",
        "supplemented_at": "2026-09-21T19:00:00+08:00",
    }, "2026-09-21T19:00:00+08:00", idempotency_key="SUPP-6001",
         summary="临时帮工补录 3 小时")

    if with_payment:
        emit(store, "SETTLEMENT_PAID", "PAY-7001", {
            "payee_id": "SUP-S01", "amount": 180.0,
            "source_refs": ["WEIGH-5001"],
            "paid_at": "2026-09-24T09:00:00+08:00",
        }, "2026-09-24T09:00:00+08:00", idempotency_key="PAY-7001",
             summary="支付花生仁货款 180 元")

    # ---- 异常原料召回 --------------------------------------------------------
    if with_recall:
        emit(store, "RECALL_STARTED", "LOT-CS-0918", {
            "recall_id": "RECALL-8001",
            "reason": "供应户自查：该批叉烧腌制温控异常",
            "started_at": "2026-09-24T10:00:00+08:00",
            "scope_lot_ids": ["LOT-CS-0918"],
        }, "2026-09-24T10:00:00+08:00", summary="对异常叉烧批次发起召回")
        emit(store, "INGREDIENT_LOT_QUARANTINED", "LOT-CS-0918", {
            "reason": "RECALL-8001 腌制温控异常",
            "at": "2026-09-24T10:05:00+08:00",
        }, "2026-09-24T10:05:00+08:00", summary="隔离剩余异常叉烧")

    return store
