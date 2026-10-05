"""双向追溯与精确冻结。

正向（原料 → 下游）：异常原料批次 → 用过该批的成品 → 占用/发运订单；
反向（退货 → 上游）：消费者退货的成品 → 班次/师傅、配方版本、全部投料批次与供应户。

冻结范围由谱系图机械推导，只冻结真正受影响的批次与订单，避免手工扩大。
"""
from __future__ import annotations

from .readmodel import World


def batch_to_lots(world: World, batch_id: str) -> list[dict]:
    """反向：成品 → 实际投料批次（含数量、原料、检验状态、供应户）。"""
    batch = world.batches.get(batch_id)
    if batch is None:
        return []
    result = []
    for used in batch["ingredient_lots"]:
        lot = world.lots.get(used["lot_id"], {})
        result.append({
            "lot_id": used["lot_id"],
            "ingredient": used["ingredient"],
            "quantity_kg": used["quantity_kg"],
            "supplier_id": lot.get("supplier_id"),
            "received_on": lot.get("received_on"),
            "inspection": lot.get("inspection"),
            "quarantined": lot.get("quarantined", False),
        })
    return result


def return_provenance(world: World, shipment_id: str) -> dict:
    """一张消费者退货单反查成品、班次、师傅、配方版本、原料与供应户。"""
    shipment = next((s for s in world.shipments if s["shipment_id"] == shipment_id), None)
    if shipment is None:
        return {"found": False}
    batch_id = shipment["batch_id"]
    batch = world.batches.get(batch_id, {})
    shift = world.shifts.get(batch.get("shift_id", ""), {})
    workshop = world.workshops.get(batch.get("workshop_id", ""), {})
    return {
        "found": True,
        "shipment_id": shipment_id,
        "order_id": shipment["order_id"],
        "returned_qty_kg": shipment["returned_qty_kg"],
        "return_reason": shipment.get("return_reason"),
        "batch": {
            "batch_id": batch_id,
            "produced_at": batch.get("produced_at"),
            "best_before": batch.get("best_before"),
            "label_allergens": batch.get("label_allergens"),
        },
        "shift": {
            "shift_id": batch.get("shift_id"),
            "shift_date": shift.get("shift_date"),
            "master_id": shift.get("master_id"),
            "workshop_id": batch.get("workshop_id"),
            "workshop_name": workshop.get("name"),
            "recipe_id": batch.get("recipe_id"),
            "recipe_version": batch.get("recipe_version"),
        },
        "ingredient_lots": batch_to_lots(world, batch_id),
    }


def lots_to_batches(world: World, lot_ids: list[str]) -> dict[str, list[str]]:
    """正向：原料批次 → 使用过该批原料的全部成品。"""
    wanted = set(lot_ids)
    impact: dict[str, list[str]] = {lot_id: [] for lot_id in lot_ids}
    for batch_id, batch in world.batches.items():
        used_lots = {u["lot_id"] for u in batch["ingredient_lots"]}
        for lot_id in wanted & used_lots:
            impact[lot_id].append(batch_id)
    return impact


def affected_orders(world: World, lot_ids: list[str]) -> dict:
    """从异常原料找全受影响订单（预留占用 + 已发运），不涉及其他订单。"""
    impacted_batches = set()
    per_lot = lots_to_batches(world, lot_ids)
    for batches in per_lot.values():
        impacted_batches.update(batches)

    reserved, dispatched = [], []
    for order_id, order in world.orders.items():
        if order["status"] == "cancelled":
            continue
        if order["batch_id"] in impacted_batches:
            reserved.append(order_id)
    for shipment in world.shipments:
        if shipment["batch_id"] in impacted_batches:
            dispatched.append({
                "shipment_id": shipment["shipment_id"],
                "order_id": shipment["order_id"],
                "quantity_kg": shipment["quantity_kg"],
                "returned": shipment["returned"],
                "returned_qty_kg": shipment["returned_qty_kg"],
            })
    return {
        "lot_ids": list(lot_ids),
        "batch_ids": sorted(impacted_batches),
        "reserved_order_ids": sorted(reserved),
        "dispatched": dispatched,
    }


def freeze_scope(world: World, recall_id: str) -> dict:
    """对已发起召回，给出精确冻结范围（同批原料只覆盖真实下游）。"""
    recall = world.recalls.get(recall_id)
    if recall is None:
        raise KeyError(f"召回不存在：{recall_id}")
    scope = affected_orders(world, recall["scope_lot_ids"])
    # 已被该召回标记的批次视为应冻结；若尚未写 BATCH_BLOCKED，给出待冻建议
    already = {
        bid for bid, b in world.batches.items()
        if b.get("recall_id") == recall_id
    }
    scope["recall_id"] = recall_id
    scope["to_block_batch_ids"] = sorted(set(scope["batch_ids"]) - already)
    return scope
