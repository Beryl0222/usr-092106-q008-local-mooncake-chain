import unittest

from src.trace import recall_scope, trace_lot, trace_order
from tests.support import Scenario


def build_chain() -> Scenario:
    """两个批次、两笔订单：batch-2 用了后来判定异常的 lot-2。"""
    s = Scenario()
    s.base_chain()
    s.emit(
        "LOT_ACCEPTED", "ingredient_lot", "lot-2",
        supplier_id="sup-1", ingredient="叉烧", quantity=200, unit="kg",
        allergens=["大豆"],
    )
    s.emit(
        "LOT_INSPECTION_RECORDED", "ingredient_lot", "lot-2",
        result="passed", inspector="质检员甲",
    )
    s.produce("batch-1", 60)  # 只用 lot-1
    s.produce(
        "batch-2", 40,
        lot_usages=[
            {"lot_id": "lot-1", "quantity": 12},
            {"lot_id": "lot-2", "quantity": 8},
        ],
        label_allergens=("核桃", "花生", "大豆"),
    )
    s.reserve("order-1", 60)
    s.emit(
        "ORDER_ALLOCATED", "sales_order", "order-1",
        allocations=[{"batch_id": "batch-1", "quantity": 60}],
    )
    s.reserve("order-2", 40, channel="直播渠道")
    s.emit(
        "ORDER_ALLOCATED", "sales_order", "order-2",
        allocations=[{"batch_id": "batch-2", "quantity": 40}],
    )
    s.emit(
        "SHIPMENT_DISPATCHED", "shipment", "ship-1",
        order_id="order-2", batch_id="batch-2", quantity=40, channel="直播渠道",
    )
    return s


class TraceTest(unittest.TestCase):
    def test_return_reverse_traces_to_shift_and_lots(self) -> None:
        s = build_chain()
        s.emit(
            "RETURN_RECEIVED", "sales_order", "order-2",
            return_id="ret-1", reason="口感问题", batch_id="batch-2",
        )
        view = trace_order(s.ledger, "order-2")
        self.assertEqual(view["returns"][0]["return_id"], "ret-1")
        batch = view["batches"]["batch-2"]
        self.assertEqual(batch["shift_id"], "shift-1")
        self.assertEqual(batch["workshop_id"], "ws-1")
        self.assertEqual({lot["lot_id"] for lot in batch["lots"]}, {"lot-1", "lot-2"})
        self.assertEqual(batch["lots"][0]["region"], "屯昌县乌坡镇")
        self.assertEqual(view["shipments"][0]["shipment_id"], "ship-1")

    def test_abnormal_lot_freeze_scope_is_precise(self) -> None:
        s = build_chain()
        s.emit("RECALL_STARTED", "ingredient_lot", "lot-2", reason="复检不合格")
        scope = recall_scope(s.ledger, "ingredient_lot", "lot-2")
        self.assertEqual(scope["batches"], ["batch-2"])
        self.assertEqual(scope["orders"], ["order-2"])
        self.assertEqual(scope["shipments"], ["ship-1"])
        self.assertNotIn("order-1", scope["orders"])  # 不扩大冻结范围
        forward = trace_lot(s.ledger, "lot-2")
        self.assertEqual(forward["orders"], ["order-2"])
        self.assertTrue(forward["quarantined"])


if __name__ == "__main__":
    unittest.main()
