"""领域事件校验：信封字段、聚合匹配、各事件类型载荷、日期格式与幂等键。

只依赖标准库，与 contracts/domain.schema.json 的约定保持一致；
两者由 tests/test_contract.py 交叉核对。
"""
from __future__ import annotations

import re
from datetime import date, datetime

ENVELOPE_REQUIRED = (
    "event_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "occurred_at",
    "version",
    "summary",
)

EVENT_TYPES = {
    "SUPPLIER_REGISTERED",
    "WORKSHOP_QUALIFIED",
    "INGREDIENT_LOT_RECEIVED",
    "INGREDIENT_LOT_INSPECTED",
    "INGREDIENT_LOT_QUARANTINED",
    "RECIPE_VERSION_PUBLISHED",
    "PRODUCTION_SHIFT_OPENED",
    "PRODUCTION_SHIFT_CLOSED",
    "BATCH_PRODUCED",
    "BATCH_ALLERGEN_LABEL_FIXED",
    "BATCH_RELEASED",
    "BATCH_BLOCKED",
    "ORDER_RESERVED",
    "ORDER_CANCELLED",
    "ORDER_RUSH_ACCEPTED",
    "PLAN_REARRANGED",
    "SHIPMENT_DISPATCHED",
    "SHIPMENT_RETURNED",
    "STOCK_RECHANNELLED",
    "WORK_ATTENDANCE_RECORDED",
    "WORK_HOURS_SUPPLEMENTED",
    "WEIGH_IN_RECORDED",
    "SETTLEMENT_PAID",
    "RECALL_STARTED",
    "RECALL_CLOSED",
    "RISK_FLAG_RAISED",
}

AGGREGATE_TYPES = {
    "supplier",
    "workshop",
    "ingredient_lot",
    "recipe",
    "production_shift",
    "production_batch",
    "sales_order",
    "production_plan",
    "shipment",
    "worker_settlement",
}

ALLERGENS = {"花生", "坚果", "蛋", "奶", "小麦麸质", "大豆", "芝麻"}

