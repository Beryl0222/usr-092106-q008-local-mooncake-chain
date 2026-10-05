"""双向追溯：消费者退货反查与异常原料影响面。"""

from __future__ import annotations

from .ledger import Ledger


def trace_order(ledger: Ledger, order_id: str) -> dict:
    """从退货/订单反查成品批次、班次与原料批次。"""
    order = ledger.orders[order_id]
    batch_ids = set(order.allocations) | set(order.shipped_per_batch)
    batches = {}
    for batch_id in sorted(batch_ids):
        batch = ledger.batches[batch_id]
        shift = ledger.shifts[batch.shift_id]
        lots = []
        for lot_id, quantity in sorted(batch.lot_usages.items()):
            lot = ledger.lots[lot_id]
            supplier = ledger.suppliers[lot.supplier_id]
            lots.append(
                {
                    "lot_id": lot_id,
                    "ingredient": lot.ingredient,
                    "quantity": quantity,
                    "supplier_id": supplier.supplier_id,
                    "region": supplier.region,
                }
            )
        batches[batch_id] = {
            "workshop_id": batch.workshop_id,
            "shift_id": shift.shift_id,
            "shift_date": shift.date,
            "produced_at": batch.produced_at.isoformat(),
            "lots": lots,
        }
    return {
        "order_id": order_id,
        "channel": order.channel,
        "batches": batches,
        "shipments": [s for s in ledger.shipments if s["order_id"] == order_id],
        "returns": list(order.returns),
    }


def trace_lot(ledger: Ledger, lot_id: str) -> dict:
    """从原料批次正查投料批次与受影响订单。"""
    lot = ledger.lots[lot_id]
    batch_ids = {b.batch_id for b in ledger.batches.values() if lot_id in b.lot_usages}
    return {
        "lot_id": lot_id,
        "ingredient": lot.ingredient,
        "supplier_id": lot.supplier_id,
        "quarantined": lot.quarantined,
        "batches": sorted(batch_ids),
        "orders": sorted(_orders_touching(ledger, batch_ids)),
    }


def recall_scope(ledger: Ledger, aggregate_type: str, aggregate_id: str) -> dict:
    """精确冻结范围：只含确实受影响的批次、订单与发货，不扩大。"""
    if aggregate_type == "ingredient_lot":
        lot_ids = {aggregate_id}
        batch_ids = {
            b.batch_id for b in ledger.batches.values() if aggregate_id in b.lot_usages
        }
    elif aggregate_type == "production_batch":
        batch_ids = {aggregate_id}
        lot_ids = set(ledger.batches[aggregate_id].lot_usages)
    else:
        raise ValueError(f"不支持的召回对象：{aggregate_type}")
    return {
        "lots": sorted(lot_ids),
        "batches": sorted(batch_ids),
        "orders": sorted(_orders_touching(ledger, batch_ids)),
        "shipments": [
            s["shipment_id"] for s in ledger.shipments if s["batch_id"] in batch_ids
        ],
    }


def _orders_touching(ledger: Ledger, batch_ids) -> set:
    return {
        order.order_id
        for order in ledger.orders.values()
        if batch_ids & (set(order.allocations) | set(order.shipped_per_batch))
    }
