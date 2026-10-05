"""结算：线下称重与工时补录只能付款一次。

可付款来源及其引用号：
- 线下称重 WEIGH_IN_RECORDED      → weigh_no（供应户货款 = 数量 × 单价）
- 正常记工 WORK_ATTENDANCE_RECORDED → att:{worker_id}:{shift_id}（工资 = 工时 × 时薪）
- 工时补录 WORK_HOURS_SUPPLEMENTED  → supplement_no（工资 = 工时 × 时薪）

SETTLEMENT_PAID 的 source_refs 把来源号钉在付款事件上；
任何来源号第二次出现在付款中都会被拒绝（配合账本的 idempotency_key 双保险）。
"""
from __future__ import annotations

from datetime import datetime

from .readmodel import World


class SettlementError(ValueError):
    pass


def attendance_ref(worker_id: str, shift_id: str) -> str:
    return f"att:{worker_id}:{shift_id}"


def _all_source_refs(world: World) -> dict[str, dict]:
    refs: dict[str, dict] = {}
    seen_att: dict[str, int] = {}
    for w in world.weigh_ins:
        refs[w["weigh_no"]] = {
            "payee_id": w["supplier_id"],
            "amount": round(w["quantity_kg"] * w["unit_price"], 2),
            "kind": "weigh_in",
        }
    for a in world.attendance:
        ref = attendance_ref(a["worker_id"], a["shift_id"])
        seen_att[ref] = seen_att.get(ref, 0) + 1
        refs[ref] = {
            "payee_id": a["worker_id"],
            "amount": round(a["hours"] * a["hourly_rate"], 2),
            "kind": "attendance",
        }
    for s in world.supplements:
        refs[s["supplement_no"]] = {
            "payee_id": s["worker_id"],
            "amount": round(s["hours"] * s["hourly_rate"], 2),
            "kind": "supplement",
        }
    duplicates = [ref for ref, n in seen_att.items() if n > 1]
    if duplicates:
        raise SettlementError(f"同一工人班次存在重复记工：{sorted(duplicates)}")
    return refs


def paid_source_refs(world: World) -> set[str]:
    paid: set[str] = set()
    for payment in world.payments.values():
        paid.update(payment["source_refs"])
    return paid


def unpaid_for(world: World, payee_id: str) -> dict[str, float]:
    """某供应户/工人尚未付款的来源及金额（供对账）。"""
    paid = paid_source_refs(world)
    refs = _all_source_refs(world)
    return {
        ref: info["amount"]
        for ref, info in refs.items()
        if info["payee_id"] == payee_id and ref not in paid
    }


def build_payment(
    world: World,
    *,
    payment_id: str,
    payee_id: str,
    source_refs: list[str],
    paid_at: datetime,
) -> dict:
    """构造 SETTLEMENT_PAID 草案；金额由来源自动汇总，调用方不允许手填。"""
    if not source_refs:
        raise SettlementError("付款必须至少结清一个来源")
    if len(source_refs) != len(set(source_refs)):
        raise SettlementError("同一次付款内来源不可重复")

    refs = _all_source_refs(world)
    already = paid_source_refs(world)
    amount = 0.0
    for ref in source_refs:
        if ref not in refs:
            raise SettlementError(f"来源不存在：{ref}")
        info = refs[ref]
        if info["payee_id"] != payee_id:
            raise SettlementError(f"来源 {ref} 不属于收款人 {payee_id}")
        if ref in already:
            raise SettlementError(f"来源已付款，不得重复支付：{ref}")
        amount += info["amount"]

    return {
        "event_id": f"{payment_id}-evt-1",
        "idempotency_key": payment_id,
        "event_type": "SETTLEMENT_PAID",
        "aggregate_type": "worker_settlement",
        "aggregate_id": payment_id,
        "occurred_at": paid_at.isoformat(),
        "version": 1,
        "summary": f"向 {payee_id} 结清 {len(source_refs)} 条称重/工时来源",
        "payload": {
            "payee_id": payee_id,
            "amount": round(amount, 2),
            "source_refs": list(source_refs),
            "paid_at": paid_at.isoformat(),
        },
    }
