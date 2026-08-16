from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.service import ApplicationService
from regulation_agent.server import configure_regpilot_runtime_environment
from regulation_agent.settings import ProviderSettingsStore, default_state_dir
from regulation_agent.tools import default_tool_inventory


class ProviderSettingsTests(unittest.TestCase):
    def test_default_state_dir_uses_regpilot_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp_name}, clear=True):
                self.assertEqual(default_state_dir(), Path(temp_name) / "RegPilot")

    def test_regpilot_runtime_redirects_formfill_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp_name}, clear=True):
                expected = Path(temp_name) / "RegPilot" / "formfill"

                actual = configure_regpilot_runtime_environment()

                self.assertEqual(actual, expected)
                self.assertEqual(os.environ["FORMFILL_GUARD_STATE_DIR"], str(expected))

    def test_regpilot_runtime_preserves_explicit_formfill_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            explicit = Path(temp_name) / "custom-formfill"
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": temp_name, "FORMFILL_GUARD_STATE_DIR": str(explicit)},
                clear=True,
            ):
                actual = configure_regpilot_runtime_environment()

                self.assertEqual(actual, explicit)
                self.assertEqual(os.environ["FORMFILL_GUARD_STATE_DIR"], str(explicit))

    def test_provider_settings_default_to_deepseek_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            store = ProviderSettingsStore(Path(temp_name))

            public = store.public_view()
            loaded = store.load()

            self.assertEqual(public["provider"], "deepseek")
            self.assertEqual(public["base_url"], "https://api.deepseek.com")
            self.assertEqual(public["model"], "deepseek-v4-pro")
            self.assertFalse(public["has_api_key"])
            self.assertEqual(loaded["api_key"], "")
            self.assertNotIn("api_key", public)

    def test_provider_settings_public_view_masks_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            store = ProviderSettingsStore(Path(temp_name))

            store.save(
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.test/v1",
                    "model": "gpt-test",
                    "api_key": "test-secret-current-001",
                }
            )

            public = store.public_view()
            public_text = json.dumps(public, ensure_ascii=False)

            self.assertEqual(public["provider"], "openai_compatible")
            self.assertEqual(public["base_url"], "https://api.example.test/v1")
            self.assertEqual(public["model"], "gpt-test")
            self.assertTrue(public["has_api_key"])
            self.assertNotIn("test-secret-current-001", public_text)
            self.assertNotIn("api_key", public)
            self.assertIn("api_key_masked", public)

    def test_provider_settings_blank_api_key_preserves_existing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            store = ProviderSettingsStore(Path(temp_name))

            first = store.save(
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.test/v1",
                    "model": "gpt-test",
                    "api_key": "test-secret-original-001",
                }
            )
            second = store.save(
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.test/v2",
                    "model": "gpt-next",
                    "api_key": "",
                }
            )

            stored = store.load()
            self.assertEqual(stored["api_key"], "test-secret-original-001")
            self.assertEqual(second["api_key_masked"], first["api_key_masked"])
            self.assertNotIn("test-secret-original-001", json.dumps(second, ensure_ascii=False))

    def test_provider_settings_preserve_deepseek_options_without_exposing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            store = ProviderSettingsStore(Path(temp_name))

            public = store.save(
                {
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-pro",
                    "api_key": "test-secret-provider-001",
                    "request_timeout_seconds": "660",
                    "deepseek_thinking": "enabled",
                    "deepseek_reasoning_effort": "max",
                    "deepseek_max_tokens": "65536",
                    "deepseek_stream_include_usage": True,
                    "deepseek_user_id": "regpilot-local",
                    "deepseek_strict_tool_schema": True,
                    "deepseek_retry_max_attempts": "4",
                    "deepseek_retry_backoff_seconds": "0",
                    "deepseek_json_empty_retry_attempts": "2",
                }
            )

            stored = store.load()
            public_text = json.dumps(public, ensure_ascii=False)

            self.assertEqual(stored["api_key"], "test-secret-provider-001")
            self.assertEqual(public["request_timeout_seconds"], "660")
            self.assertEqual(public["deepseek_thinking"], "enabled")
            self.assertEqual(public["deepseek_reasoning_effort"], "max")
            self.assertEqual(public["deepseek_max_tokens"], "65536")
            self.assertTrue(public["deepseek_stream_include_usage"])
            self.assertEqual(public["deepseek_user_id"], "regpilot-local")
            self.assertTrue(public["deepseek_strict_tool_schema"])
            self.assertNotIn("test-secret-provider-001", public_text)


