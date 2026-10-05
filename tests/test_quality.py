"""质量门槛：检验、保质期、资质、配方批准与过敏原标签。"""
import unittest
from datetime import datetime

from src.commands import build_event
from src.ledger import EventStore
from src.quality import (
    allergens_introduced,
    can_release,
    check_label,
    check_production,
    required_label_allergens,
)
from src.readmodel import World
from tests.fixtures import build_store, emit


class QualityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = build_store()
        self.world = World(self.store.events)

    def test_fixture_batches_release_clean(self) -> None:
        for bid in ("BATCH-A-0920-01", "BATCH-A-0920-02", "BATCH-B-0920-01"):
            self.assertEqual(can_release(self.world, bid), [], bid)

    # ---- 投产前检查 ----------------------------------------------------------

    def test_rejects_uninspected_and_quarantined_lot(self) -> None:
        store = build_store(with_recall=False)
        # 新增一个只进场未检验的批次
        emit(store, "INGREDIENT_LOT_RECEIVED", "LOT-RAW-X", {
            "ingredient": "叉烧", "supplier_id": "SUP-S02",
            "quantity_kg": 5, "received_on": "2026-09-19", "shelf_life_days": 12,
        }, "2026-09-19T08:30:00+08:00", summary="未检验叉烧进场")
        world = World(store.events)
        draft = {
            "workshop_id": "WS-A", "shift_id": "SHIFT-A-0920",
            "recipe_id": "RECIPE-CSWR", "recipe_version": 1,
            "quantity_kg": 5, "produced_at": "2026-09-20T11:00:00+08:00",
            "best_before": "2026-10-05",
            "ingredient_lots": [
                {"lot_id": "LOT-RAW-X", "ingredient": "叉烧", "quantity_kg": 5}
            ],
        }
        errors = check_production(world, draft)
        self.assertTrue(any("未合格放行" in e for e in errors))

    def test_rejects_expired_lot_and_overconsumption(self) -> None:
        store = EventStore()
        emit(store, "SUPPLIER_REGISTERED", "S1",
             {"name": "户", "village": "村", "supplies": ["花生仁"]},
             "2026-09-01T09:00:00+08:00")
        emit(store, "WORKSHOP_QUALIFIED", "W1", {
            "name": "坊", "qualification_no": "Q1",
            "valid_until": "2026-12-31", "daily_capacity_kg": 50,
        }, "2026-09-01T10:00:00+08:00")
        emit(store, "INGREDIENT_LOT_RECEIVED", "L1", {
            "ingredient": "花生仁", "supplier_id": "S1", "quantity_kg": 10,
            "received_on": "2026-06-01", "shelf_life_days": 90,  # 8月30到期
        }, "2026-09-01T08:30:00+08:00")
        emit(store, "INGREDIENT_LOT_INSPECTED", "L1",
             {"result": "合格", "inspected_at": "2026-09-01T09:00:00+08:00"},
             "2026-09-01T09:00:00+08:00")
        emit(store, "RECIPE_VERSION_PUBLISHED", "R1", {
            "recipe_name": "五仁", "recipe_version": 1, "workshop_id": "W1",
            "ingredients": [{"ingredient": "花生仁", "quantity_kg": 5}],
            "declared_allergens": ["花生"],
            "approved": True, "published_at": "2026-09-01T12:00:00+08:00",
        }, "2026-09-01T12:00:00+08:00")
        emit(store, "PRODUCTION_SHIFT_OPENED", "SH1", {
            "workshop_id": "W1", "shift_date": "2026-09-20",
            "master_id": "M1", "planned_kg": 10,
        }, "2026-09-20T06:00:00+08:00")
        draft = {
            "workshop_id": "W1", "shift_id": "SH1",
            "recipe_id": "R1", "recipe_version": 1,
            "quantity_kg": 10, "produced_at": "2026-09-20T11:00:00+08:00",
            "best_before": "2026-10-05",
            "ingredient_lots": [
                {"lot_id": "L1", "ingredient": "花生仁", "quantity_kg": 12}
            ],
        }
        errors = check_production(World(store.events), draft)
        self.assertTrue(any("过保质期" in e for e in errors))
        self.assertTrue(any("库存不足" in e for e in errors))

    def test_recipe_must_be_approved_and_belong_to_workshop(self) -> None:
        store = EventStore()
        emit(store, "SUPPLIER_REGISTERED", "S1",
             {"name": "户", "village": "村", "supplies": ["叉烧"]},
             "2026-09-01T09:00:00+08:00")
        emit(store, "WORKSHOP_QUALIFIED", "W1", {
            "name": "坊", "qualification_no": "Q1",
            "valid_until": "2026-12-31", "daily_capacity_kg": 50,
        }, "2026-09-01T10:00:00+08:00")
        emit(store, "INGREDIENT_LOT_RECEIVED", "L1", {
            "ingredient": "叉烧", "supplier_id": "S1", "quantity_kg": 5,
            "received_on": "2026-09-19", "shelf_life_days": 12,
        }, "2026-09-19T08:30:00+08:00")
        emit(store, "INGREDIENT_LOT_INSPECTED", "L1",
             {"result": "合格", "inspected_at": "2026-09-19T09:00:00+08:00"},
             "2026-09-19T09:00:00+08:00")
        emit(store, "PRODUCTION_SHIFT_OPENED", "SH1", {
            "workshop_id": "W1", "shift_date": "2026-09-20",
            "master_id": "M1", "planned_kg": 10,
        }, "2026-09-20T06:00:00+08:00")
        # 未批准配方
        emit(store, "RECIPE_VERSION_PUBLISHED", "R1", {
            "recipe_name": "试验方", "recipe_version": 1, "workshop_id": "W1",
            "ingredients": [{"ingredient": "叉烧", "quantity_kg": 5}],
            "declared_allergens": ["大豆", "小麦麸质"],
            "approved": False, "published_at": "2026-09-19T12:00:00+08:00",
        }, "2026-09-19T12:00:00+08:00")
        draft = {
            "workshop_id": "W1", "shift_id": "SH1",
            "recipe_id": "R1", "recipe_version": 1,
            "quantity_kg": 5, "produced_at": "2026-09-20T11:00:00+08:00",
            "best_before": "2026-10-05",
            "ingredient_lots": [
                {"lot_id": "L1", "ingredient": "叉烧", "quantity_kg": 5}
            ],
        }
        errors = check_production(World(store.events), draft)
        self.assertTrue(any("未经批准" in e for e in errors))

    # ---- 过敏原标签 ----------------------------------------------------------

    def test_label_must_cover_actual_allergens(self) -> None:
        world = self.world
        required = required_label_allergens(world, "BATCH-A-0920-01")
        # 叉烧（大豆+麸质）+ 花生仁（花生）+ 芝麻（芝麻）+ 面粉麸质
        self.assertEqual(required, {"花生", "芝麻", "大豆", "小麦麸质"})
        self.assertEqual(check_label(world, "BATCH-A-0920-01"), [])

    def test_missing_allergen_blocks_release(self) -> None:
        store = build_store()
        # 覆盖标签为少标：漏了芝麻与大豆
        bid = "BATCH-A-0920-01"
        version = len(store.for_aggregate(bid)) + 1
        store.append({
            "event_id": f"{bid}#{version}",
            "event_type": "BATCH_ALLERGEN_LABEL_FIXED",
            "aggregate_type": "production_batch",
            "aggregate_id": bid,
            "occurred_at": "2026-09-24T12:00:00+08:00",
            "version": version,
            "summary": "更正标签仍然漏标",
            "payload": {
                "label_allergens": ["花生", "小麦麸质"],
                "fixed_at": "2026-09-24T12:00:00+08:00",
                "recipe_version_snapshot": 1,
            },
        })
        world = World(store.events)
        errors = can_release(world, bid)
        self.assertTrue(any("漏标" in e for e in errors))

    def test_local_recipe_variant_label_differs(self) -> None:
        # 枫木坊少芝麻版标签必须真的没有芝麻，但叉烧引入的大豆/麸质不能少
        required_b = required_label_allergens(self.world, "BATCH-B-0920-01")
        self.assertNotIn("芝麻", required_b)
        self.assertEqual(required_b, {"花生", "大豆", "小麦麸质"})

    def test_allergen_vocabulary_is_controlled(self) -> None:
        introduced = allergens_introduced(["叉烧", "花生仁", "芝麻", "面粉"])
        self.assertEqual(introduced, {"花生", "芝麻", "大豆", "小麦麸质"})


if __name__ == "__main__":
    unittest.main()
