from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from urllib.request import Request

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.model_runtime import (
    AgentTurnDecisionParser,
    DeepSeekOptions,
    ModelToolCall,
    ModelIntentParser,
    ModelChatResponder,
    ModelRuntimeError,
    OpenAICompatibleChatClient,
    ProviderConfig,
    default_config_path,
    load_provider_config,
)
from regulation_agent.prompt_builder import build_system_prompt
from regulation_agent.service import _assistant_tool_call_message


class ModelRuntimeTests(unittest.TestCase):
    def test_default_config_path_stays_inside_public_repository(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]

        self.assertEqual(default_config_path(), repository_root / "config" / "model.default.toml")

    def test_load_provider_config_supports_codex_style_deepseek_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "config.toml"
            path.write_text(
                """
api_key = "top-level-key"
provider = "deepseek"
default_text_model = "deepseek-v4-pro"

[providers.deepseek]
api_key = "provider-key"
""".strip(),
                encoding="utf-8",
            )

            config = load_provider_config(path)

            self.assertEqual(config.provider, "deepseek")
            self.assertEqual(config.model, "deepseek-v4-pro")
            self.assertEqual(config.api_key, "provider-key")
            self.assertEqual(config.base_url, "https://api.deepseek.com")
            self.assertEqual(config.timeout, 180.0)
            self.assertEqual(config.public_view()["api_key_masked"], "pro...-key")
            self.assertNotIn("provider-key", json.dumps(config.public_view(), ensure_ascii=False))

    def test_load_provider_config_allows_provider_timeout_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "config.toml"
            path.write_text(
                """
provider = "deepseek"
default_text_model = "deepseek-v4-pro"

[providers.deepseek]
api_key = "provider-key"
request_timeout_seconds = 300
""".strip(),
                encoding="utf-8",
            )

            config = load_provider_config(path)

            self.assertEqual(config.timeout, 300.0)

    def test_load_provider_config_reads_deepseek_specialized_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "config.toml"
            path.write_text(
                """
provider = "deepseek"
default_text_model = "deepseek-v4-pro"

[providers.deepseek]
api_key = "provider-key"
request_timeout_seconds = 660
thinking = "disabled"
reasoning_effort = "max"
max_tokens = 65536
stream_include_usage = false
user_id = "regpilot-local"
strict_tool_schema = true
retry_max_attempts = 4
retry_backoff_seconds = 0
json_empty_retry_attempts = 2
""".strip(),
                encoding="utf-8",
            )

            config = load_provider_config(path)

            self.assertEqual(config.timeout, 660.0)
            self.assertEqual(config.deepseek.thinking, "disabled")
            self.assertEqual(config.deepseek.reasoning_effort, "max")
            self.assertEqual(config.deepseek.max_tokens, 65536)
            self.assertFalse(config.deepseek.stream_include_usage)
            self.assertEqual(config.deepseek.user_id, "regpilot-local")
            self.assertTrue(config.deepseek.strict_tool_schema)
            self.assertEqual(config.deepseek.retry_max_attempts, 4)
            self.assertEqual(config.deepseek.retry_backoff_seconds, 0)
            self.assertEqual(config.deepseek.json_empty_retry_attempts, 2)

    def test_load_provider_config_rejects_unsupported_override_provider(self) -> None:
        with self.assertRaises(ModelRuntimeError) as raised:
            load_provider_config(
                overrides={
                    "provider": "anthropic_compatible",
                    "base_url": "https://api.example.test",
                    "model": "claude-test",
                    "api_key": "secret-key",
                }
            )

        self.assertEqual(raised.exception.code, "model_provider_unsupported")

    def test_openai_compatible_client_posts_chat_completion_and_extracts_json(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["url"] = request.full_url
            seen["headers"] = dict(request.header_items())
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"action": "formfill_run_until_stop", "task_id": "shanghaiData_fill"},
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
            )

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
            ),
            transport=transport,
        )

        payload = client.complete_json([{"role": "user", "content": "上海数据平台"}])

        self.assertEqual(payload["task_id"], "shanghaiData_fill")
        self.assertEqual(seen["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(seen["payload"]["model"], "deepseek-v4-pro")
        self.assertEqual(seen["payload"]["response_format"], {"type": "json_object"})
        self.assertIn("Bearer secret-key", seen["headers"]["Authorization"])

    def test_openai_compatible_client_posts_tool_catalog_and_parses_tool_calls(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "regpilot_list_skills",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            )

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
            ),
            transport=transport,
        )

        turn = client.complete_with_tools(
            [{"role": "user", "content": "你有什么 skill?"}],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "regpilot_list_skills",
                        "description": "List RegPilot skills.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )

        self.assertEqual(seen["payload"]["tool_choice"], "auto")
        self.assertEqual(seen["payload"]["tools"][0]["function"]["name"], "regpilot_list_skills")
        self.assertEqual(turn.content, "")
        self.assertEqual(turn.tool_calls, [ModelToolCall("call_1", "regpilot_list_skills", {}, "{}")])

    def test_openai_compatible_client_streams_content_and_tool_call_deltas(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["timeout"] = timeout
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeStreamResponse(
                [
                    {"choices": [{"delta": {"content": "正在"}}]},
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": "regpilot_list_skills", "arguments": "{"},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "处理",
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"arguments": "}"},
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                ]
            )

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
                timeout=180,
            ),
            transport=transport,
        )
        events: list[Any] = []

        turn = client.complete_with_tools_stream(
            [{"role": "user", "content": "你有什么 skill?"}],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "regpilot_list_skills",
                        "description": "List RegPilot skills.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            on_event=events.append,
        )

        self.assertTrue(seen["payload"]["stream"])
        self.assertEqual(seen["timeout"], 180)
        self.assertEqual(turn.content, "正在处理")
        self.assertEqual(turn.tool_calls, [ModelToolCall("call_1", "regpilot_list_skills", {}, "{}")])
        self.assertEqual([getattr(event, "delta", "") for event in events], ["正在", "处理"])

    def test_deepseek_streaming_defaults_enable_thinking_usage_and_omit_temperature(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeStreamResponse(
                [
                    {"choices": [{"delta": {"reasoning_content": "先判断是否要调用工具。"}}]},
                    {"choices": [{"delta": {"content": "正在处理"}}]},
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 100,
                            "prompt_cache_hit_tokens": 80,
                            "prompt_cache_miss_tokens": 20,
                            "completion_tokens_details": {"reasoning_tokens": 12},
                        },
                    },
                ]
            )

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
            ),
            transport=transport,
        )
        events: list[Any] = []

        turn = client.complete_with_tools_stream(
            [{"role": "user", "content": "你好"}],
            [{"type": "function", "function": {"name": "regpilot_list_skills", "parameters": {"type": "object", "properties": {}}}}],
            on_event=events.append,
        )

        self.assertEqual(seen["payload"]["thinking"], {"type": "enabled"})
        self.assertEqual(seen["payload"]["reasoning_effort"], "high")
        self.assertEqual(seen["payload"]["stream_options"], {"include_usage": True})
        self.assertNotIn("temperature", seen["payload"])
        self.assertEqual(turn.content, "正在处理")
        self.assertEqual(turn.reasoning_content, "先判断是否要调用工具。")
        usage_events = [event for event in events if getattr(event, "type", "") == "usage"]
        self.assertEqual(usage_events[0].payload["prompt_cache_hit_tokens"], 80)

    def test_deepseek_tool_turn_replays_reasoning_content_after_tool_call(self) -> None:
        turn = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
            ),
            transport=lambda request, *, timeout: FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "reasoning_content": "我需要先读取 skill 目录。",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "regpilot_list_skills", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ),
        ).complete_with_tools([{"role": "user", "content": "有什么 skill"}], [])

        replay = _assistant_tool_call_message(turn)

        self.assertEqual(turn.reasoning_content, "我需要先读取 skill 目录。")
        self.assertEqual(replay["reasoning_content"], "我需要先读取 skill 目录。")
        self.assertEqual(replay["tool_calls"][0]["id"], "call_1")

    def test_deepseek_strict_tool_schema_adapter_uses_beta_endpoint_and_required_properties(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["url"] = request.full_url
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeResponse({"choices": [{"message": {"content": "ok", "tool_calls": []}}]})

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
                deepseek=DeepSeekOptions(strict_tool_schema=True),
            ),
            transport=transport,
        )

        client.complete_with_tools(
            [{"role": "user", "content": "安装 skill"}],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "regpilot_install_skill",
                        "description": "Install a skill.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "skill_id": {"type": "string"},
                                "path": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ],
        )

        function = seen["payload"]["tools"][0]["function"]
        self.assertEqual(seen["url"], "https://api.deepseek.com/beta/chat/completions")
        self.assertTrue(function["strict"])
        self.assertEqual(function["parameters"]["required"], ["skill_id", "path"])
        self.assertFalse(function["parameters"]["additionalProperties"])

    def test_deepseek_http_errors_retry_transient_and_preserve_error_code(self) -> None:
        attempts = 0

        def transport(request: Request, *, timeout: float):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise_http_error(503, {"error": {"message": "服务器繁忙"}})
            return FakeResponse({"choices": [{"message": {"content": "恢复"}}]})

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
                deepseek=DeepSeekOptions(retry_max_attempts=2),
            ),
            transport=transport,
            sleep=lambda _seconds: None,
        )

        self.assertEqual(client.complete_text([{"role": "user", "content": "你好"}]), "恢复")
        self.assertEqual(attempts, 2)

    def test_json_mode_retries_empty_content_and_reports_length_truncation(self) -> None:
        calls = 0

        def transport(request: Request, *, timeout: float):
            nonlocal calls
            calls += 1
            if calls == 1:
                return FakeResponse({"choices": [{"finish_reason": "stop", "message": {"content": ""}}]})
            return FakeResponse({"choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\": true}"}}]})

        client = OpenAICompatibleChatClient(
            ProviderConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com",
                model="deepseek-v4-pro",
                api_key="secret-key",
                deepseek=DeepSeekOptions(json_empty_retry_attempts=1),
            ),
            transport=transport,
        )

        self.assertEqual(client.complete_json([{"role": "user", "content": "return json"}]), {"ok": True})
        self.assertEqual(calls, 2)

    def test_regpilot_chat_responder_uses_text_chat_without_json_mode(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeResponse({"choices": [{"message": {"content": "我是 RegPilot，可以帮你梳理法规问题。"}}]})

        responder = ModelChatResponder(
            OpenAICompatibleChatClient(
                ProviderConfig(
                    provider="deepseek",
                    base_url="https://api.deepseek.com",
                    model="deepseek-v4-pro",
                    api_key="secret-key",
                ),
                transport=transport,
            )
        )

        reply = responder.respond("你好")

        self.assertIn("RegPilot", reply)
        self.assertNotIn("response_format", seen["payload"])
        self.assertIn("RegPilot", seen["payload"]["messages"][0]["content"])
        self.assertIn("不要使用 Markdown 表格", seen["payload"]["messages"][0]["content"])

    def test_regpilot_chat_responder_sends_recent_history(self) -> None:
        seen: dict[str, Any] = {}

        def transport(request: Request, *, timeout: float):
            seen["payload"] = json.loads((request.data or b"{}").decode("utf-8"))
            return FakeResponse({"choices": [{"message": {"content": "可以，我会接着前面的任务处理。"}}]})

        responder = ModelChatResponder(
            OpenAICompatibleChatClient(
                ProviderConfig(
                    provider="deepseek",
                    base_url="https://api.deepseek.com",
                    model="deepseek-v4-pro",
                    api_key="secret-key",
                ),
                transport=transport,
            )
        )

        responder.respond(
            "继续",
            history=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "第一轮回复"},
            ],
        )

        messages = seen["payload"]["messages"]
        self.assertEqual(messages[1:4], [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "第一轮回复"},
            {"role": "user", "content": "继续"},
        ])

    def test_regpilot_system_prompt_is_built_from_shared_layers(self) -> None:
        prompt = build_system_prompt("chat")

        self.assertIn("RegPilot", prompt)
        self.assertIn("市场合规领航员", prompt)
        self.assertIn("不要使用 Markdown 表格", prompt)

    def test_model_intent_parser_validates_allowed_task_and_column(self) -> None:
        parser = ModelIntentParser(
            FakeClient(
                {
                    "action": "formfill_run_until_stop",
                    "task_id": "shanghaiData_fill",
                    "workbook_path": r"D:\case\上海总表.xlsx",
                    "sheet": "SHGL备案参数",
                    "value_column": "e",
                    "auto_advance_policy": "until_before_final_submit",
                }
            )
        )

        intent = parser.parse("请按这个表填上海平台")

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent["task_id"], "shanghaiData_fill")
        self.assertEqual(intent["value_column"], "E")
        self.assertEqual(intent["sheet"], "SHGL备案参数")

    def test_model_intent_parser_uses_explicit_platform_not_workbook_name(self) -> None:
        parser = ModelIntentParser(
            FakeClient(
                {
                    "action": "formfill_run_until_stop",
                    "task_id": "shanghaiData_fill",
                    "workbook_path": r"D:\samples\regulatory_input.xlsx",
                    "sheet": "SHGL备案参数",
                    "value_column": "R",
                    "auto_advance_policy": "until_before_final_submit",
                }
            )
        )

        shanghai = parser.parse(
            r"帮我拿这个表 D:\samples\regulatory_input.xlsx 跑一下上海数据中心，数值在R列。"
        )
        landmark = parser.parse(
            r"用<D:\samples\regulatory_input.xlsx>的R列填地标平台。"
        )
        missing = parser.parse(
            r"用<D:\samples\regulatory_input.xlsx>的R列跑一下。"
        )

        assert shanghai is not None
        assert landmark is not None
        self.assertEqual(shanghai["task_id"], "shanghaiData_fill")
        self.assertEqual(landmark["task_id"], "landmark_fill")
        self.assertIsNone(missing)

    def test_model_intent_parser_rejects_unknown_tool_or_task(self) -> None:
        parser = ModelIntentParser(FakeClient({"action": "delete_files", "task_id": "danger"}))

        with self.assertRaises(ModelRuntimeError) as raised:
            parser.parse("删掉文件")

        self.assertEqual(raised.exception.code, "model_intent_rejected")

    def test_agent_turn_decision_parser_sends_candidate_skills_and_returns_json(self) -> None:
        client = FakeClient(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.shanghai_data",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_run_until_stop",
                "inputs": {"value_column": "E"},
            }
        )
        parser = AgentTurnDecisionParser(client)

        decision = parser.decide(
            r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台",
            candidate_skills=[
                {
                    "id": "formfill.shanghai_data",
                    "title": "上海数据平台填报",
                    "description": "受控填报。",
                    "task_id": "shanghaiData_fill",
                    "run_policy_default": "until_before_final_submit",
                    "allowed_run_policies": ["until_before_final_submit"],
                    "allowed_tools": ["formfill_run_until_stop"],
                    "reference_text": "人工修正后继续不得重新填写当前页。",
                }
            ],
            history=[{"role": "assistant", "content": "前文摘要"}],
        )

        self.assertEqual(decision["decision_type"], "skill_command")
        messages = client.messages[0]
        prompt_text = json.dumps(messages, ensure_ascii=False)
        self.assertIn("只能从 candidate_skills 中选择 skill_id", prompt_text)
        self.assertIn("上海数据平台填报", prompt_text)
        self.assertIn("人工修正后继续不得重新填写当前页", prompt_text)
        self.assertNotIn("workbook_path", messages[0]["content"])


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.messages.append(messages)
        return self.payload


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeStreamResponse:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.lines = []
        for payload in payloads:
            self.lines.extend(
                [
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n".encode("utf-8"),
                    b"\n",
                ]
            )
        self.lines.extend([b"data: [DONE]\n", b"\n"])

    def __enter__(self) -> "FakeStreamResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def readline(self) -> bytes:
        if not self.lines:
            return b""
        return self.lines.pop(0)


def raise_http_error(code: int, payload: dict[str, Any]) -> None:
    from io import BytesIO
    from urllib.error import HTTPError

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raise HTTPError("https://api.deepseek.com/chat/completions", code, "error", {}, BytesIO(body))


if __name__ == "__main__":
    unittest.main()
