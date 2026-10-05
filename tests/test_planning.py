import unittest

from src.planning import delivery_risks, material_shortages
from tests.support import Scenario


class PlanningTest(unittest.TestCase):
    def test_material_shortage_visible_before_production(self) -> None:
        s = Scenario()
        s.base_chain()
        s.reserve("order-1", 2000)  # 需果仁 600kg、叉烧 400kg，库存都不够
        shortages = {item["ingredient"]: item for item in material_shortages(s.ledger)}
        self.assertAlmostEqual(shortages["果仁"]["shortage"], 100.0)
        self.assertAlmostEqual(shortages["叉烧"]["shortage"], 400.0)

    def test_delivery_risk_clears_after_adding_shift(self) -> None:
        s = Scenario()
        s.base_chain(capacity=100)
        s.produce("batch-1", 100)  # 当班产能已排满
        s.reserve("order-1", 150)
        s.emit(
            "ORDER_ALLOCATED", "sales_order", "order-1",
            allocations=[{"batch_id": "batch-1", "quantity": 100}],
        )
        risks = delivery_risks(s.ledger, "2026-09-24T09:00:00+08:00")
        self.assertEqual([r["order_id"] for r in risks], ["order-1"])
        self.assertEqual(risks[0]["reason"], "交期前产能不足")
        s.emit(
            "SHIFT_RECORDED", "shift", "shift-2",
            workshop_id="ws-1", master_id="m-1", date="2026-09-28", capacity=50,
        )
        self.assertEqual(delivery_risks(s.ledger, "2026-09-24T09:00:00+08:00"), [])

    def test_recalled_allocation_flagged(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60)
        s.reserve("order-1", 60)
        s.emit(
            "ORDER_ALLOCATED", "sales_order", "order-1",
            allocations=[{"batch_id": "batch-1", "quantity": 60}],
        )
        s.emit("RECALL_STARTED", "production_batch", "batch-1", reason="留样检出异常")
        risks = delivery_risks(s.ledger, "2026-09-24T09:00:00+08:00")
        self.assertEqual(risks[0]["reason"], "已分配批次被召回")
        self.assertEqual(risks[0]["batch_id"], "batch-1")


if __name__ == "__main__":
    unittest.main()
