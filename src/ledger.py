"""产销协作账：按追加方式接收领域事件，并维护双向追溯所需的投影。

入账时执行的领域规则：

- 事件按聚合单调递增的 version 追加，已入账事件不被原地改写，更正走后继事件；
- 原料批次须检验合格且未被冻结，才能投入生产；
- 成品标签过敏原必须等于实际投料批次过敏原的并集（地方配方可不同，标签必须反映实际）；
- 班次产量不得超过当班师傅产能；
- 订单只能分配已放行、未召回且在交期内不过期的成品批次；
- 线下称重与工时补录等结算按幂等键只允许付款一次。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .validator import validate_event


class DomainError(Exception):
    """事件违反领域规则，被拒绝入账。"""


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass
class Supplier:
    supplier_id: str
    name: str
    region: str


@dataclass
class Lot:
    lot_id: str
    supplier_id: str
    ingredient: str
    quantity: float
    unit: str
    allergens: frozenset
    remaining: float
    inspection: str = "pending"  # pending / passed / failed
    quarantined: bool = False
    weighings: list = field(default_factory=list)


@dataclass
class Recipe:
    recipe_id: str
    version: int
    workshop_id: str
    ingredients: dict
    declared_allergens: frozenset


@dataclass
class Workshop:
    workshop_id: str
    name: str
    masters: dict


@dataclass
class Shift:
    shift_id: str
    workshop_id: str
    master_id: str
    date: str
    capacity: float
    used: float = 0.0
    labor: list = field(default_factory=list)


@dataclass
class Batch:
    batch_id: str
    shift_id: str
    workshop_id: str
    recipe_id: str
    recipe_version: int
    quantity: float
    lot_usages: dict
    produced_at: datetime
    expires_at: datetime
    label_allergens: frozenset
    status: str = "produced"  # produced / released
    recalled: bool = False
    allocated: float = 0.0


@dataclass
class Order:
    order_id: str
    channel: str
    quantity: float
    due_at: datetime
    recipe_id: str
    allocations: dict = field(default_factory=dict)
    shipped_per_batch: dict = field(default_factory=dict)
    shipped: float = 0.0
    cancelled: bool = False
    returns: list = field(default_factory=list)

    @property
    def allocated(self) -> float:
        return sum(self.allocations.values())


#: 允许的重排原因：预售取消、临期转渠道、节前加单。
REALLOCATION_REASONS = ("presale_cancelled", "near_expiry_transfer", "rush_order")


class Ledger:
    """追加式协作账：apply 成功即入账，违反规则抛 DomainError 且不留痕迹。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self._versions: dict[str, int] = {}
        self.suppliers: dict[str, Supplier] = {}
        self.lots: dict[str, Lot] = {}
        self.recipes: dict[tuple[str, int], Recipe] = {}
        self.workshops: dict[str, Workshop] = {}
        self.shifts: dict[str, Shift] = {}
        self.batches: dict[str, Batch] = {}
        self.orders: dict[str, Order] = {}
        self.shipments: list[dict] = []
        self.settlements: dict[str, dict] = {}
        self._settlement_keys: set[str] = set()
        self._covered_refs: set[tuple[str, str]] = set()

    def apply(self, event: dict) -> None:
        errors = validate_event(event)
        if errors:
            raise DomainError("；".join(errors))
        aggregate_id = event["aggregate_id"]
        expected = self._versions.get(aggregate_id, 0) + 1
        if event["version"] != expected:
            raise DomainError(
                f"聚合 {aggregate_id} 期望 version={expected}，收到 {event['version']}"
            )
        handler = getattr(self, f"_on_{event['event_type'].lower()}", None)
        if handler is None:
            raise DomainError(f"未支持的事件类型：{event['event_type']}")
        handler(event)
        self._versions[aggregate_id] = expected
        self.events.append(event)

    # -- 查询辅助 -----------------------------------------------------

    def _lot(self, lot_id: str) -> Lot:
        try:
            return self.lots[lot_id]
        except KeyError:
            raise DomainError(f"原料批次不存在：{lot_id}") from None

    def _batch(self, batch_id: str) -> Batch:
        try:
            return self.batches[batch_id]
        except KeyError:
            raise DomainError(f"成品批次不存在：{batch_id}") from None

    def _shift(self, shift_id: str) -> Shift:
        try:
            return self.shifts[shift_id]
        except KeyError:
            raise DomainError(f"班次不存在：{shift_id}") from None

    def _order(self, order_id: str) -> Order:
        try:
            return self.orders[order_id]
        except KeyError:
            raise DomainError(f"订单不存在：{order_id}") from None

    def _workshop(self, workshop_id: str) -> Workshop:
        try:
            return self.workshops[workshop_id]
        except KeyError:
            raise DomainError(f"工坊不存在：{workshop_id}") from None

    # -- 供应户、原料与检验 -------------------------------------------

    def _on_supplier_registered(self, event: dict) -> None:
        supplier_id = event["aggregate_id"]
        self.suppliers[supplier_id] = Supplier(
            supplier_id=supplier_id, name=event["name"], region=event["region"]
        )

    def _on_lot_accepted(self, event: dict) -> None:
        lot_id = event["aggregate_id"]
        if event["supplier_id"] not in self.suppliers:
            raise DomainError(f"供应户未登记：{event['supplier_id']}")
        quantity = event["quantity"]
        if not _is_number(quantity) or quantity <= 0:
            raise DomainError("原料数量必须为正数")
        self.lots[lot_id] = Lot(
            lot_id=lot_id,
            supplier_id=event["supplier_id"],
            ingredient=event["ingredient"],
            quantity=quantity,
            unit=event["unit"],
            allergens=frozenset(event["allergens"]),
            remaining=quantity,
        )

    def _on_lot_inspection_recorded(self, event: dict) -> None:
        lot = self._lot(event["aggregate_id"])
        if event["result"] not in ("passed", "failed"):
            raise DomainError("检验结果必须是 passed 或 failed")
        lot.inspection = event["result"]

    def _on_weighing_recorded(self, event: dict) -> None:
        lot = self._lot(event["aggregate_id"])
        weighed = event["weighed_quantity"]
        if not _is_number(weighed) or weighed <= 0:
            raise DomainError("称重数量必须为正数")
        lot.weighings.append({"quantity": weighed, "source": event["source"]})

    def _on_recall_started(self, event: dict) -> None:
        target = event["aggregate_id"]
        if event["aggregate_type"] == "ingredient_lot":
            self._lot(target).quarantined = True
        else:
            self._batch(target).recalled = True

    # -- 配方、工坊与班次 ---------------------------------------------

    def _on_recipe_version_published(self, event: dict) -> None:
        # aggregate_id 即配方标识，信封 version 即配方版本；版本发布后不可改写。
        recipe_id = event["aggregate_id"]
        self.recipes[(recipe_id, event["version"])] = Recipe(
            recipe_id=recipe_id,
            version=event["version"],
            workshop_id=event["workshop_id"],
            ingredients=dict(event["ingredients"]),
            declared_allergens=frozenset(event["declared_allergens"]),
        )

    def _on_workshop_certified(self, event: dict) -> None:
        workshop_id = event["aggregate_id"]
        masters = {m["master_id"]: m["daily_capacity"] for m in event["masters"]}
        self.workshops[workshop_id] = Workshop(
            workshop_id=workshop_id, name=event["name"], masters=masters
        )

    def _on_shift_recorded(self, event: dict) -> None:
        shift_id = event["aggregate_id"]
        workshop = self._workshop(event["workshop_id"])
        master_id = event["master_id"]
        if master_id not in workshop.masters:
            raise DomainError(f"师傅 {master_id} 未登记在工坊资质中")
        capacity = event["capacity"]
        if not _is_number(capacity) or capacity < 0:
            raise DomainError("班次产能不能为负")
        if capacity > workshop.masters[master_id]:
            raise DomainError("班次产能超出师傅日产能")
        self.shifts[shift_id] = Shift(
            shift_id=shift_id,
            workshop_id=workshop.workshop_id,
            master_id=master_id,
            date=event["date"],
            capacity=capacity,
        )

    def _on_labor_recorded(self, event: dict) -> None:
        shift = self._shift(event["aggregate_id"])
        hours = event["hours"]
        if not _is_number(hours) or hours <= 0:
            raise DomainError("工时必须为正数")
        if event["source"] not in ("online", "backfill"):
            raise DomainError("工时来源必须是 online 或 backfill")
        shift.labor.append(
            {"worker_id": event["worker_id"], "hours": hours, "source": event["source"]}
        )

    # -- 生产 ---------------------------------------------------------

    def _on_batch_produced(self, event: dict) -> None:
        batch_id = event["aggregate_id"]
        workshop = self._workshop(event["workshop_id"])
        shift = self._shift(event["shift_id"])
        if shift.workshop_id != workshop.workshop_id:
            raise DomainError("班次不属于该工坊")
        recipe = self.recipes.get((event["recipe_id"], event["recipe_version"]))
        if recipe is None:
            raise DomainError("配方版本未发布")
        if recipe.workshop_id != workshop.workshop_id:
            raise DomainError("该配方版本未授权给此工坊")
        quantity = event["quantity"]
        if not _is_number(quantity) or quantity <= 0:
            raise DomainError("产量必须为正数")

        usages: dict[str, float] = {}
        for usage in event["lot_usages"]:
            lot_id = usage["lot_id"]
            if lot_id in usages:
                raise DomainError(f"重复投料记录：{lot_id}")
            usages[lot_id] = usage["quantity"]

        actual_allergens: set[str] = set()
        for lot_id, used in usages.items():
            lot = self._lot(lot_id)
            if lot.inspection != "passed":
                raise DomainError(f"原料批次 {lot_id} 未检验合格")
            if lot.quarantined:
                raise DomainError(f"原料批次 {lot_id} 已被冻结")
            if not _is_number(used) or used <= 0 or used > lot.remaining:
                raise DomainError(f"原料批次 {lot_id} 余量不足")
            actual_allergens |= lot.allergens

        if set(event["label_allergens"]) != actual_allergens:
            raise DomainError("标签过敏原必须等于实际投料批次的过敏原并集")
        if shift.used + quantity > shift.capacity:
            raise DomainError("超出当班师傅产能")

        for lot_id, used in usages.items():
            self.lots[lot_id].remaining -= used
        shift.used += quantity
        produced_at = _ts(event["produced_at"])
        self.batches[batch_id] = Batch(
            batch_id=batch_id,
            shift_id=shift.shift_id,
            workshop_id=workshop.workshop_id,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            quantity=quantity,
            lot_usages=usages,
            produced_at=produced_at,
            expires_at=produced_at + timedelta(days=event["shelf_life_days"]),
            label_allergens=frozenset(event["label_allergens"]),
        )

    def _on_batch_released(self, event: dict) -> None:
        batch = self._batch(event["aggregate_id"])
        if batch.recalled:
            raise DomainError("已召回批次不能放行")
        batch.status = "released"

    # -- 订单、发货与退货 ---------------------------------------------

    def _on_order_reserved(self, event: dict) -> None:
        order_id = event["aggregate_id"]
        if not any(rid == event["recipe_id"] for rid, _ in self.recipes):
            raise DomainError("订单指定的配方未发布")
        quantity = event["quantity"]
        if not _is_number(quantity) or quantity <= 0:
            raise DomainError("订单数量必须为正数")
        self.orders[order_id] = Order(
            order_id=order_id,
            channel=event["channel"],
            quantity=quantity,
            due_at=_ts(event["due_at"]),
            recipe_id=event["recipe_id"],
        )

    def _check_allocations(self, order: Order, allocations: list) -> None:
        for alloc in allocations:
            batch = self._batch(alloc["batch_id"])
            if batch.status != "released":
                raise DomainError(f"成品批次 {batch.batch_id} 未放行")
            if batch.recalled:
                raise DomainError(f"成品批次 {batch.batch_id} 已召回")
            if batch.expires_at < order.due_at:
                raise DomainError(f"成品批次 {batch.batch_id} 先于订单交期过期")
            quantity = alloc["quantity"]
            if not _is_number(quantity) or quantity <= 0:
                raise DomainError("分配数量必须为正数")
            if batch.allocated + quantity > batch.quantity:
                raise DomainError(f"成品批次 {batch.batch_id} 可分配数量不足")

    def _apply_allocations(self, order: Order, allocations: list, sign: int) -> None:
        for alloc in allocations:
            batch = self.batches[alloc["batch_id"]]
            delta = sign * alloc["quantity"]
            batch.allocated += delta
            remaining = order.allocations.get(batch.batch_id, 0) + delta
            if remaining:
                order.allocations[batch.batch_id] = remaining
            else:
                order.allocations.pop(batch.batch_id, None)

    def _on_order_allocated(self, event: dict) -> None:
        order = self._order(event["aggregate_id"])
        if order.cancelled:
            raise DomainError("订单已取消")
        allocations = event["allocations"]
        self._check_allocations(order, allocations)
        if order.allocated + sum(a["quantity"] for a in allocations) > order.quantity:
            raise DomainError("分配总量超出订单数量")
        self._apply_allocations(order, allocations, +1)

    def _on_order_reallocated(self, event: dict) -> None:
        order = self._order(event["aggregate_id"])
        if order.cancelled:
            raise DomainError("订单已取消")
        if event["reason"] not in REALLOCATION_REASONS:
            raise DomainError("重排原因必须是预售取消、临期转渠道或节前加单")
        deallocations = event["deallocations"]
        allocations = event["allocations"]
        for dealloc in deallocations:
            quantity = dealloc["quantity"]
            if not _is_number(quantity) or quantity <= 0:
                raise DomainError("解除分配数量必须为正数")
            batch_id = dealloc["batch_id"]
            self._batch(batch_id)
            remaining = order.allocations.get(batch_id, 0) - quantity
            if remaining < 0:
                raise DomainError("解除分配超出已分配数量")
            if remaining < order.shipped_per_batch.get(batch_id, 0):
                raise DomainError("不能解除已发货部分的分配")
        self._check_allocations(order, allocations)
        delta = sum(a["quantity"] for a in allocations) - sum(
            d["quantity"] for d in deallocations
        )
        if order.allocated + delta > order.quantity:
            raise DomainError("分配总量超出订单数量")
        self._apply_allocations(order, deallocations, -1)
        self._apply_allocations(order, allocations, +1)

    def _on_order_cancelled(self, event: dict) -> None:
        order = self._order(event["aggregate_id"])
        if order.shipped > 0:
            raise DomainError("已发货订单不能整体取消")
        self._apply_allocations(
            order,
            [{"batch_id": b, "quantity": q} for b, q in order.allocations.items()],
            -1,
        )
        order.cancelled = True

    def _on_shipment_dispatched(self, event: dict) -> None:
        order = self._order(event["order_id"])
        if order.cancelled:
            raise DomainError("订单已取消")
        batch = self._batch(event["batch_id"])
        if batch.recalled or batch.status != "released":
            raise DomainError("批次不可发货")
        quantity = event["quantity"]
        if not _is_number(quantity) or quantity <= 0:
            raise DomainError("发货数量必须为正数")
        allocated = order.allocations.get(batch.batch_id, 0)
        if order.shipped_per_batch.get(batch.batch_id, 0) + quantity > allocated:
            raise DomainError("发货超出该批次对订单的分配量")
        order.shipped += quantity
        order.shipped_per_batch[batch.batch_id] = (
            order.shipped_per_batch.get(batch.batch_id, 0) + quantity
        )
        self.shipments.append(
            {
                "shipment_id": event["aggregate_id"],
                "order_id": order.order_id,
                "batch_id": batch.batch_id,
                "quantity": quantity,
                "channel": event["channel"],
            }
        )

    def _on_return_received(self, event: dict) -> None:
        order = self._order(event["aggregate_id"])
        order.returns.append(
            {
                "return_id": event["return_id"],
                "reason": event["reason"],
                "batch_id": event.get("batch_id"),
            }
        )

    # -- 结算 ---------------------------------------------------------

    def _on_settlement_paid(self, event: dict) -> None:
        key = event["idempotency_key"]
        if key in self._settlement_keys:
            raise DomainError(f"重复付款：幂等键 {key} 已使用")
        covers = event["covers"]
        if not isinstance(covers, dict) or "kind" not in covers or "ref_id" not in covers:
            raise DomainError("covers 必须包含 kind 与 ref_id")
        ref = (covers["kind"], covers["ref_id"])
        if ref in self._covered_refs:
            raise DomainError("该称重/工时记录已结算，不能重复付款")
        amount = event["amount"]
        if not _is_number(amount) or amount <= 0:
            raise DomainError("结算金额必须为正数")
        if event["payee_type"] not in ("worker", "supplier"):
            raise DomainError("收款方类型必须是 worker 或 supplier")
        self._settlement_keys.add(key)
        self._covered_refs.add(ref)
        self.settlements[event["aggregate_id"]] = {
            "payee_type": event["payee_type"],
            "payee_id": event["payee_id"],
            "amount": amount,
            "covers": covers,
        }
