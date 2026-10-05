"""扫码公开投影：只公开产地与制作信息，绝不含村民收入与结算字段。

公开视图是白名单投影，而不是对内部记录做删除——新增敏感字段时默认不外露。
"""
from __future__ import annotations

from .readmodel import World

# 成品公开视图允许出现的键（白名单）
_PUBLIC_BATCH_KEYS = (
    "batch_id", "product", "produced_at", "best_before",
    "workshop_name", "qualification_no", "shift_date", "master_title",
    "recipe_name", "recipe_version", "label_allergens", "ingredients",
)

# 任何情况下都不得进入公开投影的字段名片段
_FORBIDDEN_HINTS = ("price", "rate", "amount", "income", "account", "pay", "supplier")


def public_batch_view(world: World, batch_id: str) -> dict:
    batch = world.batches.get(batch_id)
    if batch is None:
        return {"found": False}
    if not batch["released"]:
        return {"found": False, "reason": "成品尚未放行，暂不公开"}
    if batch["blocked"]:
        return {"found": False, "reason": "成品已冻结，暂停公开"}

    workshop = world.workshops.get(batch["workshop_id"], {})
    shift = world.shifts.get(batch["shift_id"], {})
    recipe = world.recipe_payload(batch["recipe_id"], batch["recipe_version"]) or {}

    # 产地公开到“原料品类 + 进货日期 + 村片区（若供应户资料中有）”，
    # 不公开供应户姓名、单价、银行信息。供应户身份只保留在内部追溯侧。
    ingredients_view = []
    for used in batch["ingredient_lots"]:
        lot = world.lots.get(used["lot_id"], {})
        supplier = world.suppliers.get(lot.get("supplier_id", ""), {})
        ingredients_view.append({
            "ingredient": used["ingredient"],
            "village": supplier.get("village"),
            "received_on": lot.get("received_on"),
            "inspection": lot.get("inspection"),
        })

    view = {
        "found": True,
        "batch_id": batch_id,
        "product": recipe.get("recipe_name", "叉烧五仁月饼"),
        "produced_at": batch["produced_at"],
        "best_before": batch["best_before"],
        "workshop_name": workshop.get("name"),
        "qualification_no": workshop.get("qualification_no"),
        "shift_date": shift.get("shift_date"),
        # 师傅只公开岗位称谓，不公开身份/报酬
        "master_title": "当班师傅" if shift.get("master_id") else None,
        "recipe_name": recipe.get("recipe_name"),
        "recipe_version": batch["recipe_version"],
        "label_allergens": sorted(batch["label_allergens"] or []),
        "ingredients": ingredients_view,
    }
    _assert_clean(view)
    return view


def _assert_clean(view: dict) -> None:
    def walk(obj: object, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                low = k.lower()
                if any(hint in low for hint in _FORBIDDEN_HINTS):
                    raise AssertionError(f"公开投影混入敏感字段：{path}{k}")
                walk(v, f"{path}{k}.")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}].")
    walk(view)
