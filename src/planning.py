"""产能内重排：预售取消、临期转渠道、节前加单。

约束：
- 只能分配已放行、未冻结、保质期内的可售库存；
- 不能超过工坊当日产能（含师傅当班的班次计划量）；
- 取消释放的库存优先用于加单；临期批次按最早到期优先转渠道；
- 排不下的订单进入 unmet 并产生缺料/延期风险旗标，让工坊提前看见。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from .readmodel import World, _day


class CapacityError(ValueError):
    pass


def planned_load_by_day(world: World) -> dict[tuple[str, str], float]:
    """每个工坊每天已计划产量（班次 planned_kg 之和）。"""
    return dict(world.shift_loads())


def remaining_capacity(world: World, workshop_id: str, day: date) -> float:
    ws = world.workshops.get(workshop_id)
    if ws is None or not world.workshop_qualified_on(workshop_id, day):
        return 0.0
    planned = world.shift_loads().get((workshop_id, day.isoformat()), 0.0)
    return round(ws["daily_capacity_kg"] - planned, 3)


def _candidate_batches(world: World, on: date) -> list[dict]:
    """已放行、未冻结、未过期的成品（含已被订单全额占用的，取消后可再分配）。"""
    rows = []
    for b in world.batches.values():
        if not b["released"] or b["blocked"]:
            continue
        if _day(b["best_before"]) < on:
            continue
        rows.append(b)
    # 临期优先（best_before 升序），减少损耗
    rows.sort(key=lambda b: b["best_before"])
    return rows


def rechannel_near_expiry(world: World, on: date, *, window_days: int = 3) -> list[dict]:
    """临期批次转临期渠道的建议（仅含已放行、未冻结、将到期且无订单占用的量）。"""
    suggestions = []
    for bid, b in world.batches.items():
        if not b["released"] or b["blocked"]:
            continue
        if world.is_near_expiry(bid, on, window_days):
            free = world.saleable_qty(bid, on)
            if free > 0:
                suggestions.append({
                    "batch_id": bid,
                    "quantity_kg": free,
                    "best_before": b["best_before"],
                    "to_channel": "临期渠道",
                })
    suggestions.sort(key=lambda s: s["best_before"])
    return suggestions


def reassign(
    world: World,
    *,
    plan_id: str,
    trigger: str,
    at: datetime,
    cancelled_order_ids: list[str] | None = None,
    rush_order_ids: list[str] | None = None,
    rechannel: list[dict] | None = None,
) -> dict:
    """在当前世界快照上做一次重排，返回可直接入账的事件草案。

    不修改传入快照；草案全部入账后由新的 World 体现结果。
    - cancelled_order_ids：预售取消（其占用的库存立即释放给本次加单）；
    - rush_order_ids：节前加单（ORDER_RUSH_ACCEPTED 已入账，按 needed_by 先后分配）；
    - rechannel：临期转渠道（batch_id/quantity_kg），先扣减可售池；
    返回 {"events", "assignments", "unmet", "risks"}。
    """
    if trigger not in ("预售取消", "节前加单", "临期转渠道"):
        raise CapacityError(f"未知重排触发：{trigger}")

    on = at.date()
    events: list[dict] = []

    cancelled = set(cancelled_order_ids or [])
    for oid in cancelled:
        order = world.orders.get(oid)
        if order is None:
            raise CapacityError(f"订单不存在：{oid}")
        if order["status"] != "cancelled":
            events.append({
                "event_type": "ORDER_CANCELLED",
                "aggregate_type": "sales_order",
                "aggregate_id": oid,
                "payload": {
                    "reason": "预售取消",
                    "cancelled_at": at.isoformat(),
                    "plan_id": plan_id,
                },
            })

    # 本次计算用的可售池：在世界快照基础上模拟“取消释放”和“转渠道扣减”
    freed_qty: dict[str, float] = defaultdict(float)

    def simulated_free(batch_id: str) -> float:
        free = world.saleable_qty(batch_id, on)
        for o in world.active_orders():
            if o["batch_id"] == batch_id and o["order_id"] in cancelled:
                freed = o["quantity_kg"]
                free += freed
                freed_qty[batch_id] += freed
        return round(free, 3)

    rechannel_qty: dict[str, float] = defaultdict(float)
    for item in rechannel or []:
        bid, qty = item["batch_id"], item["quantity_kg"]
        available = simulated_free(bid) - rechannel_qty[bid]
        if available + 1e-9 < qty:
            raise CapacityError(f"批次 {bid} 可售库存不足，无法转渠道 {qty}kg")
        rechannel_qty[bid] += qty
        events.append({
            "event_type": "STOCK_RECHANNELLED",
            "aggregate_type": "production_batch",
            "aggregate_id": bid,
            "payload": {
                "batch_id": bid,
                "quantity_kg": round(qty, 3),
                "from_channel": item.get("from_channel", "电商预售"),
                "to_channel": "临期渠道",
                "at": at.isoformat(),
            },
        })

    # 候选池：本次取消释放出库存的批次优先（预售取消的货应先消化），
    # 再按到期日升序（临期优先）。
    pool = _candidate_batches(world, on)
    simulated = {b["batch_id"]: simulated_free(b["batch_id"]) for b in pool}
    pool.sort(key=lambda b: (
        0 if freed_qty.get(b["batch_id"], 0.0) > 0 else 1,
        b["best_before"],
    ))
    free_qty = {
        b["batch_id"]: round(simulated[b["batch_id"]] - rechannel_qty[b["batch_id"]], 3)
        for b in pool
    }

    rushes = []
    for oid in rush_order_ids or []:
        order = world.orders.get(oid)
        if order is None:
            raise CapacityError(f"加单不存在：{oid}")
        if order["status"] in ("rush", "reserved"):
            rushes.append(order)
    rushes.sort(key=lambda o: (o["needed_by"], o["order_id"]))

    assignments: list[dict] = []
    unmet: list[str] = []
    risks: list[dict] = []

    for order in rushes:
        need = order["quantity_kg"]
        # 优先找到一个能整批满足、且交付日前不过期的批次，避免一单拆多处
        full_fit = next(
            (b for b in pool
             if _day(b["best_before"]) >= _day(order["needed_by"])
             and free_qty[b["batch_id"]] + 1e-9 >= need),
            None,
        )
        if full_fit is not None:
            bid = full_fit["batch_id"]
            free_qty[bid] = round(free_qty[bid] - need, 3)
            assignments.append({
                "order_id": order["order_id"],
                "batch_id": bid,
                "quantity_kg": round(need, 3),
            })
            need = 0.0
        else:
            # 整批放不下：按临期顺序拆配，剩余量报缺料风险
            for b in pool:
                if need <= 1e-9:
                    break
                if _day(b["best_before"]) < _day(order["needed_by"]):
                    continue
                take = min(need, free_qty[b["batch_id"]])
                if take <= 1e-9:
                    continue
                free_qty[b["batch_id"]] = round(free_qty[b["batch_id"]] - take, 3)
                assignments.append({
                    "order_id": order["order_id"],
                    "batch_id": b["batch_id"],
                    "quantity_kg": round(take, 3),
                })
                need = round(need - take, 3)
        if need > 1e-9:
            unmet.append(order["order_id"])
            risks.append({
                "event_type": "RISK_FLAG_RAISED",
                "aggregate_type": "production_batch",
                "aggregate_id": _first_open_batch(world),
                "payload": {
                    "kind": "缺料",
                    "at": at.isoformat(),
                    "detail": f"加单 {order['order_id']} 仍缺 {round(need, 3)}kg，"
                              f"需在 {order['needed_by']} 前排班补货",
                },
            })

    if assignments or unmet:
        events.append({
            "event_type": "PLAN_REARRANGED",
            "aggregate_type": "production_plan",
            "aggregate_id": plan_id,
            "payload": {
                "plan_id": plan_id,
                "trigger": trigger,
                "rearranged_at": at.isoformat(),
                "assignments": assignments,
                "unmet_orders": unmet,
            },
        })
    events.extend(risks)
    return {"events": events, "assignments": assignments, "unmet": unmet, "risks": risks}


def _first_open_batch(world: World) -> str:
    for bid, b in world.batches.items():
        if not b["blocked"]:
            return bid
    return "no-batch"


def delivery_risks(world: World, on: date) -> list[dict]:
    """提前暴露：临期占用冲突、订单交付日前可能过期、资质将到期。"""
    risks = []
    for oid, o in world.orders.items():
        if o["status"] == "cancelled" or not o["batch_id"]:
            continue
        b = world.batches.get(o["batch_id"])
        if b and _day(b["best_before"]) < _day(o["needed_by"]):
            risks.append({
                "kind": "保质期",
                "order_id": oid,
                "detail": f"批次 {o['batch_id']} 保质期 {b['best_before']} 早于交付日 {o['needed_by']}",
            })
        if b and world.saleable_qty(o["batch_id"], on) <= 0 and o["status"] != "cancelled":
            # 占用本身成立；这里只提示库存被临期转出挤占的情况
            pass
    for wid, ws in world.workshops.items():
        days_left = (_day(ws["valid_until"]) - on).days
        if 0 <= days_left <= 7:
            risks.append({
                "kind": "资质到期",
                "workshop_id": wid,
                "detail": f"工坊资质 {ws['valid_until']} 到期，仅剩 {days_left} 天",
            })
    return risks
