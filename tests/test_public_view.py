import json
import unittest

from src.public_view import public_trace_view
from tests.support import Scenario


class PublicViewTest(unittest.TestCase):
    def test_public_view_shows_origin_and_allergens(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60)
        view = public_trace_view(s.ledger, "batch-1")
        self.assertEqual(view["origins"], ["屯昌县乌坡镇"])
        self.assertEqual(view["allergens"], ["核桃", "花生"])
        self.assertEqual(view["workshop"], "屯昌香工坊")
        self.assertEqual(view["inspection"], "检验合格")

    def test_public_view_hides_villager_income(self) -> None:
        s = Scenario()
        s.base_chain()
        s.produce("batch-1", 60)
        s.emit(
            "WEIGHING_RECORDED", "ingredient_lot", "lot-1",
            weighed_quantity=498, source="offline_scale",
        )
        s.emit(
            "SETTLEMENT_PAID", "worker_settlement", "settle-1",
            payee_type="supplier", payee_id="sup-1", amount=9960,
            idempotency_key="weigh:lot-1",
            covers={"kind": "offline_weighing", "ref_id": "lot-1"},
        )
        s.emit(
            "LABOR_RECORDED", "shift", "shift-1",
            worker_id="worker-9", hours=6, source="backfill",
        )
        s.emit(
            "SETTLEMENT_PAID", "worker_settlement", "settle-2",
            payee_type="worker", payee_id="worker-9", amount=300,
            idempotency_key="labor:shift-1:worker-9",
            covers={"kind": "labor_backfill", "ref_id": "shift-1:worker-9"},
        )
        text = json.dumps(public_trace_view(s.ledger, "batch-1"), ensure_ascii=False)
        for leaked in ("9960", "worker-9", "sup-1", "供应户", "settle", "amount", "payee"):
            self.assertNotIn(leaked, text)


if __name__ == "__main__":
    unittest.main()
