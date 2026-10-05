"""双向追溯：退货反查与异常原料的精确影响面/冻结范围。"""
import unittest
from datetime import datetime

from src import commands
from src.readmodel import World
from src.traceability import (
    affected_orders,
    batch_to_lots,
    freeze_scope,
    lots_to_batches,
    return_provenance,
)
from tests.fixtures import build_store


class TraceabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = build_store()
        self.world = World(self.store.events)

    # ---- 反向：退货 → 成品/班次/原料 ----------------------------------------

    def test_return_traces_back_to_batch_shift_and_suppliers(self) -> None:
        provenance = return_provenance(self.world, "SHIP-3001")
        self.assertTrue(provenance["found"])
        self.assertEqual(provenance["batch"]["batch_id"], "BATCH-A-0920-01")
        self.assertEqual(provenance["shift"]["shift_id"], "SHIFT-A-0920")
        self.assertEqual(provenance["shift"]["master_id"], "MST-01")
        self.assertEqual(provenance["shift"]["recipe_version"], 1)
        lot_ids = {row["lot_id"] for row in provenance["ingredient_lots"]}
        self.assertEqual(
            lot_ids,
            {"LOT-CS-0916", "LOT-PN-0915", "LOT-SES-0916", "LOT-FL-0915"},
        )
        suppliers = {row["supplier_id"] for row in provenance["ingredient_lots"]}
        self.assertEqual(suppliers, {"SUP-S02", "SUP-S01", "SUP-S03"})

    def test_batch_to_lots_carries_inspection_state(self) -> None:
        rows = batch_to_lots(self.world, "BATCH-A-0920-02")
        by_lot = {r["lot_id"]: r for r in rows}
        self.assertTrue(by_lot["LOT-CS-0918"]["quarantined"])
        self.assertEqual(by_lot["LOT-PN-0918"]["inspection"], "合格")

    # ---- 正向：异常原料 → 受影响成品与订单 -----------------------------------

    def test_bad_lot_maps_exactly_to_batches_using_it(self) -> None:
        impact = lots_to_batches(self.world, ["LOT-CS-0918"])
        self.assertEqual(impact["LOT-CS-0918"], ["BATCH-A-0920-02"])
        # 同品类不同批次（0916 叉烧）不受影响
        impact_good = lots_to_batches(self.world, ["LOT-CS-0916"])
        self.assertEqual(set(impact_good["LOT-CS-0916"]),
                         {"BATCH-A-0920-01", "BATCH-B-0920-01"})

    def test_affected_orders_does_not_widen_scope(self) -> None:
        scope = affected_orders(self.world, ["LOT-CS-0918"])
        self.assertEqual(scope["batch_ids"], ["BATCH-A-0920-02"])
        self.assertEqual(scope["reserved_order_ids"], ["O-ECOM-1002"])
        # O-ECOM-1001/1003 用的是 0920-01，团购单用枫木批次，都不得被带入
        self.assertNotIn("O-ECOM-1001", scope["reserved_order_ids"])
        self.assertNotIn("O-ECOM-1003", scope["reserved_order_ids"])
        self.assertNotIn("O-GROUP-2001", scope["reserved_order_ids"])
        # 异常批次尚未发货，故没有在途发货需要拦截
        self.assertEqual(scope["dispatched"], [])

    def test_freeze_scope_covers_dispatched_goods_when_present(self) -> None:
        # 让 LOT-PN-0915 成为异常批：它的下游 0920-01 已有发货
        store = build_store()
        world = World(store.events)
        scope = affected_orders(world, ["LOT-PN-0915"])
        self.assertEqual(set(scope["batch_ids"]),
                         {"BATCH-A-0920-01", "BATCH-B-0920-01"})
        shipments = {d["shipment_id"] for d in scope["dispatched"]}
        self.assertIn("SHIP-3001", shipments)

    # ---- 精确冻结：只追加 BATCH_BLOCKED --------------------------------------

    def test_freeze_recall_blocks_only_impacted_batch(self) -> None:
        before = len(self.store.events)
        blocked = commands.freeze_recall_batches(
            self.store, "RECALL-8001",
            at=datetime.fromisoformat("2026-09-24T10:10:00+08:00"),
        )
        self.assertEqual(blocked, ["BATCH-A-0920-02"])
        self.assertEqual(len(self.store.events) - before, 1)
        world = World(self.store.events)
        self.assertTrue(world.batches["BATCH-A-0920-02"]["blocked"])
        self.assertFalse(world.batches["BATCH-A-0920-01"]["blocked"])
        self.assertFalse(world.batches["BATCH-B-0920-01"]["blocked"])
        # 再次执行不会重复冻结
        again = commands.freeze_recall_batches(
            self.store, "RECALL-8001",
            at=datetime.fromisoformat("2026-09-24T10:20:00+08:00"),
        )
        self.assertEqual(again, [])

    def test_freeze_scope_unknown_recall_raises(self) -> None:
        with self.assertRaises(KeyError):
            freeze_scope(self.world, "RECALL-NOPE")


if __name__ == "__main__":
    unittest.main()
