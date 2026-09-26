import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.instrument import (  # noqa: E402
    DISABLE_INSTRUMENT,
    COMPLETE_CALIBRATION,
    START_CALIBRATION,
    InstrumentService,
)
from app.store import store  # noqa: E402


class InstrumentFlowTests(unittest.TestCase):
    def setUp(self):
        self.service = InstrumentService()
        self.rows = store.rows("instrument")
        self.original_rows = copy.deepcopy(self.rows)
        self.row = {
            "id": 9001,
            "status": "在用",
            "仪器编号": "TEST-9001",
            "仪器名称": "状态流转型仪器",
            "型号规格": "TEST-MODEL",
        }
        self.rows.append(self.row)

    def tearDown(self):
        self.rows[:] = self.original_rows

    def test_full_calibration_flow_and_available_actions(self):
        entry, message, code = self.service.run_action(9001, START_CALIBRATION, expected_version=1)
        self.assertIsNone(code)
        self.assertEqual(entry["status"], "待校准")
        self.assertEqual(entry["version"], 2)
        self.assertEqual(self.service.available_actions("待校准"), [START_CALIBRATION, DISABLE_INSTRUMENT])
        self.assertIn("进入待校准", message)

        entry, _, code = self.service.run_action(9001, START_CALIBRATION, expected_version=2)
        self.assertIsNone(code)
        self.assertEqual(entry["status"], "校准中")
        self.assertEqual(entry["version"], 3)
        self.assertEqual(self.service.available_actions("校准中"), [COMPLETE_CALIBRATION, DISABLE_INSTRUMENT])

        entry, message, code = self.service.run_action(9001, COMPLETE_CALIBRATION, expected_version=2)
        self.assertIsNone(entry)
        self.assertEqual(code, "VERSION_CONFLICT")
        self.assertEqual(self.service.get_entry(9001)["status"], "校准中")
        self.assertIn("其他人更新", message)

        entry, _, code = self.service.run_action(9001, COMPLETE_CALIBRATION, expected_version=3)
        self.assertIsNone(code)
        self.assertEqual(entry["status"], "在用")
        self.assertEqual(entry["version"], 4)

        entry, _, code = self.service.run_action(9001, DISABLE_INSTRUMENT, expected_version=4)
        self.assertIsNone(code)
        self.assertEqual(entry["status"], "已停用")
        self.assertEqual(entry["available_actions"], [])

        entry, message, code = self.service.run_action(9001, DISABLE_INSTRUMENT, expected_version=5)
        self.assertIsNone(entry)
        self.assertEqual(code, "STATE_CONFLICT")
        self.assertEqual(self.service.get_entry(9001)["status"], "已停用")
        self.assertIn("当前为「已停用」", message)

        detail = self.service.get_entry(9001)
        self.assertEqual([item["action"] for item in detail["history"]], [
            "历史档案导入",
            START_CALIBRATION,
            START_CALIBRATION,
            COMPLETE_CALIBRATION,
            DISABLE_INSTRUMENT,
        ])
        self.assertEqual(detail["仪器状态"], "已停用")
        self.assertFalse(detail["pending"])
        self.assertTrue(detail["abnormal"])

    def test_completion_is_only_allowed_during_calibration(self):
        self.service.run_action(9001, START_CALIBRATION, expected_version=1)
        entry, _, code = self.service.run_action(9001, COMPLETE_CALIBRATION, expected_version=2)
        self.assertIsNone(entry)
        self.assertEqual(code, "STATE_CONFLICT")

    def test_concurrent_conflicting_actions_keep_single_result(self):
        # 两个线程进入临界区时都只持有页面加载时的 v1；串行化后第二个提交应收到版本冲突。
        other_service = InstrumentService()
        first = self.service.run_action(9001, START_CALIBRATION, expected_version=1)
        second = other_service.run_action(9001, DISABLE_INSTRUMENT, expected_version=1)
        results = [first, second]

        codes = {result[2] for result in results}
        self.assertIn(None, codes)
        self.assertIn("VERSION_CONFLICT", codes)
        final = self.service.get_entry(9001)
        self.assertIn(final["status"], {"待校准", "已停用"})
        self.assertEqual(final["version"], 2)
        self.assertEqual(len(final["history"]), 2)

    def test_concurrent_same_action_is_reported_as_duplicate(self):
        self.service._inflight.add((9001, START_CALIBRATION))
        try:
            entry, message, code = self.service.run_action(9001, START_CALIBRATION, expected_version=1)
        finally:
            self.service._inflight.discard((9001, START_CALIBRATION))
        self.assertIsNone(entry)
        self.assertEqual(code, "DUPLICATE_SUBMISSION")
        self.assertIn("请勿重复操作", message)
        self.assertEqual(self.service.get_entry(9001)["status"], "在用")

    def test_legacy_record_remains_visible_and_is_normalized_once(self):
        self.rows.append({
            "id": 9002,
            "status": "已报废",
            "仪器编号": "TEST-9002",
        })
        entry = self.service.get_entry(9002)
        self.assertEqual(entry["status"], "已报废")
        self.assertEqual(entry["仪器状态"], "已报废")
        self.assertEqual(entry["available_actions"], [])
        self.assertEqual(entry["history"][0]["action"], "历史档案导入")
        entries, total = self.service.list_entries(status="已停用", page=1, size=200)
        self.assertFalse(any(item["id"] == 9002 for item in entries))
        self.assertGreaterEqual(total, 1)
        all_entries, all_total = self.service.list_entries(page=1, size=200)
        self.assertTrue(any(item["id"] == 9002 for item in all_entries))
        self.assertGreaterEqual(all_total, total)


if __name__ == "__main__":
    unittest.main()
