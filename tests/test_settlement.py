"""结算：金额自动汇总，线下称重与工时补录全局只付一次。"""
import unittest
from datetime import datetime

from src import commands
from src.ledger import LedgerError
from src.readmodel import World
from src.settlement import (
    SettlementError,
    attendance_ref,
    build_payment,
    paid_source_refs,
    unpaid_for,
)
from tests.fixtures import build_store

PAID_AT = datetime.fromisoformat("2026-09-25T09:00:00+08:00")


class SettlementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = build_store()
        self.world = World(self.store.events)

    def test_unpaid_listing_and_amounts(self) -> None:
        # 叉烧称重 14kg × 40 = 560；花生仁 WEIGH-5001 已在夹具中支付
        self.assertEqual(unpaid_for(self.world, "SUP-S02"), {"WEIGH-5002": 560.0})
        self.assertEqual(unpaid_for(self.world, "SUP-S01"), {})
        # 记工 8h × 25 = 200；补录 3h × 20 = 60
        self.assertEqual(
            unpaid_for(self.world, "WKR-01"),
            {attendance_ref("WKR-01", "SHIFT-A-0920"): 200.0},
        )
        self.assertEqual(unpaid_for(self.world, "WKR-02"), {"SUPP-6001": 60.0})

    def test_payment_amount_is_computed_not_caller_supplied(self) -> None:
        draft = build_payment(
            self.world, payment_id="PAY-7002", payee_id="SUP-S02",
            source_refs=["WEIGH-5002"], paid_at=PAID_AT,
        )
        self.assertEqual(draft["payload"]["amount"], 560.0)
        commands.settle(self.store, draft)
        self.assertIn("WEIGH-5002", paid_source_refs(World(self.store.events)))

    def test_source_ref_can_only_be_paid_once(self) -> None:
        draft = build_payment(
            self.world, payment_id="PAY-7002", payee_id="SUP-S02",
            source_refs=["WEIGH-5002"], paid_at=PAID_AT,
        )
        commands.settle(self.store, draft)
        # 同一张称重单再付一次：领域层拦截
        with self.assertRaises(SettlementError):
            build_payment(
                World(self.store.events), payment_id="PAY-7003",
                payee_id="SUP-S02", source_refs=["WEIGH-5002"], paid_at=PAID_AT,
            )
        # 夹具中已付的 WEIGH-5001 同样不可再付
        with self.assertRaises(SettlementError):
            build_payment(
                World(self.store.events), payment_id="PAY-7004",
                payee_id="SUP-S01", source_refs=["WEIGH-5001"], paid_at=PAID_AT,
            )

    def test_supplement_and_attendance_settle_once(self) -> None:
        for payment_id, payee, ref in (
            ("PAY-7101", "WKR-01", attendance_ref("WKR-01", "SHIFT-A-0920")),
            ("PAY-7102", "WKR-02", "SUPP-6001"),
        ):
            commands.settle(self.store, build_payment(
                World(self.store.events), payment_id=payment_id, payee_id=payee,
                source_refs=[ref], paid_at=PAID_AT,
            ))
        world = World(self.store.events)
        self.assertEqual(unpaid_for(world, "WKR-01"), {})
        self.assertEqual(unpaid_for(world, "WKR-02"), {})

    def test_payee_must_own_the_source(self) -> None:
        with self.assertRaises(SettlementError):
            build_payment(
                self.world, payment_id="PAY-7201", payee_id="SUP-S01",
                source_refs=["WEIGH-5002"], paid_at=PAID_AT,  # 属于 SUP-S02
            )

    def test_duplicate_offline_entry_blocked_at_ledger(self) -> None:
        # 即使绕过结算构造，账本的幂等键也拒绝同号补录
        version = len(self.store.for_aggregate("SETTLE-LEDGER")) + 1
        duplicate = {
            "event_id": f"SETTLE-LEDGER#{version}",
            "idempotency_key": "SUPP-6001",
            "event_type": "WORK_HOURS_SUPPLEMENTED",
            "aggregate_type": "worker_settlement",
            "aggregate_id": "SETTLE-LEDGER",
            "occurred_at": "2026-09-25T10:00:00+08:00",
            "version": version,
            "summary": "同号补录第二次",
            "payload": {
                "worker_id": "WKR-02", "shift_id": "SHIFT-A-0920",
                "hours": 3, "hourly_rate": 20.0, "supplement_no": "SUPP-6001",
                "supplemented_at": "2026-09-25T10:00:00+08:00",
            },
        }
        with self.assertRaises(LedgerError):
            self.store.append(duplicate)


if __name__ == "__main__":
    unittest.main()
