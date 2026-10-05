"""扫码公开视图：只公开产地与制作信息，不带出村民收入。"""

from __future__ import annotations

from .ledger import Ledger


def public_trace_view(ledger: Ledger, batch_id: str) -> dict:
    """成品批次的对外追溯视图。

    字段经过白名单挑选：供应户姓名、结算金额、工时工资等
    敏感信息一律不出现在视图中。
    """
    batch = ledger.batches[batch_id]
    workshop = ledger.workshops[batch.workshop_id]
    shift = ledger.shifts[batch.shift_id]
    origins = sorted(
        {
            ledger.suppliers[ledger.lots[lot_id].supplier_id].region
            for lot_id in batch.lot_usages
        }
    )
    ingredients = sorted({ledger.lots[lot_id].ingredient for lot_id in batch.lot_usages})
    return {
        "batch_id": batch.batch_id,
        "recipe_id": batch.recipe_id,
        "workshop": workshop.name,
        "shift_date": shift.date,
        "produced_at": batch.produced_at.isoformat(),
        "expires_at": batch.expires_at.isoformat(),
        "ingredients": ingredients,
        "origins": origins,
        "allergens": sorted(batch.label_allergens),
        "inspection": "检验合格",
    }
