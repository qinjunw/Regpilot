from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.model_runtime import ModelStreamEvent, ModelToolCall, ModelToolTurn
from regulation_agent.service import ApplicationService, MODEL_TOOL_LOOP_MAX_STEPS


class FormFillChatTests(unittest.TestCase):
    def test_chat_message_runs_shanghai_fill_until_final_review(self) -> None:
        fake = FakeHarness(
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
                    "step_id": "other",
                    "step_title": "其他信息",
                    "recommended_next_action": "final_review",
                    "stopped_reason": "final_boundary",
                    "traffic_light": {"green_count": 23, "yellow_count": 4, "red_count": 0},
                    "blocking_items": [],
                    "manual_intervention_count": 0,
                    "event_log": [{"event_id": 7, "type": "final_boundary_reached", "message": "已停在提交前。"}],
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake)

            result = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

            self.assertTrue(result["ok"])
            self.assertEqual(result["assistant"]["status"], "final_review")
            self.assertEqual(result["fill_result"]["status"], "final_review")
            self.assertEqual(len(fake.requests), 1)
            request = fake.requests[0]
            self.assertEqual(request.task_id, "shanghaiData_fill")
            self.assertEqual(request.workbook_path, r"D:\case\上海总表.xlsx")
            self.assertEqual(request.sheet, "SHGL备案参数")
            self.assertEqual(request.value_column, "E")
            self.assertEqual(request.auto_advance_policy, "until_before_final_submit")

            events = service.session_events(result["session_id"])
            event_types = [event["type"] for event in events]
            skill_events = [event["payload"] for event in events if event["type"] == "skill_command_selected"]
            self.assertEqual(len(skill_events), 1)
            self.assertEqual(skill_events[0]["skill_id"], "formfill.shanghai_data")
            self.assertEqual(skill_events[0]["goal"], "run_until_stop")
            self.assertEqual(skill_events[0]["run_policy"], "until_before_final_submit")
            self.assertNotIn("node_id", skill_events[0])
            self.assertIn("formfill_tool_started", event_types)
            self.assertIn("formfill_tool_completed", event_types)
            self.assertIn("assistant_status", event_types)

            bootstrap = service.bootstrap()
            self.assertEqual(bootstrap["task_context"]["status"], "ready")
            self.assertEqual(bootstrap["task_context"]["target_tool"], "上海数据平台")
            self.assertEqual(bootstrap["task_context"]["target_column"], "E列")
            self.assertEqual(bootstrap["review_summary"]["status"], "final_review")
            self.assertEqual(bootstrap["review_summary"]["totals"], {"green": 23, "yellow": 4, "red": 0})
            self.assertEqual(bootstrap["execution_status"]["final_stop"]["value"], "已停在提交前")

    def test_chat_message_uses_model_turn_decision_to_select_skill_and_call_formfill(self) -> None:
        fake = FakeHarness(
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
                "summary": {"status": "final_review", "traffic_light": {"green_count": 1, "yellow_count": 0, "red_count": 0}},
            }
        )
        turn_parser = FakeTurnDecisionParser(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.shanghai_data",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_run_until_stop",
                "inputs": {"value_column": "E"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=fake,
                model_turn_decision_parser=turn_parser,
                model_intent_parser=FakeIntentParser({"task_id": "ota_fill", "value_column": "Z"}),
            )

            result = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

            self.assertEqual(result["assistant"]["status"], "final_review")
            self.assertEqual(len(turn_parser.calls), 1)
            self.assertEqual(turn_parser.calls[0]["candidate_skills"][0]["id"], "formfill.shanghai_data")
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0].task_id, "shanghaiData_fill")
            self.assertEqual(fake.requests[0].value_column, "E")
            event_types = [event["type"] for event in service.session_events(result["session_id"])]
            self.assertIn("agent_turn_decision_parsed", event_types)
            self.assertIn("skill_command_selected", event_types)

    def test_chat_message_can_select_ota_skill_and_pass_attachment_folder(self) -> None:
        fake = FakeHarness(
            {
                "ok": True,
                "status": "final_review",
                "session_id": "sess_ota",
                "task_id": "ota_fill",
                "inputs": {
                    "task_id": "ota_fill",
                    "excel_path": r"D:\ota\总表.xlsx",
                    "sheet": "REEV车型及功能备案细分",
                    "value_column": "E",
                    "attachment_folder": r"D:\ota\attachments",
                },
            }
        )
        turn_parser = FakeTurnDecisionParser(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.ota",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_run_until_stop",
                "inputs": {
                    "workbook_path": r"D:\ota\总表.xlsx",
                    "value_column": "E",
                    "attachment_folder": r"D:\ota\attachments",
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=fake,
                model_turn_decision_parser=turn_parser,
            )

            result = service.post_user_message(r"用<D:\ota\总表.xlsx>的 E 列做 OTA平台填报，附件在 D:\ota\attachments")

            self.assertEqual(result["assistant"]["status"], "final_review")
            self.assertEqual([skill["id"] for skill in turn_parser.calls[0]["candidate_skills"]], ["formfill.ota"])
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0].task_id, "ota_fill")
            self.assertEqual(fake.requests[0].sheet, "REEV车型及功能备案细分")
            self.assertEqual(fake.requests[0].value_column, "E")
            self.assertEqual(fake.requests[0].attachment_folder, r"D:\ota\attachments")

    def test_chat_message_exposes_regpilot_tools_to_model_for_skill_discovery(self) -> None:
        tool_client = FakeToolChatClient(
            [
                ModelToolTurn(
                    content="",
                    tool_calls=[ModelToolCall("call_1", "regpilot_list_skills", {}, "{}")],
                ),
                ModelToolTurn(content="我目前可以使用的 Skill 是：上海数据平台填报。", tool_calls=[]),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )

            result = service.post_user_message("你有什么 skill?")

            self.assertEqual(result["assistant"]["status"], "chat")
            self.assertIn("上海数据平台填报", result["assistant"]["content"])
            self.assertEqual(tool_client.calls[0]["tools"][0]["function"]["name"], "regpilot_list_skills")
            self.assertEqual(tool_client.calls[0]["tools"][1]["function"]["name"], "regpilot_use_skill")
            self.assertEqual(tool_client.calls[1]["messages"][-1]["role"], "tool")
            self.assertIn("上海数据平台填报", tool_client.calls[1]["messages"][-1]["content"])
            system_prompt = tool_client.calls[0]["messages"][0]["content"]
            self.assertIn("不要使用 Markdown 表格", system_prompt)
            self.assertIn("不能作为聊天正文流式输出", system_prompt)
            self.assertIn("工具完成后只用简短中文说明", system_prompt)
            tool_payload = json.loads(tool_client.calls[1]["messages"][-1]["content"])
            self.assertEqual(tool_payload["presentation"]["audience"], "法规人员")
            self.assertIn("routing_id", tool_payload["presentation"]["hide_unless_asked"])

    def test_model_tool_loop_emits_provider_streaming_deltas(self) -> None:
        tool_client = StreamingToolChatClient(
            [ModelToolTurn(content="你好，provider streaming。", tool_calls=[])],
            deltas=[["你好，", "provider streaming。"]],
        )
        streamed_events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )

            result = service.post_user_message(
                "你好",
                event_sink=lambda _session_id, event: streamed_events.append(event),
            )

            self.assertEqual(result["assistant"]["status"], "chat")
            self.assertEqual(result["assistant"]["content"], "你好，provider streaming。")
            delta_events = [event for event in streamed_events if event["type"] == "assistant_delta"]
            self.assertEqual("".join(str(event["payload"]["delta"]) for event in delta_events), "你好，provider streaming。")
            event_types = [event["type"] for event in streamed_events]
            self.assertLess(event_types.index("assistant_delta"), event_types.index("assistant_status"))
            self.assertEqual(len(tool_client.stream_calls), 1)
            self.assertEqual(tool_client.sync_calls, [])

    def test_model_tool_loop_emits_provider_usage_cache_observation(self) -> None:
        usage = {
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
        tool_client = StreamingToolChatClient(
            [ModelToolTurn(content="完成。", tool_calls=[])],
            stream_events=[[ModelStreamEvent(type="usage", payload=usage)]],
        )
        streamed_events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )

            result = service.post_user_message(
                "你好",
                event_sink=lambda _session_id, event: streamed_events.append(event),
            )

            self.assertEqual(result["assistant"]["status"], "chat")
            usage_events = [event for event in streamed_events if event["type"] == "model_usage"]
            self.assertEqual(usage_events[0]["payload"]["prompt_cache_hit_tokens"], 80)
            persisted = [event for event in service.session_events(result["session_id"]) if event["type"] == "model_usage"]
            self.assertEqual(persisted[0]["payload"]["completion_tokens_details"]["reasoning_tokens"], 12)

    def test_streaming_model_tool_loop_keeps_management_tool_calls_compatible(self) -> None:
        tool_client = StreamingToolChatClient(
            [
                ModelToolTurn(
                    content="",
                    tool_calls=[ModelToolCall("call_1", "regpilot_list_skills", {}, "{}")],
                ),
                ModelToolTurn(content="我可以使用上海数据平台填报。", tool_calls=[]),
            ],
            deltas=[[], ["我可以使用", "上海数据平台填报。"]],
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )

            result = service.post_user_message("你有什么 skill?")

            self.assertEqual(result["assistant"]["status"], "chat")
            self.assertIn("上海数据平台填报", result["assistant"]["content"])
            self.assertEqual(len(tool_client.stream_calls), 2)
            self.assertEqual(tool_client.stream_calls[1]["messages"][-1]["role"], "tool")
            self.assertIn("上海数据平台填报", tool_client.stream_calls[1]["messages"][-1]["content"])
            self.assertEqual(tool_client.sync_calls, [])

    def test_model_tool_loop_suppresses_provider_deltas_from_tool_call_turns(self) -> None:
        tool_client = StreamingToolChatClient(
            [
                ModelToolTurn(
                    content="完整法规解读报告正文不应直接进入聊天框。",
                    tool_calls=[ModelToolCall("call_1", "regpilot_list_skills", {}, "{}")],
                ),
                ModelToolTurn(content="已生成解读文件，下面是简短结论。", tool_calls=[]),
            ],
            deltas=[
                ["完整法规解读报告正文", "不应直接进入聊天框。"],
                ["已生成解读文件，", "下面是简短结论。"],
            ],
        )
        streamed_events: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )

            result = service.post_user_message(
                "生成法规解读报告",
                event_sink=lambda _session_id, event: streamed_events.append(event),
            )

            self.assertEqual(result["assistant"]["status"], "chat")
            delta_text = "".join(
                str(event["payload"]["delta"]) for event in streamed_events if event["type"] == "assistant_delta"
            )
            self.assertEqual(delta_text, "已生成解读文件，下面是简短结论。")
            self.assertNotIn("完整法规解读报告正文", delta_text)
            model_messages = json.dumps(tool_client.stream_calls[1]["messages"], ensure_ascii=False)
            self.assertIn("完整法规解读报告正文", model_messages)

    def test_model_tool_loop_allows_sixty_four_steps_before_limit(self) -> None:
        tool_client = FakeToolChatClient(
            [
                ModelToolTurn(
                    content="",
                    tool_calls=[ModelToolCall(f"call_{index}", "regpilot_list_skills", {}, "{}")],
                )
                for index in range(MODEL_TOOL_LOOP_MAX_STEPS)
            ]
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )

            result = service.post_user_message("持续读取资料")

            self.assertEqual(result["assistant"]["status"], "model_tool_loop_limit")
            self.assertEqual(len(tool_client.calls), 64)

    def test_agent_turn_can_be_cancelled_between_model_tool_steps(self) -> None:
        tool_client = CancellingToolChatClient("turn_cancel")
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
            )
            tool_client.service = service

            result = service.post_user_message("停止这一轮", client_turn_id="turn_cancel")

            self.assertTrue(result["cancelled"])
            self.assertEqual(result["assistant"]["status"], "cancelled")
            event_types = [event["type"] for event in service.session_events(result["session_id"])]
            self.assertIn("agent_turn_cancel_requested", event_types)
            self.assertIn("assistant_status", event_types)

    def test_model_tool_catalog_exposes_skill_management_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=FakeHarness({"ok": True}))

            tool_names = [tool["function"]["name"] for tool in service._regpilot_model_tools()]

            self.assertEqual(tool_names[:2], ["regpilot_list_skills", "regpilot_use_skill"])
            self.assertIn("regpilot_inspect_skill", tool_names)
            self.assertIn("regpilot_create_skill_draft", tool_names)
            self.assertIn("regpilot_validate_skill", tool_names)
            self.assertIn("regpilot_install_skill", tool_names)
            self.assertIn("regpilot_enable_skill", tool_names)
            self.assertIn("regpilot_rename_skill", tool_names)
            self.assertIn("regpilot_load_skill", tool_names)
            self.assertIn("regpilot_ingest_sources", tool_names)
            self.assertIn("regpilot_build_evidence_bundle", tool_names)

    def test_model_tool_loop_can_load_enabled_ai_workflow_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_enabled_ai_workflow_skill(root / "installed" / "reg_read")
            tool_client = FakeToolChatClient(
                [
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_load",
                                "regpilot_load_skill",
                                {"skill_id": "automotive-regulation-interpretation"},
                                json_dumps({"skill_id": "automotive-regulation-interpretation"}),
                            )
                        ],
                    ),
                    ModelToolTurn(content="已加载法规解读 Skill。", tool_calls=[]),
                ]
            )
            service = ApplicationService(
                state_dir=Path(temp_name) / "state",
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
                skills_root=root,
            )

            result = service.post_user_message("加载法规解读 skill")

            self.assertEqual(result["assistant"]["status"], "chat")
            self.assertEqual(result["assistant"]["content"], "已加载法规解读 Skill。")
            tool_payload = json.loads(tool_client.calls[1]["messages"][-1]["content"])
            self.assertTrue(tool_payload["ok"])
            self.assertEqual(tool_payload["skill_type"], "ai_workflow")
            self.assertIn("# 法规解读", tool_payload["instructions"])
            self.assertIn("report workflow", tool_payload["references"][0]["content"])

    def test_model_tool_loop_can_create_validate_install_and_enable_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            tool_client = FakeToolChatClient(
                [
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_create",
                                "regpilot_create_skill_draft",
                                {
                                    "slug": "reg-read",
                                    "name": "automotive-regulation-interpretation",
                                    "title": "法规解读",
                                    "description": "Create source-grounded automotive regulation interpretation reports.",
                                    "skill_type": "ai_workflow",
                                },
                                json_dumps(
                                    {
                                        "slug": "reg-read",
                                        "name": "automotive-regulation-interpretation",
                                        "title": "法规解读",
                                        "description": "Create source-grounded automotive regulation interpretation reports.",
                                        "skill_type": "ai_workflow",
                                    }
                                ),
                            )
                        ],
                    ),
                    ModelToolTurn(content="草案已创建。", tool_calls=[]),
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_validate",
                                "regpilot_validate_skill",
                                {"path": "drafts/reg-read"},
                                json_dumps({"path": "drafts/reg-read"}),
                            )
                        ],
                    ),
                    ModelToolTurn(content="草案校验通过。", tool_calls=[]),
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_install",
                                "regpilot_install_skill",
                                {"path": "drafts/reg-read"},
                                json_dumps({"path": "drafts/reg-read"}),
                            )
                        ],
                    ),
                    ModelToolTurn(content="草案已安装，尚未启用。", tool_calls=[]),
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_enable",
                                "regpilot_enable_skill",
                                {"skill_id": "automotive-regulation-interpretation"},
                                json_dumps({"skill_id": "automotive-regulation-interpretation"}),
                            )
                        ],
                    ),
                    ModelToolTurn(content="Skill 已启用。", tool_calls=[]),
                ]
            )
            service = ApplicationService(
                state_dir=Path(temp_name) / "state",
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
                skills_root=root,
            )

            service.post_user_message("创建法规解读 skill 草案")
            self.assertTrue((root / "drafts" / "reg-read" / "SKILL.md").exists())
            self.assertEqual(service.agent_skills, [])

            service.post_user_message("校验法规解读 skill")
            service.post_user_message("安装法规解读 skill")
            self.assertTrue((root / "installed" / "reg-read" / "SKILL.md").exists())
            self.assertEqual(service.agent_skills, [])

            result = service.post_user_message("启用法规解读 skill")

            self.assertEqual(result["assistant"]["content"], "Skill 已启用。")
            self.assertEqual([skill["id"] for skill in service.agent_skills], ["automotive-regulation-interpretation"])

    def test_model_tool_loop_can_continue_after_tool_result_to_install_and_enable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            source_dir = root / "reg_read"
            _write_legacy_ai_workflow_skill(source_dir)
            tool_client = FakeToolChatClient(
                [
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_inspect",
                                "regpilot_inspect_skill",
                                {"path": str(source_dir)},
                                json_dumps({"path": str(source_dir)}),
                            )
                        ],
                    ),
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_install",
                                "regpilot_install_skill",
                                {"path": str(source_dir)},
                                json_dumps({"path": str(source_dir)}),
                            )
                        ],
                    ),
                    ModelToolTurn(
                        content="",
                        tool_calls=[
                            ModelToolCall(
                                "call_enable",
                                "regpilot_enable_skill",
                                {"skill_id": "automotive-regulation-interpretation"},
                                json_dumps({"skill_id": "automotive-regulation-interpretation"}),
                            )
                        ],
                    ),
                    ModelToolTurn(content="已安装并启用法规解读 Skill。", tool_calls=[]),
                ]
            )
            service = ApplicationService(
                state_dir=Path(temp_name) / "state",
                formfill_harness=FakeHarness({"ok": True}),
                model_tool_client=tool_client,
                skills_root=root,
            )

            result = service.post_user_message("请安装并启用这个本地 skill")

            self.assertEqual(result["assistant"]["content"], "已安装并启用法规解读 Skill。")
            self.assertTrue((root / "installed" / "reg_read" / "SKILL.md").exists())
            self.assertEqual([skill["id"] for skill in service.agent_skills], ["automotive-regulation-interpretation"])
            self.assertEqual(len(tool_client.calls), 4)
            self.assertEqual(
                [
                    event["payload"]["tool"]
                    for event in service.session_events(result["session_id"])
                    if event["type"] == "model_tool_call_started"
                ],
                ["regpilot_inspect_skill", "regpilot_install_skill", "regpilot_enable_skill"],
            )

    def test_model_tool_loop_can_rename_and_invoke_skill_by_custom_name(self) -> None:
        fake = FakeHarness(
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
                "summary": {"status": "final_review", "traffic_light": {"green_count": 1, "yellow_count": 0, "red_count": 0}},
            }
        )
        rename_arguments = {"skill_id": "formfill.shanghai_data", "display_name": "我的上海填报"}
        fill_arguments = {
            "skill_name": "我的上海填报",
            "regulatory_tool": "formfill_run_until_stop",
            "workbook_path": r"D:\case\上海总表.xlsx",
            "sheet": "SHGL备案参数",
            "value_column": "E",
            "run_policy": "until_before_final_submit",
        }
        tool_client = FakeToolChatClient(
            [
                ModelToolTurn(
                    content="",
                    tool_calls=[
                        ModelToolCall(
                            "call_rename",
                            "regpilot_rename_skill",
                            rename_arguments,
                            json_dumps(rename_arguments),
                        )
                    ],
                ),
                ModelToolTurn(content="已经改名为我的上海填报。", tool_calls=[]),
                ModelToolTurn(
                    content="",
                    tool_calls=[
                        ModelToolCall(
                            "call_fill",
                            "regpilot_use_skill",
                            fill_arguments,
                            json_dumps(fill_arguments),
                        )
                    ],
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_action_skill(root / "builtin" / "shanghai_data_fill")
            service = ApplicationService(
                state_dir=Path(temp_name) / "state",
                formfill_harness=fake,
                model_tool_client=tool_client,
                skills_root=root,
            )

            service.post_user_message("把上海数据平台填报改名为我的上海填报")
            self.assertEqual(service.agent_skills[0]["display_name"], "我的上海填报")
            result = service.post_user_message(r"用我的上海填报处理<D:\case\上海总表.xlsx>的 E 列")

            self.assertEqual(result["assistant"]["status"], "final_review")
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0].task_id, "shanghaiData_fill")
            skill_events = [
                event["payload"]
                for event in service.session_events(result["session_id"])
                if event["type"] == "skill_command_selected"
            ]
            self.assertEqual(skill_events[0]["skill_id"], "formfill.shanghai_data")
            self.assertEqual(skill_events[0]["skill_title"], "我的上海填报")

    def test_model_tool_call_can_invoke_shanghai_fill_skill(self) -> None:
        fake = FakeHarness(
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
                "summary": {"status": "final_review", "traffic_light": {"green_count": 1, "yellow_count": 0, "red_count": 0}},
            }
        )
        arguments = {
            "skill_id": "formfill.shanghai_data",
            "regulatory_tool": "formfill_run_until_stop",
            "workbook_path": r"D:\case\上海总表.xlsx",
            "sheet": "SHGL备案参数",
            "value_column": "E",
            "run_policy": "until_before_final_submit",
        }
        tool_client = FakeToolChatClient(
            [
                ModelToolTurn(
                    content="",
                    tool_calls=[
                        ModelToolCall(
                            "call_fill",
                            "regpilot_use_skill",
                            arguments,
                            json_dumps(arguments),
                        )
                    ],
                )
            ]
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=fake,
                model_tool_client=tool_client,
            )

            result = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

            self.assertEqual(result["assistant"]["status"], "final_review")
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0].workbook_path, r"D:\case\上海总表.xlsx")
            self.assertEqual(fake.requests[0].value_column, "E")
            event_types = [event["type"] for event in service.session_events(result["session_id"])]
            self.assertIn("model_tool_call_started", event_types)
            self.assertIn("skill_command_selected", event_types)

    def test_invalid_model_turn_decision_does_not_fall_back_to_tool_execution(self) -> None:
        fake = FakeHarness({"ok": True, "status": "final_review"})
        turn_parser = FakeTurnDecisionParser(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.unknown",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_run_until_stop",
                "inputs": {},
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=fake,
                model_turn_decision_parser=turn_parser,
            )

            result = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

            self.assertEqual(result["assistant"]["status"], "model_turn_decision_failed")
            self.assertEqual(fake.requests, [])
            event_types = [event["type"] for event in service.session_events(result["session_id"])]
            self.assertIn("agent_turn_decision_failed", event_types)

    def test_chat_message_surfaces_missing_inputs_without_fake_success(self) -> None:
        fake = FakeHarness(
            {
                "ok": False,
                "status": "needs_input",
                "missing_inputs": ["excel_path", "value_column"],
                "ambiguous_inputs": [],
                "questions": ["请确认要使用的总表/工作簿。", "请确认值所在列。"],
                "message": "需要补充或确认填报输入后才能创建 Fill Session。",
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake)

            result = service.post_user_message("帮我填报上海数据平台")

            self.assertTrue(result["ok"])
            self.assertEqual(result["assistant"]["status"], "needs_input")
            self.assertIn("需要补充或确认填报输入", result["assistant"]["content"])
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(service.bootstrap()["review_summary"]["status"], "needs_input")

    def test_chat_message_requests_human_action_when_fill_blocks(self) -> None:
        fake = FakeHarness(
            {
                "ok": True,
                "status": "advance_blocked",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "manual_fix",
                "human_handoff_required": True,
                "inputs": {
                    "task_id": "shanghaiData_fill",
                    "excel_path": r"D:\case\上海总表.xlsx",
                    "sheet": "SHGL备案参数",
                    "value_column": "E",
                    "auto_advance_policy": "until_before_final_submit",
                },
                "summary": {
                    "status": "advance_blocked",
                    "step_title": "基本信息",
                    "recommended_next_action": "manual_fix",
                    "traffic_light": {"green_count": 20, "yellow_count": 2, "red_count": 1},
                    "blocking_items": [{"code": "red_light", "label": "燃料类型", "message": "字段红灯，需要人工处理后继续。"}],
                    "manual_intervention_count": 1,
                    "event_log": [],
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake)

            result = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

            self.assertEqual(result["assistant"]["status"], "advance_blocked")
            self.assertEqual(service.bootstrap()["human_budget"]["pending_count"], 1)
            events = service.session_events(result["session_id"])
            event_types = [event["type"] for event in events]
            self.assertIn("human_action_requested", event_types)
            action = next(event["payload"] for event in events if event["type"] == "human_action_requested")
            self.assertIn("不要手动点击下一步", action["prompt"])
            self.assertIn("重新验证当前页", action["prompt"])
            self.assertEqual(action["options"][0]["label"], "人工修正后继续")

    def test_human_action_continue_resumes_formfill_session(self) -> None:
        fake = FakeHarness(
            {
                "ok": True,
                "status": "advance_blocked",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "manual_fix",
                "human_handoff_required": True,
                "inputs": {
                    "task_id": "shanghaiData_fill",
                    "excel_path": r"D:\case\上海总表.xlsx",
                    "sheet": "SHGL备案参数",
                    "value_column": "E",
                    "auto_advance_policy": "until_before_final_submit",
                },
                "summary": {
                    "status": "advance_blocked",
                    "step_title": "基本信息",
                    "traffic_light": {"green_count": 20, "yellow_count": 2, "red_count": 1},
                    "blocking_items": [{"code": "page_message", "label": "基本信息", "message": "车辆登记型号已存在"}],
                    "manual_intervention_count": 1,
                    "event_log": [],
                },
            },
            resume_result={
                "ok": True,
                "status": "final_review",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "final_review",
                "human_handoff_required": False,
                "inputs": {
                    "session_id": "sess_formfill",
                    "auto_advance_policy": "until_before_final_submit",
                },
                "summary": {
                    "status": "final_review",
                    "step_title": "其他信息",
                    "traffic_light": {"green_count": 24, "yellow_count": 0, "red_count": 0},
                    "blocking_items": [],
                    "manual_intervention_count": 0,
                    "event_log": [],
                },
            },
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake)

            blocked = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")
            action = next(
                event["payload"]
                for event in service.session_events(blocked["session_id"])
                if event["type"] == "human_action_requested"
            )
            response = service.respond_to_human_action(action["action_id"], "continue_after_manual_fix")

            self.assertEqual(fake.resume_calls, [("sess_formfill", "until_before_final_submit", False, 20)])
            self.assertEqual(response["status"], "resolved")
            self.assertEqual(response["fill_result"]["status"], "final_review")
            self.assertEqual(response["bootstrap"]["review_summary"]["status"], "final_review")
            self.assertEqual(response["bootstrap"]["task_context"]["target_tool"], "上海数据平台")
            self.assertEqual(response["bootstrap"]["task_context"]["target_column"], "E列")
            event_types = [event["type"] for event in service.session_events(blocked["session_id"])]
            self.assertIn("human_action_resolved", event_types)
            self.assertEqual(event_types.count("formfill_tool_completed"), 2)

    def test_continue_message_routes_to_pending_manual_resume(self) -> None:
        fake = FakeHarness(
            {
                "ok": True,
                "status": "advance_blocked",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "manual_fix",
                "human_handoff_required": True,
                "inputs": {
                    "task_id": "shanghaiData_fill",
                    "excel_path": r"D:\case\上海总表.xlsx",
                    "sheet": "SHGL备案参数",
                    "value_column": "E",
                    "auto_advance_policy": "until_before_final_submit",
                },
                "summary": {
                    "status": "advance_blocked",
                    "step_title": "基本信息",
                    "traffic_light": {"green_count": 20, "yellow_count": 2, "red_count": 1},
                    "blocking_items": [{"code": "page_message", "label": "基本信息", "message": "车辆登记型号已存在"}],
                    "manual_intervention_count": 1,
                    "event_log": [],
                },
            },
            resume_result={
                "ok": True,
                "status": "final_review",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "final_review",
                "human_handoff_required": False,
                "inputs": {"session_id": "sess_formfill", "auto_advance_policy": "until_before_final_submit"},
                "summary": {
                    "status": "final_review",
                    "step_title": "其他信息",
                    "traffic_light": {"green_count": 24, "yellow_count": 0, "red_count": 0},
                    "blocking_items": [],
                    "manual_intervention_count": 0,
                    "event_log": [],
                },
            },
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake)

            blocked = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")
            response = service.post_user_message("继续", blocked["session_id"])

            self.assertEqual(fake.resume_calls, [("sess_formfill", "until_before_final_submit", False, 20)])
            self.assertEqual(response["fill_result"]["status"], "final_review")
            self.assertEqual(response["assistant"]["status"], "final_review")

    def test_model_selected_resume_tool_routes_to_pending_manual_action(self) -> None:
        fake = FakeHarness(
            {
                "ok": True,
                "status": "advance_blocked",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "manual_fix",
                "human_handoff_required": True,
                "inputs": {
                    "task_id": "shanghaiData_fill",
                    "excel_path": r"D:\case\上海总表.xlsx",
                    "sheet": "SHGL备案参数",
                    "value_column": "E",
                    "auto_advance_policy": "until_before_final_submit",
                },
                "summary": {
                    "status": "advance_blocked",
                    "step_title": "基本信息",
                    "traffic_light": {"green_count": 20, "yellow_count": 2, "red_count": 1},
                    "blocking_items": [{"code": "page_message", "label": "基本信息", "message": "车辆登记型号已存在"}],
                    "manual_intervention_count": 1,
                    "event_log": [],
                },
            },
            resume_result={
                "ok": True,
                "status": "final_review",
                "session_id": "sess_formfill",
                "task_id": "shanghaiData_fill",
                "recommended_next_action": "final_review",
                "human_handoff_required": False,
                "inputs": {"session_id": "sess_formfill", "auto_advance_policy": "until_before_final_submit"},
                "summary": {
                    "status": "final_review",
                    "step_title": "其他信息",
                    "traffic_light": {"green_count": 24, "yellow_count": 0, "red_count": 0},
                    "blocking_items": [],
                    "manual_intervention_count": 0,
                    "event_log": [],
                },
            },
        )
        turn_parser = FakeTurnDecisionParser(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.shanghai_data",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_resume_after_manual_fix",
                "inputs": {},
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=fake,
            )

            blocked = service.post_user_message(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")
            service.model_turn_decision_parser = turn_parser
            response = service.post_user_message("上海数据平台已修正，继续填报", blocked["session_id"])

            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.resume_calls, [("sess_formfill", "until_before_final_submit", False, 20)])
            self.assertEqual(response["assistant"]["status"], "final_review")
            skill_events = [
                event["payload"]
                for event in service.session_events(blocked["session_id"])
                if event["type"] == "skill_command_selected"
            ]
            self.assertEqual(skill_events[-1]["regulatory_tool"], "formfill_resume_after_manual_fix")

    def test_model_selected_resume_tool_without_pending_action_does_not_restart_fill(self) -> None:
        fake = FakeHarness({"ok": True, "status": "final_review"})
        turn_parser = FakeTurnDecisionParser(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.shanghai_data",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_resume_after_manual_fix",
                "inputs": {},
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=fake,
                model_turn_decision_parser=turn_parser,
            )

            result = service.post_user_message("上海数据平台已修正，继续填报")

            self.assertEqual(fake.requests, [])
            self.assertEqual(fake.resume_calls, [])
            self.assertEqual(result["assistant"]["status"], "manual_resume_unavailable")
            self.assertIn("当前没有等待人工修正", result["assistant"]["content"])

    def test_chat_message_can_use_model_intent_when_rules_are_not_enough(self) -> None:
        fake = FakeHarness(
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
                    "traffic_light": {"green_count": 3, "yellow_count": 0, "red_count": 0},
                    "blocking_items": [],
                    "manual_intervention_count": 0,
                    "event_log": [],
                },
            }
        )
        parser = FakeIntentParser(
            {
                "task_id": "shanghaiData_fill",
                "workbook_path": r"D:\case\上海总表.xlsx",
                "sheet": "SHGL备案参数",
                "value_column": "E",
                "auto_advance_policy": "until_before_final_submit",
                "intent_source": "model",
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake, model_intent_parser=parser)

            result = service.post_user_message("照这个资料跑一下那个备案，不要保存提交")

            self.assertEqual(result["assistant"]["status"], "final_review")
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(fake.requests[0].task_id, "shanghaiData_fill")
            self.assertEqual(fake.requests[0].value_column, "E")
            event_types = [event["type"] for event in service.session_events(result["session_id"])]
            self.assertIn("model_intent_parsed", event_types)

    def test_model_intent_without_column_does_not_default_to_c_column(self) -> None:
        fake = FakeHarness({"ok": False, "status": "needs_input", "message": "缺少值所在列。"})
        parser = FakeIntentParser(
            {
                "task_id": "shanghaiData_fill",
                "workbook_path": r"D:\case\上海总表.xlsx",
                "sheet": "SHGL备案参数",
                "value_column": None,
                "auto_advance_policy": "until_before_final_submit",
                "intent_source": "model",
            }
        )
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name), formfill_harness=fake, model_intent_parser=parser)

            service.post_user_message("帮我用这个表填上海数据平台")

            self.assertEqual(len(fake.requests), 1)
            self.assertIsNone(fake.requests[0].value_column)

    def test_model_no_action_uses_regpilot_chat_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_intent_parser=FakeIntentParser(None),
                model_chat_responder=FakeChatResponder("你好，我是 RegPilot，可以帮你梳理法规和填报准备。"),
            )

            result = service.post_user_message("你好")

            self.assertEqual(result["assistant"]["status"], "chat")
            self.assertIn("RegPilot", result["assistant"]["content"])
            self.assertNotIn("尚未接入", result["assistant"]["content"])
            event_types = [event["type"] for event in service.session_events(result["session_id"])]
            self.assertIn("model_intent_no_action", event_types)
            self.assertIn("model_chat_completed", event_types)

    def test_model_chat_response_receives_recent_session_history(self) -> None:
        responder = FakeChatResponder(["第一轮回复", "第二轮回复"])
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(
                state_dir=Path(temp_name),
                formfill_harness=FakeHarness({"ok": True}),
                model_intent_parser=FakeIntentParser(None),
                model_chat_responder=responder,
            )

            first = service.post_user_message("你好")
            service.post_user_message("继续", first["session_id"])

            self.assertEqual(responder.calls[1]["content"], "继续")
            self.assertEqual(
                responder.calls[1]["history"],
                [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "第一轮回复"},
                ],
            )


