"""产能内重排：取消释放、临期转渠道、节前加单与风险预警。"""
import unittest
from datetime import date, datetime

from src import commands
from src.planning import (
    delivery_risks,
    reassign,
    rechannel_near_expiry,
    remaining_capacity,
)
from src.readmodel import World
from tests.fixtures import build_store, emit

AT = datetime.fromisoformat("2026-09-24T11:00:00+08:00")


def accept_rush(store, oid, qty, needed_by):
    return emit(store, "ORDER_RUSH_ACCEPTED", oid, {
        "quantity_kg": qty, "needed_by": needed_by,
        "accepted_at": "2026-09-24T10:30:00+08:00", "plan_id": "PLAN-0924",
    }, "2026-09-24T10:30:00+08:00", summary=f"节前加单 {qty}kg")


class PlanningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = build_store()
        self.world = World(self.store.events)
        # 夹具可售余量：0920-01=6（28-12-10），0920-02=8（16-8），B 批=8（18-10）

    def test_daily_capacity_accounts_for_shift_plan(self) -> None:
        self.assertEqual(remaining_capacity(self.world, "WS-A", date(2026, 9, 20)), 16)
        self.assertEqual(remaining_capacity(self.world, "WS-B", date(2026, 9, 20)), 22)
        # 无资质备案的日子不能排产
        self.assertEqual(remaining_capacity(self.world, "WS-A", date(2027, 1, 1)), 0)

    def test_cancelled_presale_frees_stock_for_rush(self) -> None:
        store = build_store()
        accept_rush(store, "O-RUSH-9001", 8, "2026-09-26")
        result = reassign(
            World(store.events),
            plan_id="PLAN-0924", trigger="节前加单", at=AT,
            cancelled_order_ids=["O-ECOM-1002"],  # 释放 0920-02 的 8kg
            rush_order_ids=["O-RUSH-9001"],
        )
        self.assertEqual(result["unmet"], [])
        self.assertEqual(len(result["assignments"]), 1)
        self.assertEqual(result["assignments"][0],
                         {"order_id": "O-RUSH-9001", "batch_id": "BATCH-A-0920-02",
                          "quantity_kg": 8})
        # 草案入账后订单确实改挂到释放出来的批次
        commands.commit_drafts(store, result["events"], occurred_at=AT)
        world = World(store.events)
        self.assertEqual(world.orders["O-RUSH-9001"]["batch_id"], "BATCH-A-0920-02")
        self.assertEqual(world.orders["O-ECOM-1002"]["status"], "cancelled")

    def test_rush_beyond_free_stock_raises_shortage_risk(self) -> None:
        store = build_store()
        accept_rush(store, "O-RUSH-9002", 25, "2026-09-26")  # 总可售仅 22
        result = reassign(
            World(store.events),
            plan_id="PLAN-0924", trigger="节前加单", at=AT,
            rush_order_ids=["O-RUSH-9002"],
        )
        assigned = sum(a["quantity_kg"] for a in result["assignments"])
        self.assertAlmostEqual(assigned, 22, places=3)
        self.assertEqual(result["unmet"], ["O-RUSH-9002"])
        self.assertEqual(result["risks"][0]["payload"]["kind"], "缺料")
        # 风险旗标随草案一并入账，工坊提前可见
        commands.commit_drafts(store, result["events"], occurred_at=AT)
        world = World(store.events)
        self.assertTrue(any(f["kind"] == "缺料" for f in world.risk_flags))

    def test_batch_expiring_before_need_date_not_assigned(self) -> None:
        store = build_store()
        accept_rush(store, "O-RUSH-9003", 5, "2026-10-10")  # 成品 10-05 到期
        result = reassign(
            World(store.events),
            plan_id="PLAN-0924", trigger="节前加单", at=AT,
            rush_order_ids=["O-RUSH-9003"],
        )
        self.assertEqual(result["assignments"], [])
        self.assertEqual(result["unmet"], ["O-RUSH-9003"])

    def test_near_expiry_rechannel_then_stock_unavailable(self) -> None:
        on = date(2026, 10, 4)
        suggestions = rechannel_near_expiry(self.world, on, window_days=3)
        suggested_batches = {s["batch_id"] for s in suggestions}
        # 10-05 到期的三个批次的无订单余量都应建议转渠道
        self.assertEqual(suggested_batches,
                         {"BATCH-A-0920-01", "BATCH-A-0920-02", "BATCH-B-0920-01"})

        store = build_store()
        accept_rush(store, "O-RUSH-9004", 6, "2026-10-05")
        result = reassign(
            World(store.events),
            plan_id="PLAN-0924B", trigger="临期转渠道",
            at=datetime.fromisoformat("2026-10-04T08:00:00+08:00"),
            rechannel=[{"batch_id": "BATCH-A-0920-01", "quantity_kg": 6}],
            rush_order_ids=["O-RUSH-9004"],
        )
        # 0920-01 的 6kg 已转渠道；0920-02 与 B 批各 8kg 仍可满足
        self.assertEqual(result["unmet"], [])
        self.assertTrue(all(a["batch_id"] != "BATCH-A-0920-01"
                            for a in result["assignments"]))
        commands.commit_drafts(store, result["events"], occurred_at=AT)
        world = World(store.events)
        self.assertEqual(world.batches["BATCH-A-0920-01"]["rechannel_qty"], 6)

    def test_rechannel_cannot_exceed_free_stock(self) -> None:
        with self.assertRaises(ValueError):
            reassign(
                self.world,
                plan_id="PLAN-X", trigger="临期转渠道", at=AT,
                rechannel=[{"batch_id": "BATCH-A-0920-02", "quantity_kg": 9}],  # 仅 8
            )

    def test_delivery_risk_flags_expiry_and_qualification(self) -> None:
        store = build_store()
        # 挂一张交付日晚于保质期的预留单
        emit(store, "ORDER_RESERVED", "O-LATE-9999", {
            "channel": "线下团购", "quantity_kg": 2, "needed_by": "2026-10-10",
            "reserved_batch_id": "BATCH-A-0920-01",
            "reserved_at": "2026-09-24T09:00:00+08:00", "plan_id": "PLAN-0924",
        }, "2026-09-24T09:00:00+08:00", summary="晚交付预留")
        world = World(store.events)
        risks = delivery_risks(world, date(2026, 9, 24))
        self.assertTrue(any(r["kind"] == "保质期" and r["order_id"] == "O-LATE-9999"
                            for r in risks))
        # 资质有效期至年底，9 月不报警
        self.assertFalse(any(r["kind"] == "资质到期" for r in risks))
        risks_late = delivery_risks(world, date(2026, 12, 28))
        self.assertTrue(any(r["kind"] == "资质到期" for r in risks_late))


if __name__ == "__main__":
    unittest.main()