# event_type -> (应有聚合, 载荷必填字段, 字段类型/格式描述)
# 格式标记：date / datetime / enum:... / 正整数 / 正数 / 非负数 / 过敏原列表
_PAYLOAD_RULES: dict[str, tuple[str, tuple[str, ...], dict[str, str]]] = {
    "SUPPLIER_REGISTERED": (
        "supplier",
        ("name", "village", "supplies"),
        {"name": "str", "village": "str", "supplies": "str_list",
         "bank_account_last4": "last4"},
    ),
    "WORKSHOP_QUALIFIED": (
        "workshop",
        ("name", "qualification_no", "valid_until", "daily_capacity_kg"),
        {"name": "str", "qualification_no": "str", "valid_until": "date",
         "daily_capacity_kg": "正数"},
    ),
    "INGREDIENT_LOT_RECEIVED": (
        "ingredient_lot",
        ("ingredient", "supplier_id", "quantity_kg", "received_on", "shelf_life_days"),
        {"ingredient": "str", "supplier_id": "str", "quantity_kg": "正数",
         "received_on": "date", "shelf_life_days": "正整数", "unit_price": "非负数"},
    ),
    "INGREDIENT_LOT_INSPECTED": (
        "ingredient_lot",
        ("result", "inspected_at"),
        {"result": "enum:合格,不合格,让步接收", "inspected_at": "datetime", "note": "str"},
    ),
    "INGREDIENT_LOT_QUARANTINED": (
        "ingredient_lot",
        ("reason", "at"),
        {"reason": "str", "at": "datetime"},
    ),
    "RECIPE_VERSION_PUBLISHED": (
        "recipe",
        ("recipe_name", "recipe_version", "workshop_id", "ingredients",
         "declared_allergens", "approved", "published_at"),
        {"recipe_name": "str", "recipe_version": "正整数", "workshop_id": "str",
         "ingredients": "checked_separately",
         "declared_allergens": "checked_separately",
         "approved": "bool", "published_at": "datetime"},
    ),
    "PRODUCTION_SHIFT_OPENED": (
        "production_shift",
        ("workshop_id", "shift_date", "master_id", "planned_kg"),
        {"workshop_id": "str", "shift_date": "date", "master_id": "str",
         "planned_kg": "非负数"},
    ),
    "PRODUCTION_SHIFT_CLOSED": (
        "production_shift",
        ("closed_at",),
        {"closed_at": "datetime"},
    ),
    "BATCH_PRODUCED": (
        "production_batch",
        ("workshop_id", "shift_id", "recipe_id", "recipe_version", "quantity_kg",
         "produced_at", "best_before", "ingredient_lots"),
        {"workshop_id": "str", "shift_id": "str", "recipe_id": "str",
         "recipe_version": "正整数", "quantity_kg": "正数",
         "produced_at": "datetime", "best_before": "date",
         "ingredient_lots": "checked_separately"},
    ),
    "BATCH_ALLERGEN_LABEL_FIXED": (
        "production_batch",
        ("label_allergens", "fixed_at", "recipe_version_snapshot"),
        {"fixed_at": "datetime", "recipe_version_snapshot": "正整数",
         "label_allergens": "checked_separately"},
    ),
    "BATCH_RELEASED": (
        "production_batch",
        ("released_at",),
        {"released_at": "datetime", "qr_public_token": "str"},
    ),
    "BATCH_BLOCKED": (
        "production_batch",
        ("reason", "at"),
        {"reason": "str", "at": "datetime", "recall_id": "str"},
    ),
    "ORDER_RESERVED": (
        "sales_order",
        ("channel", "quantity_kg", "needed_by", "reserved_batch_id", "reserved_at"),
        {"channel": "enum:电商预售,线下团购,门店,临期渠道", "quantity_kg": "正数",
         "needed_by": "date", "reserved_batch_id": "str",
         "reserved_at": "datetime", "plan_id": "str"},
    ),
    "ORDER_CANCELLED": (
        "sales_order",
        ("reason", "cancelled_at", "plan_id"),
        {"reason": "enum:预售取消,客户退单,质量冻结", "cancelled_at": "datetime",
         "plan_id": "str"},
    ),
    "ORDER_RUSH_ACCEPTED": (
        "sales_order",
        ("quantity_kg", "needed_by", "accepted_at", "plan_id"),
        {"quantity_kg": "正数", "needed_by": "date",
         "accepted_at": "datetime", "plan_id": "str"},
    ),
    "PLAN_REARRANGED": (
        "production_plan",
        ("plan_id", "trigger", "rearranged_at", "assignments", "unmet_orders"),
        {"plan_id": "str", "trigger": "enum:预售取消,节前加单,临期转渠道",
         "rearranged_at": "datetime",
         "assignments": "checked_separately",
         "unmet_orders": "checked_separately"},
    ),
    "SHIPMENT_DISPATCHED": (
        "shipment",
        ("order_id", "batch_id", "quantity_kg", "channel", "dispatched_at"),
        {"order_id": "str", "batch_id": "str", "quantity_kg": "正数",
         "channel": "str", "dispatched_at": "datetime"},
    ),
    "SHIPMENT_RETURNED": (
        "shipment",
        ("order_id", "batch_id", "quantity_kg", "returned_at", "reason"),
        {"order_id": "str", "batch_id": "str", "quantity_kg": "正数",
         "returned_at": "datetime", "reason": "str"},
    ),
    "STOCK_RECHANNELLED": (
        "production_batch",
        ("batch_id", "quantity_kg", "from_channel", "to_channel", "at"),
        {"batch_id": "str", "quantity_kg": "正数", "from_channel": "str",
         "to_channel": "enum:临期渠道", "at": "datetime"},
    ),
    "WORK_ATTENDANCE_RECORDED": (
        "worker_settlement",
        ("worker_id", "shift_id", "hours", "hourly_rate", "recorded_at"),
        {"worker_id": "str", "shift_id": "str", "hours": "工时",
         "hourly_rate": "非负数", "recorded_at": "datetime"},
    ),
    "WORK_HOURS_SUPPLEMENTED": (
        "worker_settlement",
        ("worker_id", "shift_id", "hours", "hourly_rate", "supplement_no",
         "supplemented_at"),
        {"worker_id": "str", "shift_id": "str", "hours": "工时",
         "hourly_rate": "非负数", "supplement_no": "str",
         "supplemented_at": "datetime"},
    ),
    "WEIGH_IN_RECORDED": (
        "worker_settlement",
        ("weigh_no", "supplier_id", "lot_id", "quantity_kg", "unit_price", "weighed_at"),
        {"weigh_no": "str", "supplier_id": "str", "lot_id": "str",
         "quantity_kg": "正数", "unit_price": "非负数", "weighed_at": "datetime"},
    ),
    "SETTLEMENT_PAID": (
        "worker_settlement",
        ("payee_id", "amount", "source_refs", "paid_at"),
        {"payee_id": "str", "amount": "非负数", "paid_at": "datetime",
         "source_refs": "checked_separately"},
    ),
    "RECALL_STARTED": (
        "ingredient_lot",
        ("recall_id", "reason", "started_at", "scope_lot_ids"),
        {"recall_id": "str", "reason": "str", "started_at": "datetime",
         "scope_lot_ids": "checked_separately"},
    ),
    "RECALL_CLOSED": (
        "ingredient_lot",
        ("recall_id", "closed_at"),
        {"recall_id": "str", "closed_at": "datetime"},
    ),
    "RISK_FLAG_RAISED": (
        "production_batch",
        ("kind", "at", "detail"),
        {"kind": "enum:缺料,延期交付,保质期,资质到期",
         "at": "datetime", "detail": "str"},
    ),
}