class FakeHarness:
    def __init__(self, result: dict[str, Any], *, resume_result: dict[str, Any] | None = None) -> None:
        self.result = result
        self.resume_result = resume_result or result
        self.requests: list[Any] = []
        self.resume_calls: list[tuple[str, str, bool, int]] = []

    def run_until_stop(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return self.result

    def resume_after_manual_fix(
        self,
        session_id: str,
        *,
        policy: str,
        include_values: bool,
        max_steps: int,
    ) -> dict[str, Any]:
        self.resume_calls.append((session_id, policy, include_values, max_steps))
        return self.resume_result


class FakeIntentParser:
    def __init__(self, intent: dict[str, Any] | None) -> None:
        self.intent = intent
        self.messages: list[str] = []

    def parse(self, content: str) -> dict[str, Any] | None:
        self.messages.append(content)
        return self.intent


class FakeChatResponder:
    def __init__(self, reply: str | list[str]) -> None:
        self.replies = reply if isinstance(reply, list) else [reply]
        self.messages: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def respond(self, content: str, *, history: list[dict[str, str]] | None = None) -> str:
        self.messages.append(content)
        self.calls.append({"content": content, "history": list(history or [])})
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index]


class FakeToolChatClient:
    def __init__(self, turns: list[ModelToolTurn]) -> None:
        self.turns = turns
        self.calls: list[dict[str, Any]] = []

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> ModelToolTurn:
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        index = min(len(self.calls) - 1, len(self.turns) - 1)
        return self.turns[index]


