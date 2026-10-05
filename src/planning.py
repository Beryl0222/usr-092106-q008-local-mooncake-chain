"""缺料与交付风险投影：让工坊在节前提前看见问题。"""

from __future__ import annotations

from datetime import date, datetime

from .ledger import Ledger


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _latest_recipe(ledger: Ledger, recipe_id: str):
    versions = [r for (rid, _), r in ledger.recipes.items() if rid == recipe_id]
    return max(versions, key=lambda r: r.version, default=None)


def material_shortages(ledger: Ledger) -> list:
    """未履约订单折算的原料需求，对照合格可用库存给出缺口。"""
    required: dict[str, float] = {}
    for order in ledger.orders.values():
        if order.cancelled:
            continue
        outstanding = order.quantity - order.shipped - order.allocated
        if outstanding <= 0:
            continue
        recipe = _latest_recipe(ledger, order.recipe_id)
        if recipe is None:
            continue
        for ingredient, per_unit in recipe.ingredients.items():
            required[ingredient] = required.get(ingredient, 0.0) + per_unit * outstanding
    available: dict[str, float] = {}
    for lot in ledger.lots.values():
        if lot.inspection == "passed" and not lot.quarantined:
            available[lot.ingredient] = available.get(lot.ingredient, 0.0) + lot.remaining
    return [
        {
            "ingredient": ingredient,
            "required": quantity,
            "available": available.get(ingredient, 0.0),
            "shortage": quantity - available.get(ingredient, 0.0),
        }
        for ingredient, quantity in sorted(required.items())
        if quantity > available.get(ingredient, 0.0)
    ]


def delivery_risks(ledger: Ledger, as_of: str) -> list:
    """按交期前的剩余产能与批次状态，列出有交付风险的订单。"""
    today = _ts(as_of).date()
    risks = []
    for order in ledger.orders.values():
        if order.cancelled or order.shipped >= order.quantity:
            continue
        for batch_id in sorted(order.allocations):
            if ledger.batches[batch_id].recalled:
                risks.append(
                    {
                        "order_id": order.order_id,
                        "reason": "已分配批次被召回",
                        "batch_id": batch_id,
                    }
                )
        outstanding = order.quantity - order.shipped - order.allocated
        if outstanding <= 0:
            continue
        free_capacity = sum(
            max(0.0, shift.capacity - shift.used)
            for shift in ledger.shifts.values()
            if today <= date.fromisoformat(shift.date) <= order.due_at.date()
        )
        if outstanding > free_capacity:
            risks.append(
                {
                    "order_id": order.order_id,
                    "reason": "交期前产能不足",
                    "outstanding": outstanding,
                    "free_capacity": free_capacity,
                }
            )
    return risks
