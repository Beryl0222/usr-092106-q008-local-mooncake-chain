"""原料/配方/标签的质量门槛。

成品放行前必须同时满足：
1. 工坊资质在生产日期当天有效；
2. 班次存在且属于该工坊；
3. 配方版本已发布且经批准；
4. 每个投料批次检验合格（或让步接收）、未隔离、未过保质期、库存足够；
5. 标签过敏原覆盖实际投料引入的全部过敏原，且快照配方版本与生产一致。
"""
from __future__ import annotations

from datetime import datetime

from .readmodel import World

# 原料 → 必然引入的过敏原（受控词表与契约一致）
INGREDIENT_ALLERGENS: dict[str, set[str]] = {
    "花生仁": {"花生"},
    "芝麻": {"芝麻"},
    "芝麻仁": {"芝麻"},
    "杏仁": {"坚果"},
    "腰果": {"坚果"},
    "核桃仁": {"坚果"},
    "面粉": {"小麦麸质"},
    "鸡蛋": {"蛋"},
    "蛋液": {"蛋"},
    "牛奶": {"奶"},
    "奶粉": {"奶"},
    "奶油": {"奶"},
    "叉烧": {"大豆", "小麦麸质"},  # 叉烧腌制普遍含酱油/麸质
    "酱油": {"大豆", "小麦麸质"},
}


def allergens_introduced(ingredient_names: list[str]) -> set[str]:
    introduced: set[str] = set()
    for name in ingredient_names:
        introduced |= INGREDIENT_ALLERGENS.get(name, set())
    return introduced


def check_production(world: World, draft: dict, *, at: datetime | None = None) -> list[str]:
    """draft 为待入账的 BATCH_PRODUCED 载荷。返回问题清单，空列表即可生产。"""
    errors: list[str] = []
    moment = at or datetime.fromisoformat(draft["produced_at"])

    workshop = world.workshops.get(draft["workshop_id"])
    if workshop is None:
        errors.append(f"工坊未登记：{draft['workshop_id']}")
    elif not world.workshop_qualified_on(draft["workshop_id"], moment.date()):
        errors.append(f"工坊资质在 {moment.date()} 已失效：{draft['workshop_id']}")

    shift = world.shifts.get(draft["shift_id"])
    if shift is None:
        errors.append(f"班次不存在：{draft['shift_id']}")
    elif shift["workshop_id"] != draft["workshop_id"]:
        errors.append("班次不属于该工坊")

    recipe = world.recipe_payload(draft["recipe_id"], draft["recipe_version"])
    if recipe is None:
        errors.append(
            f"配方版本不存在：{draft['recipe_id']}@v{draft['recipe_version']}"
        )
    elif not recipe["approved"]:
        errors.append("配方版本未经批准，不得投产")
    elif recipe["workshop_id"] != draft["workshop_id"]:
        errors.append("配方不属于该工坊（地方配方需各自获准）")

    seen: dict[str, float] = {}
    for used in draft["ingredient_lots"]:
        seen[used["lot_id"]] = seen.get(used["lot_id"], 0.0) + used["quantity_kg"]
    for lot_id, need_kg in seen.items():
        lot = world.lots.get(lot_id)
        if lot is None:
            errors.append(f"原料批次不存在：{lot_id}")
            continue
        if lot["quarantined"]:
            errors.append(f"原料批次已隔离：{lot_id}（{lot.get('quarantine_reason', '')}）")
        if lot["inspection"] not in ("合格", "让步接收"):
            errors.append(f"原料批次未合格放行：{lot_id}（检验结果：{lot['inspection']}）")
        if not world.lot_usable(lot_id, moment):
            errors.append(f"原料批次已过保质期：{lot_id}")
        available = world.lot_available_qty(lot_id)
        if need_kg > available + 1e-9:
            errors.append(
                f"原料批次库存不足：{lot_id} 需要 {need_kg}kg，可用 {available}kg"
            )
    return errors


def required_label_allergens(world: World, batch_id: str) -> set[str]:
    """按实际投料推导必须标注的过敏原。"""
    batch = world.batches[batch_id]
    return allergens_introduced([u["ingredient"] for u in batch["ingredient_lots"]])


def check_label(world: World, batch_id: str) -> list[str]:
    """标签核对：少标不允许；标签版本快照必须与实际投产配方版本一致。"""
    batch = world.batches[batch_id]
    errors: list[str] = []
    if batch["label_allergens"] is None:
        errors.append("成品尚未固定过敏原标签")
        return errors
    required = required_label_allergens(world, batch_id)
    labeled = set(batch["label_allergens"])
    missing = required - labeled
    if missing:
        errors.append(f"标签漏标实际过敏原：{sorted(missing)}")
    if batch.get("label_version") != batch["recipe_version"]:
        errors.append("标签快照配方版本与投产配方版本不一致，需重新固定标签")
    return errors


def can_release(world: World, batch_id: str) -> list[str]:
    """成品放行门槛；返回未满足项。"""
    if batch_id not in world.batches:
        return [f"成品不存在：{batch_id}"]
    errors = check_label(world, batch_id)
    batch = world.batches[batch_id]
    if not world.workshop_qualified_on(batch["workshop_id"],
                                       datetime.fromisoformat(batch["produced_at"]).date()):
        errors.append("工坊资质失效，不得放行")
    if batch["blocked"]:
        errors.append("成品已被冻结/召回")
    return errors