class StreamingToolChatClient:
    def __init__(
        self,
        turns: list[ModelToolTurn],
        *,
        deltas: list[list[str]] | None = None,
        stream_events: list[list[Any]] | None = None,
    ) -> None:
        self.turns = turns
        self.deltas = deltas or [[] for _ in turns]
        self.stream_events = stream_events or [[] for _ in turns]
        self.stream_calls: list[dict[str, Any]] = []
        self.sync_calls: list[dict[str, Any]] = []

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> ModelToolTurn:
        self.sync_calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        index = min(len(self.sync_calls) - 1, len(self.turns) - 1)
        return self.turns[index]

    def complete_with_tools_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        on_event: Any = None,
        should_cancel: Any = None,
    ) -> ModelToolTurn:
        self.stream_calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        index = min(len(self.stream_calls) - 1, len(self.turns) - 1)
        for delta in self.deltas[index]:
            if should_cancel and should_cancel():
                break
            if on_event:
                on_event({"type": "content_delta", "delta": delta})
        for event in self.stream_events[index]:
            if should_cancel and should_cancel():
                break
            if on_event:
                on_event(event)
        return self.turns[index]


class CancellingToolChatClient:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.service: ApplicationService | None = None
        self.calls: list[dict[str, Any]] = []

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> ModelToolTurn:
        self.calls.append({"messages": messages, "tools": tools, "temperature": temperature})
        assert self.service is not None
        self.service.cancel_agent_turn(client_turn_id=self.turn_id)
        return ModelToolTurn(
            content="",
            tool_calls=[ModelToolCall("call_1", "regpilot_list_skills", {}, "{}")],
        )


