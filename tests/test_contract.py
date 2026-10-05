"""契约层：样例、校验器与 JSON Schema 必须三方一致。"""
import json
import unittest
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

from src.validator import (
    AGGREGATE_TYPES,
    EVENT_TYPES,
    _PAYLOAD_RULES,
    validate_event,
)

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "contracts" / "domain.schema.json").read_text("utf-8"))


class ContractTest(unittest.TestCase):
    @unittest.skipIf(jsonschema is None, "未安装 jsonschema 时跳过正式 Schema 校验")
    def test_sample_and_full_chain_match_json_schema(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        jsonschema.validate(sample, SCHEMA)
        for line in (ROOT / "data" / "sample_events.jsonl").read_text("utf-8").splitlines():
            jsonschema.validate(json.loads(line), SCHEMA)

    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_schema_and_validator_share_event_enums(self) -> None:
        schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text("utf-8"))
        schema_events = set(schema["properties"]["event_type"]["enum"])
        self.assertEqual(schema_events, EVENT_TYPES)
        schema_aggs = set(schema["properties"]["aggregate_type"]["enum"])
        self.assertEqual(schema_aggs, AGGREGATE_TYPES)

    def test_schema_if_then_matches_validator_aggregate_rules(self) -> None:
        schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text("utf-8"))
        mapping = {}
        for clause in schema["allOf"]:
            cond = clause["if"]["properties"]["event_type"]["const"]
            agg = clause["then"]["properties"]["aggregate_type"]["const"]
            mapping[cond] = agg
        self.assertEqual(set(mapping), set(_PAYLOAD_RULES.keys()))
        for event_type, (aggregate, _required, _rules) in _PAYLOAD_RULES.items():
            self.assertEqual(mapping[event_type], aggregate, event_type)

    def test_missing_envelope_fields_reported(self) -> None:
        errors = validate_event({"event_id": "x"})
        self.assertTrue(any("event_type" in e for e in errors))

    def test_bad_payload_and_aggregate_mismatch_rejected(self) -> None:
        event = {
            "event_id": "bad-1", "event_type": "BATCH_PRODUCED",
            "aggregate_type": "sales_order", "aggregate_id": "B1",
            "occurred_at": "2026-09-20T11:00:00+08:00", "version": 1,
            "summary": "错聚合且缺载荷", "payload": {},
        }
        errors = validate_event(event)
        self.assertTrue(any("aggregate_type 必须是" in e for e in errors))
        self.assertTrue(any("payload 缺少字段" in e for e in errors))

    def test_weigh_in_requires_idempotency_key(self) -> None:
        event = {
            "event_id": "w-1", "event_type": "WEIGH_IN_RECORDED",
            "aggregate_type": "worker_settlement", "aggregate_id": "W",
            "occurred_at": "2026-09-23T17:00:00+08:00", "version": 1,
            "summary": "无幂等键",
            "payload": {
                "weigh_no": "W-1", "supplier_id": "S1", "lot_id": "L1",
                "quantity_kg": 5, "unit_price": 18,
                "weighed_at": "2026-09-23T17:00:00+08:00",
            },
        }
        self.assertTrue(any("idempotency_key" in e for e in validate_event(event)))

    def test_settlement_rejects_duplicate_refs(self) -> None:
        event = {
            "event_id": "p-1", "idempotency_key": "P-1",
            "event_type": "SETTLEMENT_PAID",
            "aggregate_type": "worker_settlement", "aggregate_id": "P-1",
            "occurred_at": "2026-09-24T09:00:00+08:00", "version": 1,
            "summary": "重复来源",
            "payload": {
                "payee_id": "S1", "amount": 10,
                "source_refs": ["W-1", "W-1"],
                "paid_at": "2026-09-24T09:00:00+08:00",
            },
        }
        self.assertTrue(any("source_refs" in e for e in validate_event(event)))


if __name__ == "__main__":
    unittest.main()
