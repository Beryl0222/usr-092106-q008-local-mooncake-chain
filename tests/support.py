"""测试共用的造数工具。"""

from __future__ import annotations

from src.ledger import Ledger


class Scenario:
    """按聚合维护版本号，快速搭出一条完整的业务链。

    emit 失败（DomainError）时不推进版本号，场景可继续使用。
    """

    def __init__(self) -> None:
        self.ledger = Ledger()
        self._versions: dict[str, int] = {}
        self._seq = 0

    def emit(self, event_type: str, aggregate_type: str, aggregate_id: str, **payload) -> dict:
        self._seq += 1
        version = self._versions.get(aggregate_id, 0) + 1
        event = {
            "event_id": f"evt-{self._seq:04d}",
            "event_type": event_type,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "occurred_at": "2026-09-20T08:00:00+08:00",
            "version": version,
            "summary": "测试事件",
            **payload,
        }
        self.ledger.apply(event)
        self._versions[aggregate_id] = version
        return event

    def base_chain(self, *, capacity: float = 100, lot_quantity: float = 500) -> None:
        """供应户、工坊资质、配方版本、合格原料批次与班次。"""
        self.emit(
            "SUPPLIER_REGISTERED", "supplier", "sup-1",
            name="屯昌某村供应户", region="屯昌县乌坡镇",
        )
        self.emit(
            "WORKSHOP_CERTIFIED", "workshop", "ws-1",
            name="屯昌香工坊",
            masters=[{"master_id": "m-1", "daily_capacity": capacity}],
        )
        self.emit(
            "RECIPE_VERSION_PUBLISHED", "recipe_version", "recipe-五仁",
            workshop_id="ws-1",
            ingredients={"果仁": 0.3, "叉烧": 0.2},
            declared_allergens=["核桃", "花生"],
        )
        self.emit(
            "LOT_ACCEPTED", "ingredient_lot", "lot-1",
            supplier_id="sup-1", ingredient="果仁",
            quantity=lot_quantity, unit="kg",
            allergens=["核桃", "花生"],
        )
        self.emit(
            "LOT_INSPECTION_RECORDED", "ingredient_lot", "lot-1",
            result="passed", inspector="质检员甲",
        )
        self.emit(
            "SHIFT_RECORDED", "shift", "shift-1",
            workshop_id="ws-1", master_id="m-1",
            date="2026-09-25", capacity=capacity,
        )

    def produce(
        self,
        batch_id: str = "batch-1",
        quantity: float = 60,
        *,
        lot_usages=None,
        label_allergens=("核桃", "花生"),
        shift_id: str = "shift-1",
        shelf_life_days: int = 30,
        released: bool = True,
    ) -> None:
        self.emit(
            "BATCH_PRODUCED", "production_batch", batch_id,
            shift_id=shift_id, workshop_id="ws-1",
            recipe_id="recipe-五仁", recipe_version=1,
            quantity=quantity,
            lot_usages=lot_usages or [{"lot_id": "lot-1", "quantity": quantity * 0.3}],
            produced_at="2026-09-25T10:00:00+08:00",
            shelf_life_days=shelf_life_days,
            label_allergens=list(label_allergens),
        )
        if released:
            self.emit(
                "BATCH_RELEASED", "production_batch", batch_id,
                released_by="质检员乙",
            )

    def reserve(
        self,
        order_id: str = "order-1",
        quantity: float = 60,
        *,
        due_at: str = "2026-10-01T00:00:00+08:00",
        channel: str = "电商预售",
    ) -> None:
        self.emit(
            "ORDER_RESERVED", "sales_order", order_id,
            channel=channel, quantity=quantity,
            due_at=due_at, recipe_id="recipe-五仁",
        )
