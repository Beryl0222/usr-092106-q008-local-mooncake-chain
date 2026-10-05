"""隐私公开投影：扫码可见产地与制作信息，不得带出村民收入。"""
import json
import unittest

from src.privacy import public_batch_view
from src.readmodel import World
from tests.fixtures import build_store

# 公开投影绝不能出现的键名片段（英文内部字段）与敏感中文词
FORBIDDEN_KEY_HINTS = ("price", "rate", "amount", "income", "account", "pay", "supplier")
FORBIDDEN_TEXT = ("王秀兰", "李茂才", "张燕", "单价", "时薪", "账号", "收入", "SUP-S")


class PrivacyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(build_store().events)

    def test_public_view_shows_origin_and_making_info(self) -> None:
        view = public_batch_view(self.world, "BATCH-A-0920-01")
        self.assertTrue(view["found"])
        self.assertEqual(view["workshop_name"], "屯城老街手作工坊")
        self.assertEqual(view["label_allergens"], ["大豆", "小麦麸质", "芝麻", "花生"])
        ingredients = {(i["ingredient"], i["village"]) for i in view["ingredients"]}
        self.assertIn(("叉烧", "枫木村"), ingredients)
        self.assertIn(("花生仁", "屯城村"), ingredients)
        self.assertIn(("芝麻", "屯城村"), ingredients)

    def test_public_view_never_carries_income_or_supplier_identity(self) -> None:
        for bid in ("BATCH-A-0920-01", "BATCH-A-0920-02", "BATCH-B-0920-01"):
            raw = json.dumps(public_batch_view(self.world, bid), ensure_ascii=False)
            for hint in FORBIDDEN_KEY_HINTS:
                self.assertNotIn(hint, raw.lower(), f"{bid} 公开数据含敏感键 {hint}")
            for word in FORBIDDEN_TEXT:
                self.assertNotIn(word, raw, f"{bid} 公开数据含敏感内容 {word}")

    def test_blocked_batch_not_public(self) -> None:
        store = build_store()
        from src import commands
        from datetime import datetime
        commands.freeze_recall_batches(
            store, "RECALL-8001",
            at=datetime.fromisoformat("2026-09-24T10:10:00+08:00"),
        )
        world = World(store.events)
        view = public_batch_view(world, "BATCH-A-0920-02")
        self.assertFalse(view["found"])
        self.assertIn("冻结", view["reason"])


if __name__ == "__main__":
    unittest.main()
