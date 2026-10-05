import json
import unittest
from pathlib import Path

from src.validator import validate_event

SAMPLE = Path(__file__).parents[1] / "data" / "sample.json"


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_unknown_event_type_rejected(self) -> None:
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
        sample["event_type"] = "FLY_TO_MOON"
        self.assertIn("未知事件类型：FLY_TO_MOON", validate_event(sample))

    def test_payload_fields_checked(self) -> None:
        record = {
            "event_id": "e1",
            "event_type": "LOT_ACCEPTED",
            "aggregate_type": "ingredient_lot",
            "aggregate_id": "lot-x",
            "occurred_at": "2026-09-20T15:00:00+08:00",
            "version": 1,
            "summary": "缺字段的进场记录",
        }
        errors = validate_event(record)
        self.assertIn("缺少字段：allergens", errors)
        self.assertIn("缺少字段：supplier_id", errors)

    def test_aggregate_type_must_match_event(self) -> None:
        sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
        sample["aggregate_type"] = "sales_order"
        self.assertTrue(
            any("aggregate_type" in error for error in validate_event(sample))
        )


if __name__ == "__main__":
    unittest.main()