# 必须携带业务幂等键的事件：线下录入与付款
_IDEMPOTENCY_REQUIRED = {
    "WEIGH_IN_RECORDED",
    "WORK_HOURS_SUPPLEMENTED",
    "SETTLEMENT_PAID",
}


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _check_field(name: str, value: object, rule: str) -> str | None:
    if rule == "str":
        if not isinstance(value, str) or not value:
            return f"payload.{name} 必须是非空字符串"
    elif rule == "bool":
        if not isinstance(value, bool):
            return f"payload.{name} 必须是布尔值"
    elif rule == "date":
        if not _is_date(value):
            return f"payload.{name} 必须是 YYYY-MM-DD 日期"
    elif rule == "datetime":
        if not _is_datetime(value):
            return f"payload.{name} 必须是带时区的日期时间"
    elif rule == "正整数":
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return f"payload.{name} 必须是正整数"
    elif rule == "正数":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            return f"payload.{name} 必须是正数"
    elif rule == "非负数":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return f"payload.{name} 必须是非负数"
    elif rule == "工时":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 24:
            return f"payload.{name} 必须在 (0, 24] 小时内"
    elif rule == "checked_separately":
        return None
    elif rule == "str_list":
        if not isinstance(value, list) or not value or not all(
            isinstance(x, str) and x for x in value
        ):
            return f"payload.{name} 必须是非空字符串数组"
    elif rule == "last4":
        if not (isinstance(value, str) and re.fullmatch(r"[0-9]{4}", value)):
            return f"payload.{name} 必须是 4 位数字"
    elif rule.startswith("enum:"):
        allowed = set(rule[5:].split(","))
        if value not in allowed:
            return f"payload.{name} 取值必须在 {sorted(allowed)} 内"
    return None


