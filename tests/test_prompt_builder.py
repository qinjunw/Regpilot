from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.prompt_builder import build_system_prompt
from regulation_agent.service import ApplicationService


class PromptBuilderTests(unittest.TestCase):
    def test_chat_prompt_uses_shared_identity_without_tool_loop_rules(self) -> None:
        prompt = build_system_prompt("chat")

        self.assertIn("市场合规领航员", prompt)
        self.assertIn("你是 RegPilot，一个本地汽车法规助手。", prompt)
        self.assertIn("不要把“本地汽车法规助手”作为对外身份名称", prompt)
        self.assertIn("不要使用 Markdown 表格", prompt)
        self.assertNotIn("function tools", prompt)
        self.assertNotIn("regpilot_generate_interpretation_report", prompt)

    def test_tool_loop_prompt_extends_shared_identity_with_tool_rules(self) -> None:
        prompt = build_system_prompt("tool_loop")

        self.assertIn("市场合规领航员", prompt)
        self.assertIn("你是 RegPilot，一个本地汽车法规助手。", prompt)
        self.assertIn("function tools", prompt)
        self.assertIn("regpilot_generate_interpretation_report", prompt)
        self.assertIn("不能作为聊天正文流式输出", prompt)
        self.assertIn("工具完成后只用简短中文说明", prompt)
        self.assertIn("本机路径必须逐字符复制", prompt)
        self.assertIn("连续连字符", prompt)

    def test_application_service_uses_built_tool_loop_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=None)

            messages = service._model_tool_messages("missing-session", "你有什么 skill?")

        system_prompt = messages[0]["content"]
        self.assertIn("市场合规领航员", system_prompt)
        self.assertIn("regpilot_load_skill", system_prompt)
        self.assertIn("不要把“本地汽车法规助手”作为对外身份名称", system_prompt)


if __name__ == "__main__":
    unittest.main()
