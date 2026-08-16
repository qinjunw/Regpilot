from __future__ import annotations

import unittest

from regulation_agent.formfill_bridge import DemoFillHarness, HarnessRequest


class PortfolioBoundaryTests(unittest.TestCase):
    def test_demo_fill_harness_stops_before_submission(self) -> None:
        result = DemoFillHarness().run_until_stop(
            HarnessRequest(
                user_message="演示填报",
                task_id="landmark_fill",
                workbook_path="demo.xlsx",
                sheet="Sheet1",
                value_column="C",
            )
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["demo_mode"])
        self.assertEqual(result["status"], "final_review")
        self.assertEqual(result["recommended_next_action"], "final_review")
        self.assertIn("没有读取或写入真实工作簿", result["message"])


if __name__ == "__main__":
    unittest.main()