def validate_event(record: dict) -> list[str]:
    """返回错误信息列表；空列表表示通过。"""
    errors = [f"缺少字段：{name}" for name in ENVELOPE_REQUIRED if name not in record]
    if errors:
        return errors

    if not isinstance(record["event_id"], str) or not record["event_id"]:
        errors.append("event_id 必须是非空字符串")
    if record["event_type"] not in EVENT_TYPES:
        errors.append(f"未知 event_type：{record['event_type']}")
    if record["aggregate_type"] not in AGGREGATE_TYPES:
        errors.append(f"未知 aggregate_type：{record['aggregate_type']}")
    if not isinstance(record["aggregate_id"], str) or not record["aggregate_id"]:
        errors.append("aggregate_id 必须是非空字符串")
    if not _is_datetime(record["occurred_at"]):
        errors.append("occurred_at 必须是日期时间字符串")
    if not isinstance(record["version"], int) or isinstance(record["version"], bool) or record["version"] < 1:
        errors.append("version 必须是正整数")
    if not isinstance(record["summary"], str) or not record["summary"]:
        errors.append("summary 必须是非空字符串")
    if "idempotency_key" in record and not isinstance(record["idempotency_key"], str):
        errors.append("idempotency_key 必须是非空字符串")

    event_type = record["event_type"]
    if event_type not in _PAYLOAD_RULES:
        return errors

    aggregate, required_fields, field_rules = _PAYLOAD_RULES[event_type]
    if record["aggregate_type"] != aggregate:
        errors.append(f"{event_type} 的 aggregate_type 必须是 {aggregate}")

    payload = record.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload 必须是对象")
        return errors

    for name in required_fields:
        if name not in payload:
            errors.append(f"payload 缺少字段：{name}")

    for name, value in payload.items():
        rule = field_rules.get(name)
        if rule is None:
            errors.append(f"payload.{name} 不是 {event_type} 允许的字段")
            continue
        msg = _check_field(name, value, rule)
        if msg:
            errors.append(msg)

    if event_type == "RECIPE_VERSION_PUBLISHED":
        errors.extend(_validate_recipe(payload))
    elif event_type == "BATCH_PRODUCED":
        errors.extend(_validate_batch_produced(payload))
    elif event_type == "BATCH_ALLERGEN_LABEL_FIXED":
        labels = payload.get("label_allergens")
        if isinstance(labels, list) and not set(labels) <= ALLERGENS:
            errors.append("label_allergens 只能取受控过敏原词表")
    elif event_type == "PLAN_REARRANGED":
        errors.extend(_validate_plan(payload))
    elif event_type == "SETTLEMENT_PAID":
        refs = payload.get("source_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r for r in refs):
            errors.append("payload.source_refs 必须是非空字符串数组")
        elif len(refs) != len(set(refs)):
            errors.append("payload.source_refs 不可重复")
    elif event_type == "RECALL_STARTED":
        lots = payload.get("scope_lot_ids")
        if not isinstance(lots, list) or not lots or not all(isinstance(x, str) and x for x in lots):
            errors.append("payload.scope_lot_ids 必须是非空字符串数组")

    if event_type in _IDEMPOTENCY_REQUIRED and not record.get("idempotency_key"):
        errors.append(f"{event_type} 必须携带 idempotency_key（线下单号/付款单号）")

    return errors


def _validate_recipe(payload: dict) -> list[str]:
    errors: list[str] = []
    ingredients = payload.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        return ["payload.ingredients 必须是非空数组"]
    for i, item in enumerate(ingredients):
        if not isinstance(item, dict):
            errors.append(f"ingredients[{i}] 必须是对象")
            continue
        if not isinstance(item.get("ingredient"), str) or not item["ingredient"]:
            errors.append(f"ingredients[{i}].ingredient 必须非空")
        qty = item.get("quantity_kg")
        if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
            errors.append(f"ingredients[{i}].quantity_kg 必须是正数")
        if set(item) - {"ingredient", "quantity_kg", "lot_id"}:
            errors.append(f"ingredients[{i}] 含未声明字段")
    declared = payload.get("declared_allergens")
    if not isinstance(declared, list) or not set(declared) <= ALLERGENS:
        errors.append("declared_allergens 只能取受控过敏原词表")
    return errors


def _validate_batch_produced(payload: dict) -> list[str]:
    errors: list[str] = []
    lots = payload.get("ingredient_lots")
    if not isinstance(lots, list) or not lots:
        return ["payload.ingredient_lots 必须是非空数组（成品必须能反查原料批次）"]
    for i, item in enumerate(lots):
        if not isinstance(item, dict):
            errors.append(f"ingredient_lots[{i}] 必须是对象")
            continue
        for key in ("lot_id", "ingredient"):
            if not isinstance(item.get(key), str) or not item[key]:
                errors.append(f"ingredient_lots[{i}].{key} 必须非空")
        qty = item.get("quantity_kg")
        if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
            errors.append(f"ingredient_lots[{i}].quantity_kg 必须是正数")
        if set(item) - {"lot_id", "ingredient", "quantity_kg"}:
            errors.append(f"ingredient_lots[{i}] 含未声明字段")
    return errors


def _validate_plan(payload: dict) -> list[str]:
    errors: list[str] = []
    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        return ["payload.assignments 必须是数组"]
    for i, item in enumerate(assignments):
        if not isinstance(item, dict):
            errors.append(f"assignments[{i}] 必须是对象")
            continue
        for key in ("order_id", "batch_id"):
            if not isinstance(item.get(key), str) or not item[key]:
                errors.append(f"assignments[{i}].{key} 必须非空")
        qty = item.get("quantity_kg")
        if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
            errors.append(f"assignments[{i}].quantity_kg 必须是正数")
        if set(item) - {"order_id", "batch_id", "quantity_kg"}:
            errors.append(f"assignments[{i}] 含未声明字段")
    unmet = payload.get("unmet_orders")
    if not isinstance(unmet, list) or not all(isinstance(x, str) for x in unmet):
        errors.append("payload.unmet_orders 必须是字符串数组")
    return errors
