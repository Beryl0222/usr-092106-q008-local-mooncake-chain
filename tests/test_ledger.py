"""账本不可变与幂等约束。"""
import json
import tempfile
import unittest
from pathlib import Path

from src.commands import assert_event_sequence_integrity
from src.ledger import EventStore, LedgerError
from tests.fixtures import build_store


class LedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = build_store()

    def test_full_fixture_passes_integrity(self) -> None:
        assert_event_sequence_integrity(self.store.events)
        self.assertGreater(len(self.store.events), 25)

    def test_version_must_be_consecutive_per_aggregate(self) -> None:
        event = {
            "event_id": "dup-version",
            "event_type": "SUPPLIER_REGISTERED",
            "aggregate_type": "supplier",
            "aggregate_id": "SUP-S01",  # 夹具中已有 v1
            "occurred_at": "2026-09-02T09:00:00+08:00",
            "version": 3,
            "summary": "跳版本",
            "payload": {"name": "X", "village": "Y", "supplies": ["叉烧"]},
        }
        with self.assertRaises(LedgerError):
            self.store.append(event)

    def test_event_id_unique(self) -> None:
        again = dict(self.store.events[0])
        with self.assertRaises(LedgerError):
            self.store.append(again)

    def test_idempotency_key_blocks_duplicate_offline_entry(self) -> None:
        # 同一张线下称重单第二次补录必须被拒绝
        event = {
            "event_id": "WEIGH-5001-again",
            "idempotency_key": "WEIGH-5001",
            "event_type": "WEIGH_IN_RECORDED",
            "aggregate_type": "worker_settlement",
            "aggregate_id": "SETTLE-LEDGER",
            "occurred_at": "2026-09-23T18:00:00+08:00",
            "version": len(self.store.for_aggregate("SETTLE-LEDGER")) + 1,
            "summary": "重复称重",
            "payload": {
                "weigh_no": "WEIGH-5001", "supplier_id": "SUP-S01",
                "lot_id": "LOT-PN-0915", "quantity_kg": 10, "unit_price": 18.0,
                "weighed_at": "2026-09-23T18:00:00+08:00",
            },
        }
        with self.assertRaises(LedgerError):
            self.store.append(event)

    def test_no_mutation_api_and_jsonl_roundtrip(self) -> None:
        self.assertFalse(hasattr(self.store, "delete"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            self.store.save_jsonl(path)
            reloaded = EventStore.load_jsonl(path)
            self.assertEqual(len(reloaded.events), len(self.store.events))
            assert_event_sequence_integrity(reloaded.events)
            # 文件每行都是合法 JSON
            for line in path.read_text("utf-8").splitlines():
                self.assertIn("event_id", json.loads(line))


if __name__ == "__main__":
    unittest.main()
