"""从只追加事件流重建的读模型。

所有领域判断都基于 World 快照；快照本身不落库，可随时由事件流重建。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


def _day(value: str) -> date:
    return date.fromisoformat(value)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class World:
    def __init__(self, events: list[dict]) -> None:
        self.suppliers: dict[str, dict] = {}
        self.workshops: dict[str, dict] = {}
        self.lots: dict[str, dict] = {}
        self.recipes: dict[tuple[str, int], dict] = {}
        self.recipe_current: dict[str, int] = {}
        self.shifts: dict[str, dict] = {}
        self.batches: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.shipments: list[dict] = []
        self.attendance: list[dict] = []
        self.supplements: list[dict] = []
        self.weigh_ins: list[dict] = []
        self.payments: dict[str, dict] = {}
        self.recalls: dict[str, dict] = {}
        self.risk_flags: list[dict] = []
        for event in sorted(events, key=lambda e: (e["occurred_at"], e["event_id"])):
            self._apply(event)
        self.events = sorted(events, key=lambda e: (e["occurred_at"], e["event_id"]))

    # ---- 入账 ----------------------------------------------------------------

    def _apply(self, event: dict) -> None:
        et, p, at, agg = event["event_type"], event.get("payload", {}), event["occurred_at"], event["aggregate_id"]
        if et == "SUPPLIER_REGISTERED":
            self.suppliers[agg] = dict(p)
        elif et == "WORKSHOP_QUALIFIED":
            self.workshops[agg] = dict(p)
        elif et == "INGREDIENT_LOT_RECEIVED":
            self.lots[agg] = {
                "lot_id": agg, **p,
                "inspection": None, "quarantined": False,
            }
        elif et == "INGREDIENT_LOT_INSPECTED":
            if agg in self.lots:
                self.lots[agg]["inspection"] = p["result"]
        elif et == "INGREDIENT_LOT_QUARANTINED":
            if agg in self.lots:
                self.lots[agg]["quarantined"] = True
                self.lots[agg]["quarantine_reason"] = p["reason"]
        elif et == "RECIPE_VERSION_PUBLISHED":
            key = (agg, p["recipe_version"])
            self.recipes[key] = {"recipe_id": agg, **p}
            if p["approved"] or agg not in self.recipe_current:
                self.recipe_current[agg] = max(p["recipe_version"], self.recipe_current.get(agg, 0))
        elif et == "PRODUCTION_SHIFT_OPENED":
            self.shifts[agg] = {"shift_id": agg, **p, "closed": False}
        elif et == "PRODUCTION_SHIFT_CLOSED":
            if agg in self.shifts:
                self.shifts[agg]["closed"] = True
        elif et == "BATCH_PRODUCED":
            self.batches[agg] = {
                "batch_id": agg, **p,
                "label_allergens": None, "released": False,
                "blocked": False, "rechannel_qty": 0.0,
            }
        elif et == "BATCH_ALLERGEN_LABEL_FIXED":
            if agg in self.batches:
                self.batches[agg]["label_allergens"] = list(p["label_allergens"])
                self.batches[agg]["label_version"] = p["recipe_version_snapshot"]
        elif et == "BATCH_RELEASED":
            if agg in self.batches:
                self.batches[agg]["released"] = True
                self.batches[agg]["released_at"] = p["released_at"]
                self.batches[agg]["qr_public_token"] = p.get("qr_public_token")
        elif et == "BATCH_BLOCKED":
            if agg in self.batches:
                self.batches[agg]["blocked"] = True
                self.batches[agg]["block_reason"] = p["reason"]
                self.batches[agg]["recall_id"] = p.get("recall_id")
        elif et == "ORDER_RESERVED":
            self.orders[agg] = {
                "order_id": agg, "channel": p["channel"], "quantity_kg": p["quantity_kg"],
                "needed_by": p["needed_by"], "batch_id": p["reserved_batch_id"],
                "status": "reserved", "plan_id": p.get("plan_id"),
                "rush": False,
            }
        elif et == "ORDER_RUSH_ACCEPTED":
            self.orders[agg] = {
                "order_id": agg, "channel": "电商预售", "quantity_kg": p["quantity_kg"],
                "needed_by": p["needed_by"], "batch_id": None,
                "status": "rush", "plan_id": p["plan_id"], "rush": True,
            }
        elif et == "ORDER_CANCELLED":
            if agg in self.orders:
                self.orders[agg]["status"] = "cancelled"
                self.orders[agg]["cancel_reason"] = p["reason"]
        elif et == "PLAN_REARRANGED":
            for a in p["assignments"]:
                if a["order_id"] in self.orders:
                    order = self.orders[a["order_id"]]
                    order["batch_id"] = a["batch_id"]
                    order["quantity_kg"] = a["quantity_kg"]
                    if order["status"] == "rush":
                        order["status"] = "reserved"
                    order["plan_id"] = p["plan_id"]
        elif et == "SHIPMENT_DISPATCHED":
            self.shipments.append({
                "shipment_id": agg, **p,
                "returned_qty_kg": 0.0, "returned": False,
            })
        elif et == "SHIPMENT_RETURNED":
            for s in reversed(self.shipments):
                if s["shipment_id"] == agg:
                    s["returned"] = True
                    s["returned_qty_kg"] = p["quantity_kg"]
                    s["return_reason"] = p["reason"]
                    break
        elif et == "STOCK_RECHANNELLED":
            if agg in self.batches:
                self.batches[agg]["rechannel_qty"] += p["quantity_kg"]
        elif et == "WORK_ATTENDANCE_RECORDED":
            self.attendance.append(dict(p))
        elif et == "WORK_HOURS_SUPPLEMENTED":
            self.supplements.append(dict(p))
        elif et == "WEIGH_IN_RECORDED":
            self.weigh_ins.append(dict(p))
        elif et == "SETTLEMENT_PAID":
            self.payments[agg] = dict(p)
        elif et == "RECALL_STARTED":
            self.recalls[p["recall_id"]] = {
                "recall_id": p["recall_id"], "scope_lot_ids": list(p["scope_lot_ids"]),
                "reason": p["reason"], "status": "open",
            }
        elif et == "RECALL_CLOSED":
            if p["recall_id"] in self.recalls:
                self.recalls[p["recall_id"]]["status"] = "closed"
        elif et == "RISK_FLAG_RAISED":
            self.risk_flags.append({"batch_id": agg, **p})

    # ---- 派生量 --------------------------------------------------------------

    def lot_consumed_qty(self, lot_id: str) -> float:
        total = 0.0
        for b in self.batches.values():
            for used in b["ingredient_lots"]:
                if used["lot_id"] == lot_id:
                    total += used["quantity_kg"]
        return total

    def lot_available_qty(self, lot_id: str) -> float:
        lot = self.lots.get(lot_id)
        if lot is None:
            return 0.0
        return round(lot["quantity_kg"] - self.lot_consumed_qty(lot_id), 3)

    def lot_usable(self, lot_id: str, on: datetime | None = None) -> bool:
        """原料批次能否用于生产：合格/让步接收、未隔离、未过保质期。"""
        lot = self.lots.get(lot_id)
        if lot is None or lot["quarantined"]:
            return False
        if lot["inspection"] not in ("合格", "让步接收"):
            return False
        moment = on or datetime.now().astimezone()
        expiry = _day(lot["received_on"]) + timedelta(days=lot["shelf_life_days"])
        return moment.date() <= expiry

    def active_orders(self) -> list[dict]:
        return [o for o in self.orders.values() if o["status"] != "cancelled"]

    def order_commitments(self, batch_id: str) -> float:
        return round(
            sum(o["quantity_kg"] for o in self.active_orders() if o["batch_id"] == batch_id),
            3,
        )

    def saleable_qty(self, batch_id: str, on: date | None = None) -> float:
        """可售库存：产量 - 临期转出 - 在保订单占用；过期或冻结批次为 0。"""
        b = self.batches.get(batch_id)
        if b is None or b["blocked"]:
            return 0.0
        today = on or datetime.now().astimezone().date()
        if _day(b["best_before"]) < today:
            return 0.0
        free = b["quantity_kg"] - b["rechannel_qty"] - self.order_commitments(batch_id)
        return round(max(free, 0.0), 3)

    def is_near_expiry(self, batch_id: str, on: date, window_days: int) -> bool:
        b = self.batches[batch_id]
        best_before = _day(b["best_before"])
        return on <= best_before <= on + timedelta(days=window_days)

    def recipe_payload(self, recipe_id: str, version: int) -> dict | None:
        return self.recipes.get((recipe_id, version))

    def workshop_qualified_on(self, workshop_id: str, day: date) -> bool:
        ws = self.workshops.get(workshop_id)
        return ws is not None and _day(ws["valid_until"]) >= day

    def shift_loads(self) -> dict[tuple[str, str], float]:
        loads: dict[tuple[str, str], float] = {}
        for s in self.shifts.values():
            key = (s["workshop_id"], s["shift_date"])
            loads[key] = loads.get(key, 0.0) + s["planned_kg"]
        return loads