class FakeTurnDecisionParser:
    def __init__(self, decision: dict[str, Any]) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    def decide(
        self,
        content: str,
        *,
        candidate_skills: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "content": content,
                "candidate_skills": list(candidate_skills),
                "history": list(history or []),
            }
        )
        return self.decision


def json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _write_enabled_ai_workflow_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: automotive-regulation-interpretation",
                "description: Create source-grounded automotive regulation interpretation reports.",
                "---",
                "",
                "# 法规解读",
                "",
                "Read `reference/report-workflow.md` before drafting.",
            ]
        ),
        encoding="utf-8",
    )
    reference_dir = skill_dir / "reference"
    reference_dir.mkdir(exist_ok=True)
    (reference_dir / "report-workflow.md").write_text("report workflow details", encoding="utf-8")
    (skill_dir / ".regpilot.json").write_text(
        '{"enabled": true, "skill_type": "ai_workflow", "status": "available"}',
        encoding="utf-8",
    )


def _write_legacy_ai_workflow_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: automotive-regulation-interpretation",
                "description: Create source-grounded automotive regulation interpretation reports.",
                "---",
                "",
                "# 法规解读",
                "",
                "Read `reference/report-workflow.md` before drafting.",
            ]
        ),
        encoding="utf-8",
    )


def _write_action_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: shanghai-data-fill",
                "description: Guarded Shanghai Data Platform fill.",
                "regpilot_skill: true",
                "---",
                "",
                "# 上海数据平台填报",
                "",
                "```json regpilot_manifest",
                "{",
                '  "id": "formfill.shanghai_data",',
                '  "title": "上海数据平台填报",',
                '  "description": "受控填报并停在提交前。",',
                '  "category": "fill",',
                '  "status": "available",',
                '  "task_id": "shanghaiData_fill",',
                '  "triggers": ["上海数据平台", "上海数据"],',
                '  "intent_keywords": ["填报", "备案"],',
                '  "allowed_tools": ["formfill_run_until_stop"],',
                '  "allowed_run_policies": ["until_before_final_submit"],',
                '  "run_policy_default": "until_before_final_submit",',
                '  "operation_nodes": []',
                "}",
                "```",
            ]
        ),
        encoding="utf-8",
    )
    reference_dir = skill_dir / "reference"
    reference_dir.mkdir(exist_ok=True)
    (reference_dir / "report-workflow.md").write_text("report workflow details", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