class ApplicationContractTests(unittest.TestCase):
    def test_bootstrap_reports_contract_driven_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                controlled_chrome_probe=FakeControlledChromeProbe([]),
            )

            bootstrap = service.bootstrap()

            self.assertEqual(bootstrap["app"]["name"], "RegPilot")
            self.assertEqual(bootstrap["app"]["status"], "running")
            self.assertEqual(bootstrap["task_context"]["status"], "pending_integration")
            self.assertEqual(bootstrap["execution_status"]["chrome"]["status"], "missing")
            self.assertEqual(bootstrap["controlled_chrome"]["label"], "未检测到填报chrome")
            self.assertEqual(bootstrap["review_summary"]["status"], "idle")
            self.assertNotIn("mcp", bootstrap)
            self.assertIn(bootstrap["tool_readiness"]["status"], {"available", "partial"})
            self.assertGreaterEqual(bootstrap["tool_readiness"]["available_count"], 1)
            self.assertGreaterEqual(len(bootstrap["tools"]), 1)
            self.assertIn("human_action.request_operator_choice", {tool["name"] for tool in bootstrap["tools"]})
            self.assertIn("formfill.shanghai_data", {skill["id"] for skill in bootstrap["skills"]})
            self.assertEqual(bootstrap["checklist"]["columns"], ["参数", "当前值", "标准值"])
            self.assertEqual(bootstrap["checklist"]["rows"], [])
            self.assertEqual(bootstrap["sessions"], [])

    def test_bootstrap_exposes_operator_skills_separately_from_regulatory_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))

            bootstrap = service.bootstrap()
            skill = next(item for item in bootstrap["skills"] if item["id"] == "formfill.shanghai_data")

            self.assertEqual(skill["title"], "上海数据平台填报")
            self.assertEqual(skill["status"], "available")
            self.assertNotIn("operation_nodes", skill)
            self.assertNotIn("allowed_tools", skill)
            self.assertIn("human_action.request_operator_choice", {tool["name"] for tool in bootstrap["tools"]})

    def test_bootstrap_sniffs_controlled_chrome_with_two_state_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            probe = FakeControlledChromeProbe(
                [
                    {
                        "key": "shanghaiData_fill",
                        "title": "上海数据平台填报",
                        "debug_port": 9333,
                        "last_url": "https://data.example.test/form",
                    }
                ]
            )
            service = ApplicationService(state_dir=Path(temp_name), controlled_chrome_probe=probe)

            bootstrap = service.bootstrap()

            self.assertEqual(probe.calls, ["bootstrap"])
            self.assertEqual(bootstrap["controlled_chrome"]["status"], "connected")
            self.assertEqual(bootstrap["controlled_chrome"]["label"], "填报chrome已连接")
            self.assertEqual(bootstrap["execution_status"]["chrome"]["value"], "填报chrome已连接")

    def test_controlled_chrome_missing_state_uses_operator_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                controlled_chrome_probe=FakeControlledChromeProbe([]),
            )

            status = service.controlled_chrome_status()

            self.assertEqual(status["status"], "missing")
            self.assertEqual(status["label"], "未检测到填报chrome")
            self.assertEqual(status["active"], [])

    def test_open_controlled_chrome_does_not_launch_when_already_connected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            probe = FakeControlledChromeProbe(
                [
                    {
                        "key": "scaffold_chrome",
                        "task_id": "shanghaiData_fill",
                        "title": "上海数据平台填报",
                        "debug_port": 9333,
                    }
                ]
            )

            def launcher() -> dict:
                raise AssertionError("launcher should not be called when fill Chrome is already connected")

            service = ApplicationService(
                state_dir=Path(temp_name),
                controlled_chrome_probe=probe,
                controlled_chrome_launcher=launcher,
            )

            result = service.open_controlled_chrome()

            self.assertTrue(result["ok"])
            self.assertFalse(result["launched"])
            self.assertEqual(result["controlled_chrome"]["status"], "connected")
            self.assertEqual(probe.calls, ["open_check"])

    def test_open_controlled_chrome_launches_and_refreshes_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            probe = FakeControlledChromeProbe([])
            launcher_calls = []

            def launcher() -> dict:
                launcher_calls.append("launch")
                probe.entries = [
                    {
                        "key": "scaffold_chrome",
                        "task_id": "shanghaiData_fill",
                        "title": "上海数据平台填报",
                        "debug_port": 9444,
                    }
                ]
                return {"ok": True, "pid": 123, "remote_debugging_port": 9444, "browser_key": "scaffold_chrome"}

            service = ApplicationService(
                state_dir=Path(temp_name),
                controlled_chrome_probe=probe,
                controlled_chrome_launcher=launcher,
            )

            result = service.open_controlled_chrome()

            self.assertTrue(result["ok"])
            self.assertTrue(result["launched"])
            self.assertEqual(result["launch"]["remote_debugging_port"], 9444)
            self.assertEqual(result["controlled_chrome"]["status"], "connected")
            self.assertEqual(launcher_calls, ["launch"])
            self.assertEqual(probe.calls, ["open_check", "open_after_launch"])

    def test_session_view_switches_context_without_chrome_sniff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            probe = FakeControlledChromeProbe([])
            service = ApplicationService(state_dir=Path(temp_name), controlled_chrome_probe=probe)
            first = service.create_session(title="法规解读")
            second = service.create_session(title="填报任务")
            service.bootstrap(first["session_id"])
            probe.calls.clear()

            view = service.session_view(second["session_id"])

            self.assertEqual(probe.calls, [])
            self.assertEqual(view["selected_session"]["session_id"], second["session_id"])
            self.assertEqual(view["sessions"][0]["session_id"], second["session_id"])
            self.assertEqual(view["review_summary"]["status"], "idle")

    def test_session_checklist_can_be_updated_for_future_regulation_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            session = service.create_session(title="Checklist 占位")

            updated = service.update_checklist(
                session["session_id"],
                {
                    "rows": [
                        {"parameter": "额定电压", "current_value": "350V", "standard_value": "≤400V"},
                    ]
                },
            )
            bootstrap = service.bootstrap(session["session_id"])

            self.assertEqual(updated["checklist"]["rows"][0]["parameter"], "额定电压")
            self.assertEqual(bootstrap["checklist"]["rows"][0]["current_value"], "350V")

    def test_chat_sessions_can_be_created_renamed_deleted_and_bootstrapped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))

            first = service.create_session(title="法规解读测试")
            second = service.create_session(title="上海数据填报测试")
            renamed = service.rename_session(first["session_id"], "法规解读会话")
            selected = service.bootstrap(first["session_id"])
            deleted = service.delete_session(second["session_id"])

            self.assertEqual(renamed["session"]["title"], "法规解读会话")
            self.assertEqual(selected["selected_session"]["session_id"], first["session_id"])
            self.assertEqual(selected["selected_session"]["title"], "法规解读会话")
            self.assertEqual(deleted["active_session_id"], first["session_id"])
            self.assertEqual(len(service.list_sessions()["sessions"]), 1)

    def test_chat_sessions_persist_events_and_context_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            state_dir = Path(temp_name)
            service = ApplicationService(state_dir=state_dir)

            created = service.create_session(title="持久会话")
            service.post_user_message("你好，记住这个会话上下文", created["session_id"])

            reloaded = ApplicationService(state_dir=state_dir)
            bootstrap = reloaded.bootstrap(created["session_id"])
            events = reloaded.session_events(created["session_id"])

            self.assertEqual(bootstrap["selected_session"]["title"], "持久会话")
            self.assertIn("message", [event["type"] for event in events])
            self.assertGreaterEqual(bootstrap["selected_session"]["message_count"], 2)

    def test_sessions_can_be_archived_restored_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))

            first = service.create_session(title="活动会话")
            second = service.create_session(title="可归档会话")
            archived = service.archive_session(second["session_id"])
            active_list = service.list_sessions()["sessions"]
            archived_list = service.list_sessions(archived=True)["sessions"]
            restored = service.restore_session(second["session_id"])

            self.assertEqual(archived["archived_session_id"], second["session_id"])
            self.assertEqual(archived["active_session_id"], first["session_id"])
            self.assertEqual([item["session_id"] for item in active_list], [first["session_id"]])
            self.assertEqual([item["session_id"] for item in archived_list], [second["session_id"]])
            self.assertEqual(restored["active_session_id"], second["session_id"])
            self.assertEqual(service.delete_session(second["session_id"])["deleted_session_id"], second["session_id"])

    def test_session_history_uses_compressed_context_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            session = service.create_session(title="长上下文")
            session_id = session["session_id"]

            for index in range(30):
                service._append_event(session_id, "message", {"role": "user", "content": f"较早消息 {index}"})

            history = service._session_history_for_model(session_id, "继续")
            public = service.bootstrap(session_id)["selected_session"]

            self.assertEqual(history[0]["role"], "system")
            self.assertIn("较早上下文摘要", history[0]["content"])
            self.assertTrue(public["has_context_summary"])
            self.assertLessEqual(public["recent_message_count"], 24)

    def test_fill_tools_are_available_when_task_inventory_is_available(self) -> None:
        tools = default_tool_inventory(lambda: [FakeFillTask()])
        fill_tools = [tool for tool in tools if tool["name"] == "fill.shanghaiData_fill"]

        self.assertEqual(len(fill_tools), 1)
        self.assertEqual(fill_tools[0]["status"], "available")

    def test_saving_provider_settings_invalidates_cached_model_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            service.model_intent_parser = object()

            service.save_provider_settings(
                {
                    "provider": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-pro",
                    "api_key": "test-secret-updated-001",
                }
            )

            self.assertIsNone(service.model_intent_parser)

    def test_fill_task_actions_return_pending_integration_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))

            result = service.validate_fill_task_current_step(
                "shanghaiData_fill",
                {"excel_path": "D:/case/总表.xlsx", "sheet": "Sheet1", "value_column": "E"},
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "pending_integration")
            self.assertEqual(result["status"], "pending_integration")
            self.assertFalse(result["can_fill"])
            self.assertEqual(result["written_count"], 0)

    def test_formfill_instruction_sniffs_chrome_before_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            probe = FakeControlledChromeProbe([])
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True, "status": "final_review"}),
                controlled_chrome_probe=probe,
            )

            service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

            self.assertGreaterEqual(len(probe.calls), 1)
            self.assertEqual(probe.calls[0], "before_fill_tool")

    def test_human_action_request_is_audited_and_resolved_by_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            session = service.create_session(operator_label="tester")

            action = service.request_human_action(
                session_id=session["session_id"],
                prompt="检测到红灯项，需要人工选择下一步。",
                options=[
                    {"id": "inspect_reason", "label": "查看原因"},
                    {"id": "continue_validation", "label": "继续验证"},
                ],
                risk_level="high",
                related_tool_run_id="run-red-1",
            )
            response = service.respond_to_human_action(action["action_id"], "inspect_reason")
            events = service.session_events(session["session_id"])

            self.assertEqual(action["type"], "human_action")
            self.assertEqual(action["status"], "pending")
            self.assertEqual(response["status"], "resolved")
            self.assertEqual(response["selected_option_id"], "inspect_reason")
            self.assertIn("human_action_requested", [event["type"] for event in events])
            self.assertIn("human_action_resolved", [event["type"] for event in events])


if __name__ == "__main__":
    unittest.main()


class FakeFillTask:
    id = "shanghaiData_fill"
    title = "上海数据平台填报"
    description = "上海数据平台当前页验证和填写。"


class FakeControlledChromeProbe:
    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries
        self.calls: list[str] = []

    def __call__(self, reason: str) -> list[dict]:
        self.calls.append(reason)
        return self.entries


class FakeHarness:
    def __init__(self, result: dict) -> None:
        self.result = result

    def run_until_stop(self, request) -> dict:
        return self.result
