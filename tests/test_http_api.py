from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.launcher import RegPilotServerController
from regulation_agent.server import make_server
from regulation_agent.service import ApplicationService
from regulation_agent.model_runtime import ModelToolTurn


class HttpApiTests(unittest.TestCase):
    def test_launcher_controller_starts_local_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp_name}, clear=True):
                controller = RegPilotServerController(preferred_port=0)
                try:
                    url = controller.start()
                    index = _text("GET", f"{url}/")

                    self.assertIn("RegPilot", index)
                    self.assertEqual(os.environ["FORMFILL_GUARD_STATE_DIR"], str(Path(temp_name) / "RegPilot" / "formfill"))
                    self.assertTrue(controller.model_config_path)
                    self.assertTrue(controller.model_config_path.exists())
                finally:
                    controller.stop()

    def test_static_route_serves_nested_assets_and_blocks_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/static/assets/regpilot-logo.png", timeout=5) as response:
                    data = response.read()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/png")
                    self.assertGreater(len(data), 0)

                with self.assertRaises(HTTPError) as rejected:
                    urlopen(f"{base_url}/static/assets/%2e%2e/index.html", timeout=5)
                self.assertEqual(rejected.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_bootstrap_settings_chat_events_and_pending_fill_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                controlled_chrome_probe=FakeControlledChromeProbe([]),
                controlled_chrome_launcher=lambda: {},
                model_intent_parser=FakeIntentParser(None),
                model_chat_responder=FakeChatResponder("你好，我是 RegPilot。"),
            )
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                index = _text("GET", f"{base_url}/")
                self.assertIn("RegPilot", index)
                self.assertIn("task-context-panel", index)

                _json(
                    "PUT",
                    f"{base_url}/api/settings/provider",
                    {
                        "provider": "deepseek",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-v4-pro",
                        "api_key": "secret-provider-key",
                    },
                )
                bootstrap = _json("GET", f"{base_url}/api/bootstrap")
                bootstrap_text = json.dumps(bootstrap, ensure_ascii=False)
                self.assertEqual(bootstrap["provider"]["provider"], "deepseek")
                self.assertTrue(bootstrap["provider"]["has_api_key"])
                self.assertNotIn("secret-provider-key", bootstrap_text)

                with self.assertRaises(HTTPError) as rejected:
                    _json(
                        "PUT",
                        f"{base_url}/api/settings/provider",
                        {
                            "provider": "anthropic_compatible",
                            "base_url": "https://api.example.test",
                            "model": "claude-test",
                            "api_key": "secret-provider-key",
                        },
                    )
                self.assertEqual(rejected.exception.code, 400)
                self.assertIn("DeepSeekPro / OpenAI-compatible", rejected.exception.read().decode("utf-8"))

                self.assertEqual(bootstrap["controlled_chrome"]["label"], "未检测到填报chrome")
                self.assertEqual(service.controlled_chrome_probe.calls, ["bootstrap"])

                switch_target = _json("POST", f"{base_url}/api/sessions", {"title": "切换测试"})
                service.controlled_chrome_probe.calls.clear()
                switched = _json("GET", f"{base_url}/api/sessions/{switch_target['session_id']}")
                self.assertEqual(switched["selected_session"]["session_id"], switch_target["session_id"])
                self.assertEqual(service.controlled_chrome_probe.calls, [])

                chrome = _json("GET", f"{base_url}/api/controlled-chrome")
                self.assertEqual(chrome["controlled_chrome"]["status"], "missing")
                opened = _json("POST", f"{base_url}/api/controlled-chrome/open", {})
                self.assertTrue(opened["ok"])
                self.assertTrue(opened["launched"])
                self.assertEqual(opened["controlled_chrome"]["status"], "missing")

                chat = _json("POST", f"{base_url}/api/chat/messages", {"content": "帮我处理 E 列"})
                self.assertTrue(chat["session_id"].startswith("session_"))
                events = _text("GET", f"{base_url}/api/sessions/{chat['session_id']}/events")
                self.assertIn("event: message", events)
                self.assertIn("model_chat_completed", events)

                fill = _json(
                    "POST",
                    f"{base_url}/api/fill-tasks/shanghaiData_fill/validate",
                    {"excel_path": "D:/case/总表.xlsx", "sheet": "Sheet1", "value_column": "E"},
                )
                self.assertFalse(fill["ok"])
                self.assertEqual(fill["code"], "pending_integration")
                self.assertEqual(fill["written_count"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_session_management_routes_create_rename_select_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                created = _json("POST", f"{base_url}/api/sessions", {"title": "演示会话"})
                session_id = created["session_id"]
                renamed = _json("PUT", f"{base_url}/api/sessions/{session_id}", {"title": "上海数据演示"})
                selected = _json("GET", f"{base_url}/api/bootstrap?session_id={session_id}")
                listed = _json("GET", f"{base_url}/api/sessions")
                archived = _json("POST", f"{base_url}/api/sessions/{session_id}/archive", {})
                listed_after_archive = _json("GET", f"{base_url}/api/sessions")
                archived_list = _json("GET", f"{base_url}/api/sessions?archived=1")
                restored = _json("POST", f"{base_url}/api/sessions/{session_id}/restore", {})
                checklist = _json(
                    "PUT",
                    f"{base_url}/api/checklist",
                    {
                        "session_id": session_id,
                        "rows": [
                            {"parameter": "额定功率", "current_value": "80kW", "standard_value": "≤100kW"},
                        ],
                    },
                )
                checklist_readback = _json("GET", f"{base_url}/api/checklist?session_id={session_id}")
                deleted = _json("DELETE", f"{base_url}/api/sessions/{session_id}")

                self.assertEqual(renamed["session"]["title"], "上海数据演示")
                self.assertEqual(selected["selected_session"]["session_id"], session_id)
                self.assertEqual(listed["sessions"][0]["session_id"], session_id)
                self.assertEqual(archived["archived_session_id"], session_id)
                self.assertEqual(listed_after_archive["sessions"], [])
                self.assertEqual(archived_list["sessions"][0]["session_id"], session_id)
                self.assertEqual(restored["restored_session_id"], session_id)
                self.assertEqual(checklist["checklist"]["columns"], ["参数", "当前值", "标准值"])
                self.assertEqual(checklist_readback["checklist"]["rows"][0]["standard_value"], "≤100kW")
                self.assertEqual(deleted["deleted_session_id"], session_id)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_chat_route_triggers_formfill_harness_and_returns_bootstrap_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness(
                    {
                        "ok": True,
                        "status": "final_review",
                        "session_id": "sess_formfill",
                        "task_id": "shanghaiData_fill",
                        "recommended_next_action": "final_review",
                        "human_handoff_required": False,
                        "inputs": {
                            "task_id": "shanghaiData_fill",
                            "excel_path": r"D:\case\上海总表.xlsx",
                            "sheet": "SHGL备案参数",
                            "value_column": "E",
                            "auto_advance_policy": "until_before_final_submit",
                        },
                        "summary": {
                            "status": "final_review",
                            "step_title": "其他信息",
                            "traffic_light": {"green_count": 2, "yellow_count": 1, "red_count": 0},
                            "blocking_items": [],
                            "manual_intervention_count": 0,
                            "event_log": [],
                        },
                    }
                ),
            )
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                chat = _json(
                    "POST",
                    f"{base_url}/api/chat/messages",
                    {"content": r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台"},
                )
                events = _text("GET", f"{base_url}/api/sessions/{chat['session_id']}/events")

                self.assertEqual(chat["assistant"]["status"], "final_review")
                self.assertEqual(chat["bootstrap"]["review_summary"]["status"], "final_review")
                self.assertEqual(chat["bootstrap"]["task_context"]["target_column"], "E列")
                self.assertIn("event: formfill_tool_started", events)
                self.assertIn("event: formfill_tool_completed", events)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_stream_chat_route_sends_live_events_and_assistant_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                model_intent_parser=FakeIntentParser(None),
                model_chat_responder=FakeChatResponder("你好，流式回答。"),
            )
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                stream = _text(
                    "POST",
                    f"{base_url}/api/chat/messages/stream",
                    {"content": "你好"},
                )

                self.assertIn("event: message", stream)
                self.assertIn("event: assistant_delta", stream)
                self.assertIn('"delta": "你"', stream)
                self.assertIn("event: assistant_status", stream)
                self.assertIn("event: stream_done", stream)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_stream_chat_route_does_not_duplicate_provider_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                model_tool_client=FakeStreamingToolChatClient("真流式"),
            )
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                stream = _text(
                    "POST",
                    f"{base_url}/api/chat/messages/stream",
                    {"content": "你好"},
                )

                self.assertEqual(stream.count("event: assistant_delta"), 3)
                self.assertIn('"delta": "真"', stream)
                self.assertIn('"delta": "流"', stream)
                self.assertIn('"delta": "式"', stream)
                self.assertIn("event: assistant_status", stream)
                self.assertIn('"content": "真流式"', stream)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_cancel_chat_turn_route_accepts_client_turn_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))
            server = make_server("127.0.0.1", 0, service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                result = _json(
                    "POST",
                    f"{base_url}/api/chat/turns/cancel",
                    {"client_turn_id": "turn_http_cancel"},
                )

                self.assertTrue(result["ok"])
                self.assertTrue(result["cancelled"])
                self.assertEqual(result["client_turn_id"], "turn_http_cancel")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


def _json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _text(method: str, url: str, payload: dict | None = None) -> str:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urlopen(request, timeout=5) as response:
            return response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        raise AssertionError(f"HTTP {exc.code}: {body}") from exc


class FakeHarness:
    def __init__(self, result: dict) -> None:
        self.result = result

    def run_until_stop(self, request) -> dict:
        return self.result


class FakeIntentParser:
    def __init__(self, intent: dict | None) -> None:
        self.intent = intent

    def parse(self, content: str) -> dict | None:
        return self.intent


class FakeChatResponder:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def respond(self, content: str, *, history: list[dict[str, str]] | None = None) -> str:
        return self.reply


class FakeStreamingToolChatClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete_with_tools_stream(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        temperature: float = 0.2,
        on_event=None,
        should_cancel=None,
    ) -> ModelToolTurn:
        for character in self.reply:
            if on_event:
                on_event({"type": "content_delta", "delta": character})
        return ModelToolTurn(content=self.reply, tool_calls=[])


class FakeControlledChromeProbe:
    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries
        self.calls: list[str] = []

    def __call__(self, reason: str) -> list[dict]:
        self.calls.append(reason)
        return self.entries


if __name__ == "__main__":
    unittest.main()
