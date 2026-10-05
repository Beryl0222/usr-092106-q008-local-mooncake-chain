"""校验领域事件信封与各事件的基础字段。

只负责结构校验：字段是否齐全、事件与聚合类型是否匹配。
业务规则（检验、过敏原、产能、结算幂等等）由 `src/ledger.py` 在入账时把关。
"""

REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)

#: 每种事件允许挂载的聚合类型，以及除信封外必须具备的字段。
EVENT_CONTRACT = {
    "SUPPLIER_REGISTERED": (("supplier",), ("name", "region")),
    "LOT_ACCEPTED": (
        ("ingredient_lot",),
        ("supplier_id", "ingredient", "quantity", "unit", "allergens"),
    ),
    "LOT_INSPECTION_RECORDED": (("ingredient_lot",), ("result", "inspector")),
    "WEIGHING_RECORDED": (("ingredient_lot",), ("weighed_quantity", "source")),
    "RECALL_STARTED": (("ingredient_lot", "production_batch"), ("reason",)),
    "RECIPE_VERSION_PUBLISHED": (
        ("recipe_version",),
        ("workshop_id", "ingredients", "declared_allergens"),
    ),
    "WORKSHOP_CERTIFIED": (("workshop",), ("name", "masters")),
    "SHIFT_RECORDED": (("shift",), ("workshop_id", "master_id", "date", "capacity")),
    "LABOR_RECORDED": (("shift",), ("worker_id", "hours", "source")),
    "BATCH_PRODUCED": (
        ("production_batch",),
        (
            "shift_id",
            "workshop_id",
            "recipe_id",
            "recipe_version",
            "quantity",
            "lot_usages",
            "produced_at",
            "shelf_life_days",
            "label_allergens",
        ),
    ),
    "BATCH_RELEASED": (("production_batch",), ("released_by",)),
    "ORDER_RESERVED": (("sales_order",), ("channel", "quantity", "due_at", "recipe_id")),
    "ORDER_ALLOCATED": (("sales_order",), ("allocations",)),
    "ORDER_REALLOCATED": (("sales_order",), ("allocations", "deallocations", "reason")),
    "ORDER_CANCELLED": (("sales_order",), ("reason",)),
    "RETURN_RECEIVED": (("sales_order",), ("return_id", "reason")),
    "SHIPMENT_DISPATCHED": (("shipment",), ("order_id", "batch_id", "quantity", "channel")),
    "SETTLEMENT_PAID": (
        ("worker_settlement",),
        ("payee_type", "payee_id", "amount", "idempotency_key", "covers"),
    ),
}


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    version = record.get("version")
    if "version" in record and (
        not isinstance(version, int) or isinstance(version, bool) or version < 1
    ):
        errors.append("version 必须是正整数")
    event_type = record.get("event_type")
    if event_type is not None:
        contract = EVENT_CONTRACT.get(event_type)
        if contract is None:
            errors.append(f"未知事件类型：{event_type}")
        else:
            aggregates, fields = contract
            if record.get("aggregate_type") not in aggregates:
                errors.append(
                    f"{event_type} 的 aggregate_type 必须是 {'/'.join(aggregates)}"
                )
            errors.extend(f"缺少字段：{name}" for name in fields if name not in record)
    return errors
