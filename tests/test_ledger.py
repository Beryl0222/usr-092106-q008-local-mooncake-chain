import unittest

from src.ledger import DomainError
from tests.support import Scenario


class LedgerRulesTest(unittest.TestCase):
    def test_uninspected_lot_cannot_be_used(self) -> None:
        s = Scenario()
        s.emit("SUPPLIER_REGISTERED", "supplier", "sup-1", name="供应户", region="屯昌县")
        s.emit(
            "WORKSHOP_CERTIFIED", "workshop", "ws-1", name="工坊",
            masters=[{"master_id": "m-1", "daily_capacity": 100}],
        )
        s.emit(
            "RECIPE_VERSION_PUBLISHED", "recipe_version", "recipe-五仁",
            workshop_id="ws-1", ingredients={"果仁": 0.3}, declared_allergens=["核桃"],
        )
        s.emit(
            "LOT_ACCEPTED", "ingredient_lot", "lot-1",
            supplier_id="sup-1", ingredient="果仁", quantity=100, unit="kg",
            allergens=["核桃"],
        )
        s.emit(
            "SHIFT_RECORDED", "shift", "shift-1",
            workshop_id="ws-1", master_id="m-1", date="2026-09-25", capacity=100,
        )
        with self.assertRaises(DomainError):
            s.produce(label_allergens=("核桃",))

    def test_label_allergens_must_match_actual(self) -> None:
        s = Scenario()
        s.base_chain()
        with self.assertRaises(DomainError):
            s.produce(label_allergens=("核桃",))  # 标签漏了实际投料里的花生

    def test_shift_capacity_enforced(self) -> None:
        s = Scenario()
        s.base_chain(capacity=100)
        s.produce("batch-1", 70)
        with self.assertRaises(DomainError):
            s.produce("batch-2", 40)

    def test_quarantined_lot_cannot_be_used(self) -> None:
        s = Scenario()
        s.base_chain()
        s.emit("RECALL_STARTED", "ingredient_lot", "lot-1", reason="检出黄曲霉毒素")
        with self.assertRaises(DomainError):
            s.produce()

    def test_event_version_must_increase(self) -> None:
        s = Scenario()
        s.base_chain()
        with self.assertRaises(DomainError):
            s.ledger.apply(dict(s.ledger.events[0]))

    def test_allocation_requires_release(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60, released=False)
        s.reserve("order-1", 60)
        with self.assertRaises(DomainError):
            s.emit(
                "ORDER_ALLOCATED", "sales_order", "order-1",
                allocations=[{"batch_id": "batch-1", "quantity": 60}],
            )

    def test_expired_batch_cannot_cover_order(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60, shelf_life_days=3)  # 09-28 即过期
        s.reserve("order-1", 60, due_at="2026-10-01T00:00:00+08:00")
        with self.assertRaises(DomainError):
            s.emit(
                "ORDER_ALLOCATED", "sales_order", "order-1",
                allocations=[{"batch_id": "batch-1", "quantity": 60}],
            )

    def test_reallocation_stays_within_rules(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60)
        s.produce("batch-2", 40)
        s.reserve("order-1", 60)
        s.emit(
            "ORDER_ALLOCATED", "sales_order", "order-1",
            allocations=[{"batch_id": "batch-1", "quantity": 60}],
        )
        s.emit(
            "ORDER_REALLOCATED", "sales_order", "order-1",
            deallocations=[{"batch_id": "batch-1", "quantity": 20}],
            allocations=[{"batch_id": "batch-2", "quantity": 20}],
            reason="rush_order",
        )
        order = s.ledger.orders["order-1"]
        self.assertEqual(order.allocations, {"batch-1": 40, "batch-2": 20})
        with self.assertRaises(DomainError):
            s.emit(
                "ORDER_REALLOCATED", "sales_order", "order-1",
                deallocations=[], allocations=[], reason="随便改",
            )

    def test_presale_cancel_releases_allocation(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60)
        s.reserve("order-1", 60)
        s.emit(
            "ORDER_ALLOCATED", "sales_order", "order-1",
            allocations=[{"batch_id": "batch-1", "quantity": 60}],
        )
        s.emit("ORDER_CANCELLED", "sales_order", "order-1", reason="预售取消")
        self.assertTrue(s.ledger.orders["order-1"].cancelled)
        self.assertEqual(s.ledger.batches["batch-1"].allocated, 0)

    def test_settlement_paid_only_once(self) -> None:
        s = Scenario()
        s.base_chain()
        s.emit(
            "LABOR_RECORDED", "shift", "shift-1",
            worker_id="worker-9", hours=6, source="backfill",
        )
        s.emit(
            "SETTLEMENT_PAID", "worker_settlement", "settle-1",
            payee_type="worker", payee_id="worker-9", amount=300,
            idempotency_key="labor:shift-1:worker-9",
            covers={"kind": "labor_backfill", "ref_id": "shift-1:worker-9"},
        )
        with self.assertRaises(DomainError):  # 同一幂等键
            s.emit(
                "SETTLEMENT_PAID", "worker_settlement", "settle-2",
                payee_type="worker", payee_id="worker-9", amount=300,
                idempotency_key="labor:shift-1:worker-9",
                covers={"kind": "labor_backfill", "ref_id": "shift-1:worker-9:retry"},
            )
        with self.assertRaises(DomainError):  # 同一补录记录换键再付
            s.emit(
                "SETTLEMENT_PAID", "worker_settlement", "settle-3",
                payee_type="worker", payee_id="worker-9", amount=300,
                idempotency_key="labor:shift-1:worker-9:v2",
                covers={"kind": "labor_backfill", "ref_id": "shift-1:worker-9"},
            )
        self.assertEqual(len(s.ledger.settlements), 1)


if __name__ == "__main__":
    unittest.main()
