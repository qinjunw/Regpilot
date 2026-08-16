from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .agent_skills import (
    build_skill_command,
    create_skill_draft,
    enable_skill,
    inspect_skill_source,
    install_skill,
    load_agent_skill,
    load_builtin_agent_skills,
    operator_skill_list,
    rename_skill,
    resolve_skill_identifier,
    select_skill_candidates,
    validate_agent_turn_decision,
    validate_skill_source,
)
from .artifacts import InterpretationArtifactStore
from .formfill_bridge import build_default_formfill_harness
from .model_runtime import (
    ModelRuntimeError,
    build_default_agent_turn_decision_parser,
    build_default_model_chat_responder,
    build_default_model_intent_parser,
    build_default_tool_chat_client,
)
from .prompt_builder import build_system_prompt
from .regulation_index import RegulationIndexStore
from .settings import ProviderSettingsStore, default_state_dir
from .source_documents import DocumentSourceStore
from .tools import default_tool_inventory


SESSION_SCHEMA_VERSION = 1
SESSION_MESSAGE_COMPACT_THRESHOLD = 24
SESSION_MESSAGE_RECENT_LIMIT = 12
SESSION_SUMMARY_CHAR_LIMIT = 3200
CONTROLLED_CHROME_MISSING_LABEL = "未检测到填报chrome"
CONTROLLED_CHROME_CONNECTED_LABEL = "填报chrome已连接"
SUPPORTED_MODEL_PROVIDERS = {"", "deepseek", "openai_compatible"}
SUPPORTED_MODEL_PROVIDER_MESSAGE = "当前仅支持 DeepSeekPro / OpenAI-compatible 模型接口；API Key 可自主输入并保存。"
MODEL_TOOL_LOOP_MAX_STEPS = 64


ControlledChromeProbe = Callable[[str], list[dict[str, Any]]]
ControlledChromeLauncher = Callable[[], dict[str, Any]]


class ApplicationService:
    def __init__(
        self,
        state_dir: Path | None = None,
        formfill_harness: Any | None = None,
        model_turn_decision_parser: Any | None = None,
        model_intent_parser: Any | None = None,
        model_chat_responder: Any | None = None,
        model_tool_client: Any | None = None,
        model_config_path: str | Path | None = None,
        enable_model_intent: bool | None = None,
        controlled_chrome_probe: ControlledChromeProbe | None = None,
        controlled_chrome_launcher: ControlledChromeLauncher | None = None,
        skills_root: str | Path | None = None,
    ) -> None:
        self.state_dir = state_dir or default_state_dir()
        self.settings = ProviderSettingsStore(self.state_dir)
        self.formfill_harness = formfill_harness
        self.model_turn_decision_parser = model_turn_decision_parser
        self.model_intent_parser = model_intent_parser
        self.model_chat_responder = model_chat_responder
        self.model_tool_client = model_tool_client
        self._model_turn_decision_parser_injected = model_turn_decision_parser is not None
        self._model_intent_parser_injected = model_intent_parser is not None
        self._model_chat_responder_injected = model_chat_responder is not None
        self._model_tool_client_injected = model_tool_client is not None
        self.model_config_path = Path(model_config_path).expanduser() if model_config_path else None
        self.enable_model_intent = _env_flag("REGULATION_AGENT_ENABLE_MODEL_INTENT") if enable_model_intent is None else bool(enable_model_intent)
        self.controlled_chrome_probe = controlled_chrome_probe or _default_controlled_chrome_probe
        self.controlled_chrome_launcher = controlled_chrome_launcher or _default_controlled_chrome_launcher
        self._controlled_chrome = _controlled_chrome_status_from_entries([], reason="startup")
        self.skills_root = Path(skills_root).expanduser() if skills_root else None
        self.agent_skills = load_builtin_agent_skills(self.skills_root)
        self.source_store = DocumentSourceStore(self.state_dir)
        self.regulation_index_store = RegulationIndexStore(self.state_dir)
        self.artifact_store = InterpretationArtifactStore(self.state_dir, self.source_store)
        self.session_store_dir = self.state_dir / "regulatory_sessions"
        self.sessions: dict[str, dict[str, Any]] = {}
        self.active_session_id = ""
        self.actions: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self._event_sinks: list[Callable[[str, dict[str, Any]], None]] = []
        self._agent_turn_lock = threading.RLock()
        self._active_agent_turns_by_session: dict[str, str] = {}
        self._active_agent_turn_sessions: dict[str, str] = {}
        self._cancelled_agent_turns: set[str] = set()
        self._task_context = _empty_task_context()
        self._execution_status = _empty_execution_status()
        self._review_summary = _empty_review_summary()
        self._load_stored_sessions()

    def bootstrap(self, session_id: str | None = None) -> dict[str, Any]:
        controlled_chrome = self.refresh_controlled_chrome("bootstrap")
        selected = self._selected_session(session_id)
        task_context = _session_view(selected, "task_context") if selected else _empty_task_context()
        execution_status = _session_view(selected, "execution_status") if selected else _empty_execution_status()
        execution_status = _with_controlled_chrome_status(execution_status, controlled_chrome)
        review_summary = _session_view(selected, "review_summary") if selected else _empty_review_summary()
        checklist = _session_view(selected, "checklist") if selected else _empty_checklist()
        tool_inventory = default_tool_inventory()
        return {
            "app": {
                "name": "RegPilot",
                "status": "running",
                "mode": "local",
                "state_dir": str(self.state_dir),
            },
            "provider": self.settings.public_view(),
            "active_session_id": self.active_session_id,
            "selected_session": _public_session(selected) if selected else {},
            "sessions": self.list_sessions()["sessions"],
            "task_context": task_context,
            "execution_status": execution_status,
            "controlled_chrome": controlled_chrome,
            "review_summary": review_summary,
            "checklist": checklist,
            "human_budget": {
                "planned_count": 0,
                "remaining_before_submit": 0,
                "pending_count": self._pending_action_count(),
            },
            "tool_readiness": _operator_tool_readiness(tool_inventory),
            "skills": operator_skill_list(self.agent_skills),
            "tools": tool_inventory,
        }

    def save_provider_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = str(payload.get("provider") or "").strip()
        if provider not in SUPPORTED_MODEL_PROVIDERS:
            raise ValueError(SUPPORTED_MODEL_PROVIDER_MESSAGE)
        public_view = self.settings.save(payload)
        if not self._model_turn_decision_parser_injected:
            self.model_turn_decision_parser = None
        if not self._model_intent_parser_injected:
            self.model_intent_parser = None
        if not self._model_chat_responder_injected:
            self.model_chat_responder = None
        if not self._model_tool_client_injected:
            self.model_tool_client = None
        return public_view

    def create_session(self, operator_label: str = "", title: str | None = None) -> dict[str, Any]:
        session_id = f"session_{uuid.uuid4().hex}"
        now = _utc_now()
        session = {
            "session_id": session_id,
            "operator_label": operator_label,
            "title": _clean_session_title(title) or f"新会话 {len(self.sessions) + 1}",
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "messages": [],
            "task_context": _empty_task_context(),
            "execution_status": _empty_execution_status(),
            "review_summary": _empty_review_summary(),
            "checklist": _empty_checklist(),
            "context_summary": _empty_context_summary(),
        }
        self.sessions[session_id] = session
        self.events[session_id] = []
        self.active_session_id = session_id
        self._append_event(session_id, "session_created", _public_session(session))
        return session

    def list_sessions(self, *, archived: bool = False) -> dict[str, Any]:
        target_status = "archived" if archived else "active"
        sessions = sorted(
            (_public_session(session) for session in self.sessions.values() if str(session.get("status") or "active") == target_status),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        archived_count = sum(1 for session in self.sessions.values() if str(session.get("status") or "active") == "archived")
        return {
            "ok": True,
            "active_session_id": self.active_session_id,
            "archived": archived,
            "archived_count": archived_count,
            "sessions": sessions,
        }

    def session_view(self, session_id: str) -> dict[str, Any]:
        selected = self._selected_session(session_id)
        if selected is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        task_context = _session_view(selected, "task_context")
        execution_status = _with_controlled_chrome_status(
            _session_view(selected, "execution_status"),
            self._controlled_chrome,
        )
        return {
            "ok": True,
            "active_session_id": self.active_session_id,
            "selected_session": _public_session(selected),
            "sessions": self.list_sessions()["sessions"],
            "task_context": task_context,
            "execution_status": execution_status,
            "review_summary": _session_view(selected, "review_summary"),
            "checklist": _session_view(selected, "checklist"),
            "human_budget": {
                "planned_count": 0,
                "remaining_before_submit": 0,
                "pending_count": self._pending_action_count(),
            },
            "controlled_chrome": self._controlled_chrome,
        }

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        session = self._require_chat_session(session_id)
        cleaned = _clean_session_title(title)
        if not cleaned:
            raise ValueError("会话名称不能为空。")
        session["title"] = cleaned
        session["updated_at"] = _utc_now()
        self._append_event(session_id, "session_renamed", {"session_id": session_id, "title": cleaned})
        return {"ok": True, "session": _public_session(session)}

    def archive_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_chat_session(session_id)
        session["status"] = "archived"
        session["archived_at"] = _utc_now()
        self._append_event(session_id, "session_archived", {"session_id": session_id})
        if self.active_session_id == session_id:
            self.active_session_id = self._latest_active_session_id()
        self._persist_session(session_id)
        return {
            "ok": True,
            "archived_session_id": session_id,
            "active_session_id": self.active_session_id,
            "session": _public_session(session),
        }

    def restore_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_chat_session(session_id)
        session["status"] = "active"
        session["archived_at"] = ""
        self.active_session_id = session_id
        self._append_event(session_id, "session_restored", {"session_id": session_id})
        return {
            "ok": True,
            "restored_session_id": session_id,
            "active_session_id": self.active_session_id,
            "session": _public_session(session),
        }

    def delete_session(self, session_id: str) -> dict[str, Any]:
        self._require_chat_session(session_id)
        del self.sessions[session_id]
        self.events.pop(session_id, None)
        for action_id, action in list(self.actions.items()):
            if action.get("session_id") == session_id:
                del self.actions[action_id]
        self._delete_session_file(session_id)
        if self.active_session_id == session_id:
            self.active_session_id = self._latest_active_session_id()
        return {"ok": True, "deleted_session_id": session_id, "active_session_id": self.active_session_id}

    def checklist(self, session_id: str | None = None) -> dict[str, Any]:
        selected = self._selected_session(session_id)
        return {"ok": True, "session_id": str((selected or {}).get("session_id") or ""), "checklist": _session_view(selected, "checklist") if selected else _empty_checklist()}

    def update_checklist(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self._require_chat_session(session_id)
        session["checklist"] = _normalize_checklist(payload)
        self._append_event(
            session_id,
            "checklist_updated",
            {"session_id": session_id, "row_count": len(session["checklist"]["rows"])},
        )
        return {"ok": True, "session_id": session_id, "checklist": _session_view(session, "checklist")}

    def controlled_chrome_status(self, reason: str = "manual") -> dict[str, Any]:
        return self.refresh_controlled_chrome(reason)

    def open_controlled_chrome(self) -> dict[str, Any]:
        status = self.refresh_controlled_chrome("open_check")
        if str(status.get("status") or "") == "connected":
            return {
                "ok": True,
                "launched": False,
                "controlled_chrome": status,
                "message": str(status.get("label") or CONTROLLED_CHROME_CONNECTED_LABEL),
            }
        raw_launch_result = self.controlled_chrome_launcher()
        launch_result = raw_launch_result if isinstance(raw_launch_result, dict) else {}
        status = self.refresh_controlled_chrome("open_after_launch")
        return {
            "ok": True,
            "launched": True,
            "controlled_chrome": status,
            "launch": _safe_controlled_chrome_launch_result(launch_result),
            "message": str(launch_result.get("message") or status.get("label") or ""),
        }

    def refresh_controlled_chrome(self, reason: str) -> dict[str, Any]:
        try:
            entries = self.controlled_chrome_probe(reason)
            status = _controlled_chrome_status_from_entries(entries, reason=reason)
        except Exception as exc:
            status = _controlled_chrome_status_from_entries([], reason=reason, error=str(exc))
        self._controlled_chrome = status
        return status

    def post_user_message(
        self,
        content: str,
        session_id: str | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        client_turn_id: str | None = None,
    ) -> dict[str, Any]:
        remove_sink = self._add_event_sink(event_sink) if event_sink else None
        turn_id = _safe_client_turn_id(client_turn_id) or f"turn_{uuid.uuid4().hex}"
        resolved_session_id = ""
        try:
            if not session_id or session_id not in self.sessions:
                session = self.create_session()
                session_id = session["session_id"]
            else:
                if str(self.sessions[session_id].get("status") or "active") == "archived":
                    raise ValueError("归档会话需要先恢复后才能继续。")
                self.active_session_id = session_id
            resolved_session_id = session_id
            self._begin_agent_turn(resolved_session_id, turn_id)
            self._maybe_title_from_first_user_message(session_id, content)
            event = {
                "message_id": f"msg_{uuid.uuid4().hex}",
                "role": "user",
                "content": content,
                "created_at": _utc_now(),
            }
            self._append_event(session_id, "message", event)
            if self._is_agent_turn_cancelled(turn_id):
                return self._agent_turn_cancelled_response(session_id, turn_id)
            pending_action = self._pending_continue_action(session_id)
            if pending_action and _is_continue_after_manual_fix_message(content):
                return self.respond_to_human_action(str(pending_action["action_id"]), "continue_after_manual_fix")
            model_tool_result = self._run_model_tool_turn(session_id, content, turn_id)
            if model_tool_result is not None:
                return model_tool_result
            if self._is_agent_turn_cancelled(turn_id):
                return self._agent_turn_cancelled_response(session_id, turn_id)
            fill_result = self._run_formfill_instruction(session_id, content)
            if fill_result is not None:
                return fill_result
            if self._is_agent_turn_cancelled(turn_id):
                return self._agent_turn_cancelled_response(session_id, turn_id)
            assistant = {
                "status": "pending_integration",
                "content": "模型运行时尚未接入；消息已记录。若要自动填报，请在消息中说明填报平台、表格路径和值所在列。",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
        finally:
            if resolved_session_id:
                self._finish_agent_turn(resolved_session_id, turn_id)
            if remove_sink:
                remove_sink()

    def _run_formfill_instruction(self, session_id: str, content: str) -> dict[str, Any] | None:
        candidate_skills = select_skill_candidates(content, skills=self.agent_skills)
        skill_command = None
        intent = None
        intent_status = "unavailable"
        intent_message = ""
        if candidate_skills:
            turn_decision, decision_status, decision_message = self._agent_turn_decision(session_id, content, candidate_skills)
            if decision_status == "failed":
                assistant = {
                    "status": "model_turn_decision_failed",
                    "content": f"模型工具决策失败：{decision_message}。本次不会调用填报工具。",
                }
                self._append_event(session_id, "assistant_status", assistant)
                return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
            if turn_decision and turn_decision.get("type") == "skill_command":
                skill_command = turn_decision
                intent = _intent_from_skill_command(skill_command)
            elif turn_decision:
                assistant = _assistant_from_turn_decision(turn_decision)
                self._append_event(session_id, "assistant_status", assistant)
                return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}

        if skill_command is None:
            intent, intent_status, intent_message = self._model_intent(session_id, content)
            skill_command = build_skill_command(content, intent, skills=self.agent_skills)
        if skill_command:
            return self._run_formfill_skill_command(session_id, content, skill_command)
        request = _build_harness_request(content, intent)
        if request is None:
            if intent_status == "no_action":
                chat_reply = self._model_chat_response(session_id, content)
                assistant = {
                    "status": "chat" if chat_reply else "no_action",
                    "content": chat_reply
                    or "模型已接入；没有识别到可执行填报任务。若要自动填报，请在消息中说明填报平台、表格路径和值所在列。",
                }
                self._append_event(session_id, "assistant_status", assistant)
                return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
            if intent_status == "failed":
                assistant = {
                    "status": "model_intent_failed",
                    "content": f"模型意图解析失败：{intent_message}。消息已记录。",
                }
                self._append_event(session_id, "assistant_status", assistant)
                return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
            return None
        return self._run_formfill_request(session_id, request, intent, None)

    def _run_formfill_skill_command(self, session_id: str, content: str, skill_command: dict[str, Any]) -> dict[str, Any]:
        selected_tool = str(skill_command.get("regulatory_tool") or "").strip()
        if selected_tool == "formfill_resume_after_manual_fix":
            self._append_event(session_id, "skill_command_selected", _safe_skill_command_event(skill_command))
            return self._resume_formfill_from_skill_command(session_id)
        if selected_tool and selected_tool != "formfill_run_until_stop":
            assistant = {
                "status": "unsupported_regulatory_tool",
                "content": f"Skill 选择了暂不支持的受控工具：{selected_tool}。本次不会调用填报工具。",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
        intent = _intent_from_skill_command(skill_command)
        request = _build_harness_request(content, intent)
        if request is None:
            assistant = {
                "status": "needs_input",
                "content": "已识别到填报 Skill，但缺少表格路径、工作表或值所在列等必要信息。",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
        return self._run_formfill_request(session_id, request, intent, skill_command)

    def _run_formfill_request(
        self,
        session_id: str,
        request: Any,
        intent: dict[str, Any] | None,
        skill_command: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.refresh_controlled_chrome("before_fill_tool")
        if skill_command:
            self._append_event(session_id, "skill_command_selected", _safe_skill_command_event(skill_command))
        assistant_start = {
            "status": "running",
            "content": _assistant_start_message(intent),
        }
        self._append_event(session_id, "assistant_status", assistant_start)
        self._append_event(
            session_id,
            "formfill_tool_started",
            {
                "tool": "formfill_run_until_stop",
                "task_id": request.task_id or "",
                "workbook_path": request.workbook_path or "",
                "workspace_dir": request.workspace_dir or "",
                "sheet": request.sheet or "",
                "value_column": request.value_column or "",
                "policy": request.auto_advance_policy or "",
                "intent_source": (intent or {}).get("intent_source", "rules"),
            },
        )
        try:
            harness = self._formfill_harness()
            result = harness.run_until_stop(request)
        except Exception as exc:
            assistant = {
                "status": getattr(exc, "code", "formfill_error"),
                "content": f"FormFill 调用失败：{getattr(exc, 'message', str(exc))}",
            }
            self._review_summary = _review_summary_from_error(assistant["content"])
            self._append_event(session_id, "assistant_status", assistant)
            return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}

        return self._record_fill_result(session_id, result)

    def _run_model_tool_turn(self, session_id: str, content: str, turn_id: str) -> dict[str, Any] | None:
        client = self._get_model_tool_client()
        if client is None:
            return None
        tools = self._regpilot_model_tools()
        messages = self._model_tool_messages(session_id, content)
        for _ in range(MODEL_TOOL_LOOP_MAX_STEPS):
            if self._is_agent_turn_cancelled(turn_id):
                return self._agent_turn_cancelled_response(session_id, turn_id)
            try:
                turn = self._complete_model_tool_turn(client, messages, tools, session_id, turn_id)
            except ModelRuntimeError as exc:
                if self._is_agent_turn_cancelled(turn_id):
                    return self._agent_turn_cancelled_response(session_id, turn_id)
                self._append_event(session_id, "model_tool_turn_failed", {"code": exc.code, "message": exc.message})
                assistant = {"status": "model_tool_turn_failed", "content": f"模型工具回合失败：{exc.message}。"}
                self._append_event(session_id, "assistant_status", assistant)
                return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
            if self._is_agent_turn_cancelled(turn_id):
                return self._agent_turn_cancelled_response(session_id, turn_id)

            if not turn.tool_calls:
                assistant = {
                    "status": "chat",
                    "content": str(turn.content or "我在当前回合没有选择可调用工具。").strip(),
                }
                self._append_event(session_id, "assistant_status", assistant)
                return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}

            messages.append(_assistant_tool_call_message(turn))
            for tool_call in turn.tool_calls:
                if self._is_agent_turn_cancelled(turn_id):
                    return self._agent_turn_cancelled_response(session_id, turn_id)
                self._append_event(
                    session_id,
                    "model_tool_call_started",
                    {"tool": tool_call.name, "tool_call_id": tool_call.id},
                )
                if tool_call.name == "regpilot_use_skill":
                    if self._is_agent_turn_cancelled(turn_id):
                        return self._agent_turn_cancelled_response(session_id, turn_id)
                    return self._handle_regpilot_use_skill_tool_call(session_id, content, tool_call.arguments)
                tool_result = self._handle_regpilot_management_tool_call(tool_call.name, tool_call.arguments)
                if str(tool_result.get("code") or "") in {"unsupported_model_tool", "tool_call_invalid"}:
                    assistant = {
                        "status": "model_tool_call_failed",
                        "content": f"模型工具调用失败：{tool_result.get('message') or tool_result.get('error') or tool_call.name}。",
                    }
                    self._append_event(session_id, "assistant_status", assistant)
                    return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
                self._append_event(
                    session_id,
                    "model_tool_call_completed",
                    {
                        "tool": tool_call.name,
                        "tool_call_id": tool_call.id,
                        "ok": bool(tool_result.get("ok", True)),
                        "code": str(tool_result.get("code") or ""),
                    },
                )
                if self._is_agent_turn_cancelled(turn_id):
                    return self._agent_turn_cancelled_response(session_id, turn_id)
                messages.append(_tool_result_message(tool_call, tool_result))

        assistant = {
            "status": "model_tool_loop_limit",
            "content": "模型工具回合达到上限；请根据已完成步骤继续下一步。",
        }
        self._append_event(session_id, "assistant_status", assistant)
        return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}

    def _complete_model_tool_turn(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        session_id: str,
        turn_id: str,
    ) -> Any:
        stream_method = getattr(client, "complete_with_tools_stream", None)
        if callable(stream_method):
            buffered_content_deltas: list[str] = []

            def handle_stream_event(event: Any) -> None:
                event_type = _event_attr(event, "type")
                if event_type == "content_delta":
                    delta = _event_attr(event, "delta")
                    if delta:
                        buffered_content_deltas.append(delta)
                    return
                self._handle_model_stream_event(session_id, event)

            turn = stream_method(
                messages,
                tools,
                temperature=0.2,
                on_event=handle_stream_event,
                should_cancel=lambda: self._is_agent_turn_cancelled(turn_id),
            )
            if not getattr(turn, "tool_calls", []):
                for delta in buffered_content_deltas:
                    self._emit_live_event(session_id, "assistant_delta", {"delta": delta, "source": "provider"})
            return turn
        return client.complete_with_tools(messages, tools, temperature=0.2)

    def _handle_model_stream_event(self, session_id: str, event: Any) -> None:
        event_type = _event_attr(event, "type")
        if event_type == "usage":
            payload = _event_payload(event)
            if payload:
                self._append_event(session_id, "model_usage", payload)
            return
        if event_type != "content_delta":
            return
        delta = _event_attr(event, "delta")
        if not delta:
            return
        self._emit_live_event(
            session_id,
            "assistant_delta",
            {"delta": delta, "source": "provider"},
        )

    def _handle_regpilot_use_skill_tool_call(
        self,
        session_id: str,
        content: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            decision = {
                "decision_type": "skill_command",
                "skill_id": self._resolve_available_skill_identifier(
                    str(arguments.get("skill_id") or arguments.get("skill_name") or arguments.get("display_name") or "")
                ),
                "run_policy": str(arguments.get("run_policy") or arguments.get("auto_advance_policy") or "until_before_final_submit"),
                "regulatory_tool": str(arguments.get("regulatory_tool") or "formfill_run_until_stop"),
                "inputs": {
                    "workbook_path": arguments.get("workbook_path"),
                    "workspace_dir": arguments.get("workspace_dir"),
                    "sheet": arguments.get("sheet"),
                    "value_column": arguments.get("value_column") or arguments.get("column"),
                    "attachment_folder": arguments.get("attachment_folder"),
                },
            }
            skill_command = validate_agent_turn_decision(decision, self.agent_skills)
        except ValueError as exc:
            assistant = {
                "status": "model_tool_call_failed",
                "content": f"模型工具参数无效：{exc}。本次不会调用填报工具。",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
        return self._run_formfill_skill_command(session_id, content, skill_command)

    def _handle_regpilot_management_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "regpilot_list_skills":
                return _skill_list_tool_result(self.agent_skills)
            if name == "regpilot_inspect_skill":
                return inspect_skill_source(
                    root=self.skills_root,
                    path=_optional_argument(arguments, "path"),
                    skill_id=_optional_argument(arguments, "skill_id"),
                )
            if name == "regpilot_create_skill_draft":
                return create_skill_draft(
                    root=self.skills_root,
                    slug=str(arguments.get("slug") or ""),
                    name=str(arguments.get("name") or ""),
                    title=str(arguments.get("title") or ""),
                    description=str(arguments.get("description") or ""),
                    skill_type=str(arguments.get("skill_type") or "ai_workflow"),
                )
            if name == "regpilot_validate_skill":
                return validate_skill_source(
                    root=self.skills_root,
                    path=_optional_argument(arguments, "path"),
                    skill_id=_optional_argument(arguments, "skill_id"),
                )
            if name == "regpilot_install_skill":
                result = install_skill(
                    root=self.skills_root,
                    path=_optional_argument(arguments, "path"),
                    skill_id=_optional_argument(arguments, "skill_id"),
                )
                self.agent_skills = load_builtin_agent_skills(self.skills_root)
                return result
            if name == "regpilot_enable_skill":
                result = enable_skill(root=self.skills_root, skill_id=str(arguments.get("skill_id") or ""))
                self.agent_skills = load_builtin_agent_skills(self.skills_root)
                return result
            if name == "regpilot_rename_skill":
                result = rename_skill(
                    root=self.skills_root,
                    skill_id=str(arguments.get("skill_id") or arguments.get("skill_name") or ""),
                    display_name=str(arguments.get("display_name") or arguments.get("name") or ""),
                )
                self.agent_skills = load_builtin_agent_skills(self.skills_root)
                return result
            if name == "regpilot_load_skill":
                return load_agent_skill(
                    root=self.skills_root,
                    skill_id=self._resolve_available_skill_identifier(
                        str(arguments.get("skill_id") or arguments.get("skill_name") or arguments.get("display_name") or "")
                    ),
                )
            if name == "regpilot_ingest_sources":
                paths = arguments.get("source_paths")
                if not isinstance(paths, list):
                    paths = []
                return self.source_store.ingest_source_paths(
                    [str(item) for item in paths],
                    collection_name=str(arguments.get("collection_name") or ""),
                )
            if name == "regpilot_search_sources":
                return self.source_store.search_sources(
                    collection_id=str(arguments.get("collection_id") or ""),
                    query=str(arguments.get("query") or ""),
                    top_k=int(arguments.get("top_k") or 5),
                )
            if name == "regpilot_build_evidence_bundle":
                raw_queries = arguments.get("queries")
                queries = [str(item) for item in raw_queries] if isinstance(raw_queries, list) else None
                return self.source_store.build_evidence_bundle(
                    collection_id=str(arguments.get("collection_id") or ""),
                    query=str(arguments.get("query") or ""),
                    queries=queries,
                    top_k=int(arguments.get("top_k") or 6),
                    total_char_limit=int(arguments.get("total_char_limit") or 64000),
                )
            if name == "regpilot_stage_regulation_sources":
                paths = arguments.get("source_paths")
                if not isinstance(paths, list):
                    paths = []
                return self.regulation_index_store.stage_sources(
                    [str(item) for item in paths],
                    recursive=bool(arguments.get("recursive", True)),
                    max_sources=int(arguments.get("max_sources") or 200),
                )
            if name == "regpilot_record_regulation_entries":
                raw_entries = arguments.get("entries")
                entries = [item for item in raw_entries if isinstance(item, dict)] if isinstance(raw_entries, list) else []
                collection_id = str(arguments.get("collection_id") or "")
                if collection_id:
                    self._validate_regulation_index_evidence_ids(collection_id=collection_id, entries=entries)
                return self.regulation_index_store.record_entries(
                    source_id=str(arguments.get("source_id") or ""),
                    source_path=str(arguments.get("source_path") or ""),
                    source_status=str(arguments.get("source_status") or "processed"),
                    entries=entries,
                    collection_id=collection_id,
                    source_title=str(arguments.get("source_title") or ""),
                    source_url=str(arguments.get("source_url") or ""),
                    message=str(arguments.get("message") or ""),
                )
            if name == "regpilot_export_regulation_index":
                raw_formats = arguments.get("formats")
                formats = [str(item) for item in raw_formats] if isinstance(raw_formats, list) else None
                return self.regulation_index_store.export_index(
                    output_dir=_optional_argument(arguments, "output_dir"),
                    formats=formats,
                    filename=str(arguments.get("filename") or "regulation_index"),
                    overwrite=bool(arguments.get("overwrite", False)),
                )
            if name == "regpilot_load_source_slice":
                return self.source_store.load_source_slice(
                    collection_id=str(arguments.get("collection_id") or ""),
                    evidence_id=str(arguments.get("evidence_id") or ""),
                    char_limit=int(arguments.get("char_limit") or 4000),
                )
            if name == "regpilot_generate_interpretation_report":
                raw_formats = arguments.get("formats")
                formats = [str(item) for item in raw_formats] if isinstance(raw_formats, list) else None
                raw_evidence_ids = arguments.get("source_evidence_ids")
                evidence_ids = [str(item) for item in raw_evidence_ids] if isinstance(raw_evidence_ids, list) else []
                return self.artifact_store.generate_interpretation_report(
                    collection_id=str(arguments.get("collection_id") or ""),
                    title=str(arguments.get("title") or ""),
                    markdown=str(arguments.get("markdown") or ""),
                    source_evidence_ids=evidence_ids,
                    formats=formats,
                    output_dir=_optional_argument(arguments, "output_dir"),
                    filename=str(arguments.get("filename") or ""),
                    overwrite=bool(arguments.get("overwrite", False)),
                )
            return {"ok": False, "code": "unsupported_model_tool", "message": f"模型选择了未暴露的 RegPilot 工具：{name}。"}
        except Exception as exc:
            return {"ok": False, "code": "tool_call_invalid", "message": str(exc)}

    def _get_model_tool_client(self) -> Any | None:
        if self.model_tool_client is not None:
            return self.model_tool_client
        if self._model_turn_decision_parser_injected or self._model_intent_parser_injected or self._model_chat_responder_injected:
            return None
        settings = self.settings.load()
        has_saved_provider = bool(settings.get("provider") and settings.get("model") and settings.get("api_key"))
        if not self.enable_model_intent and not has_saved_provider:
            return None
        self.model_tool_client = build_default_tool_chat_client(
            settings=settings,
            config_path=self.model_config_path if self.enable_model_intent else None,
        )
        return self.model_tool_client

    def _validate_regulation_index_evidence_ids(self, *, collection_id: str, entries: list[dict[str, Any]]) -> None:
        collection = self.source_store.read_collection(collection_id)
        valid_ids = {
            str(item.get("evidence_id") or "")
            for item in collection.get("evidence", [])
            if str(item.get("evidence_id") or "").strip()
        }
        invalid_ids = []
        for entry in entries:
            raw_ids = entry.get("source_evidence_ids") or entry.get("evidence_ids") or []
            if not isinstance(raw_ids, list):
                continue
            invalid_ids.extend(str(item) for item in raw_ids if str(item) not in valid_ids)
        if invalid_ids:
            raise ValueError("source_evidence_ids must belong to collection_id: " + ", ".join(sorted(set(invalid_ids))[:10]))

    def _model_tool_messages(self, session_id: str, content: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_system_prompt("tool_loop"),
            }
        ]
        messages.extend(self._session_history_for_model(session_id, content))
        messages.append({"role": "user", "content": str(content or "")})
        return messages

    def _regpilot_model_tools(self) -> list[dict[str, Any]]:
        skill_ids = [str(skill.get("id") or "") for skill in self.agent_skills if str(skill.get("status") or "") != "hidden"]
        skill_names = [
            str(skill.get("display_name") or skill.get("title") or "")
            for skill in self.agent_skills
            if str(skill.get("status") or "") != "hidden" and str(skill.get("display_name") or skill.get("title") or "").strip()
        ]
        return [
            {
                "type": "function",
                "function": {
                    "name": "regpilot_list_skills",
                    "description": "列出当前 RegPilot 向法规人员开放的业务 Skill。只返回业务 Skill，不暴露内部操作节点。",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_use_skill",
                    "description": "调用一个 RegPilot 业务 Skill。用于受控填报工作流；后端会按 Skill 规则分派到受控 FormFill 工具，并停在保存/提交前。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {
                                "type": "string",
                                "enum": skill_ids,
                                "description": "要调用的 RegPilot Skill ID；也可改用 skill_name 传自定义名称。",
                            },
                            "skill_name": {"type": "string", "enum": skill_names, "description": "要调用的 RegPilot Skill 自定义名或显示名。"},
                            "regulatory_tool": {
                                "type": "string",
                                "enum": ["formfill_run_until_stop", "formfill_resume_after_manual_fix"],
                                "description": "要由后端分派的受控工具。普通填报使用 formfill_run_until_stop；人工修正后继续使用 formfill_resume_after_manual_fix。",
                            },
                            "workbook_path": {"type": "string", "description": "用户提供的工作簿路径，未知则留空。"},
                            "workspace_dir": {"type": "string", "description": "用户提供的工作目录，未知则留空。"},
                            "sheet": {"type": "string", "description": "工作表名，上海数据平台默认 SHGL备案参数。"},
                            "value_column": {"type": "string", "description": "值所在列，例如 E。"},
                            "attachment_folder": {"type": "string", "description": "附件目录，未知则留空。"},
                            "run_policy": {
                                "type": "string",
                                "enum": ["disabled", "until_blocked", "until_before_final_submit"],
                                "description": "自动推进策略，默认 until_before_final_submit。",
                            },
                        },
                        "required": ["regulatory_tool"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_inspect_skill",
                    "description": "只读检查一个 RegPilot Skill 目录、集合目录或已知 skill_id，返回类型、状态、校验摘要、候选项和来源路径。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "description": "已知 Skill ID。与 path 二选一。"},
                            "path": {"type": "string", "description": "用户明确提供的 skill 目录或 SKILL.md 路径。与 skill_id 二选一。"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_create_skill_draft",
                    "description": "在 skills/drafts 下创建一个未启用的 Skill 草案。不会进入可用 catalog。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "草案目录名，仅允许小写字母、数字、下划线和连字符。"},
                            "name": {"type": "string", "description": "Skill ID/name，例如 automotive-regulation-interpretation。"},
                            "title": {"type": "string", "description": "面向人的标题。"},
                            "description": {"type": "string", "description": "一句能力说明。"},
                            "skill_type": {"type": "string", "enum": ["ai_workflow", "action_skill"], "description": "Skill 类型。"},
                        },
                        "required": ["slug", "name", "title", "description", "skill_type"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_validate_skill",
                    "description": "校验一个 skill 草案、已安装 skill 或外部 SKILL.md 是否满足 RegPilot 最小结构。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "description": "已知 Skill ID。与 path 二选一。"},
                            "path": {"type": "string", "description": "skill 目录或 SKILL.md 路径。与 skill_id 二选一。"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_install_skill",
                    "description": "把已校验的 skill 草案、local_source 或外部 skill 复制到 skills/installed，默认不启用；集合目录需要先选择具体候选。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "description": "已知 Skill ID。与 path 二选一。"},
                            "path": {"type": "string", "description": "skill 目录或 SKILL.md 路径。与 skill_id 二选一。"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_enable_skill",
                    "description": "启用 builtin 或 installed skill，使其进入模型可发现 catalog；drafts、local_source 和 external 需要先 install。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "description": "要启用的 Skill ID。"},
                        },
                        "required": ["skill_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_rename_skill",
                    "description": "仅修改 builtin 或 installed skill 的本地自定义显示名，不改 SKILL.md、manifest、描述、reference 或渐进式披露内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "enum": skill_ids, "description": "要改名的稳定 Skill ID。"},
                            "skill_name": {"type": "string", "enum": skill_names, "description": "当前显示名或自定义名；与 skill_id 二选一。"},
                            "display_name": {"type": "string", "description": "新的自定义显示名，最多 80 个字符。"},
                        },
                        "required": ["display_name"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_load_skill",
                    "description": "加载已启用 skill 的 SKILL.md 和 reference 文本，供模型按说明工作；不执行网页或文件解析副作用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "skill_id": {"type": "string", "enum": skill_ids, "description": "要加载的已启用 Skill ID；也可改用 skill_name 传自定义名称。"},
                            "skill_name": {"type": "string", "enum": skill_names, "description": "要加载的已启用 Skill 自定义名或显示名。"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_ingest_sources",
                    "description": "登记并确定性解析用户明确提供的本机法规资料路径，生成可检索的 Source Evidence。支持 md/txt/docx/xlsx/文本型 pdf；不做 OCR、远程转换或内容伪造。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "用户明确提供的本机来源文件路径列表。不接受模型猜测路径。",
                            },
                            "collection_name": {"type": "string", "description": "可选的人类可读资料集合名。"},
                        },
                        "required": ["source_paths"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_search_sources",
                    "description": "在已摄入的 Source Evidence 中检索相关片段，返回短摘录、来源 id 和页码/段落/表格行定位。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "collection_id": {"type": "string", "description": "regpilot_ingest_sources 返回的资料集合 ID。"},
                            "query": {"type": "string", "description": "要检索的法规术语、条款、标准号或问题关键词。"},
                            "top_k": {"type": "integer", "description": "最多返回多少条证据，默认 5，最大 20。"},
                        },
                        "required": ["collection_id", "query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_build_evidence_bundle",
                    "description": "围绕一个或多个法规解读主题批量检索 Source Evidence，去重后返回带 evidence_id 和页码/段落/表格行定位的证据包，适合长上下文模型减少多轮 slice 调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "collection_id": {"type": "string", "description": "regpilot_ingest_sources 返回的资料集合 ID。"},
                            "query": {"type": "string", "description": "单个检索主题；也可改用 queries。"},
                            "queries": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "多个检索主题，例如 scope、definitions、test method、approval、transition。",
                            },
                            "top_k": {"type": "integer", "description": "每个主题最多返回多少条证据，默认 6，最大 20。"},
                            "total_char_limit": {"type": "integer", "description": "证据包总字符上限，默认 64000，最大 256000。"},
                        },
                        "required": ["collection_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_stage_regulation_sources",
                    "description": "登记用户明确提供的本机法规动态来源文件或目录，按来源文章/公告条目建立待整理队列；后端维护状态和附件关系，批量大小由已加载 skill 指导模型选择。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "用户明确提供的本机来源文件或目录路径列表。不接受模型猜测路径。",
                            },
                            "recursive": {"type": "boolean", "description": "目录是否递归扫描，默认 true。"},
                            "max_sources": {"type": "integer", "description": "本次最多新增多少个待整理来源文章；模型应按 skill 的小批量建议自行选择，后端仅做安全上限。"},
                        },
                        "required": ["source_paths"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_record_regulation_entries",
                    "description": "记录模型从一个已登记来源文章中抽取出的法规索引条目；后端按法规编号优先、法规名称其次归并为一条法规主记录，并保留状态历史、附件和来源证据。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source_id": {"type": "string", "description": "regpilot_stage_regulation_sources 返回的来源 ID。"},
                            "source_path": {"type": "string", "description": "可选来源路径；仅在 source_id 不便使用时作为后备定位。"},
                            "source_status": {
                                "type": "string",
                                "enum": ["processed", "processed_with_no_entries", "unprocessed"],
                                "description": "processed 表示已整理且记录了可抽取条目；processed_with_no_entries 表示已读但无法规身份；unprocessed 表示读取失败、格式不适合或需要人工补充。",
                            },
                            "collection_id": {"type": "string", "description": "如本来源已通过 regpilot_ingest_sources 解析，可填资料集合 ID。"},
                            "source_title": {"type": "string", "description": "来源文章标题，如资料中明确给出。"},
                            "source_url": {"type": "string", "description": "来源网页 URL，如采集资料中明确给出。"},
                            "message": {"type": "string", "description": "处理说明；失败或未处理时写明原因。"},
                            "entries": {
                                "type": "array",
                                "description": "从该来源文章抽取的法规索引条目；同一法规编号/名称会被后端归并到一条法规主记录。",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "regulation_number": {"type": "string", "description": "法规/标准编号，例如 GB 12345、UN R127。未知可留空。"},
                                        "regulation_name": {"type": "string", "description": "法规/标准名称。编号未知时必须填写。"},
                                        "regulation_status": {
                                            "type": "string",
                                            "description": "法规状态，例如 立项、征求意见、报批稿、发布、实施、修订、废止、未知。",
                                        },
                                        "event_date": {"type": "string", "description": "与该来源提到的状态相关的主日期，优先 YYYY-MM-DD；资料未提及时留空。"},
                                        "date_type": {"type": "string", "description": "主日期类型，例如 公告日期、发布日期、实施日期。"},
                                        "notice_date": {"type": "string", "description": "公告或征求意见开始日期。"},
                                        "comment_deadline": {"type": "string", "description": "意见截止日期；不要为了截止日期创建第二条相同法规状态记录。"},
                                        "effective_date": {"type": "string", "description": "实施日期或生效日期。"},
                                        "issuing_body": {"type": "string", "description": "发布/征求意见/管理机构。"},
                                        "source_article_title": {"type": "string", "description": "支撑该条目的来源文章标题。"},
                                        "source_url": {"type": "string", "description": "支撑该条目的来源 URL。"},
                                        "source_evidence_ids": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                            "description": "支撑该条目的 Source Evidence ID 列表。",
                                        },
                                        "attachments": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "file_name": {"type": "string"},
                                                    "title": {"type": "string"},
                                                    "path_or_url": {"type": "string"},
                                                    "file_type": {"type": "string"},
                                                    "relationship": {"type": "string"},
                                                },
                                                "additionalProperties": False,
                                            },
                                            "description": "来源文章明确提到或采集包中明确关联的附件。附件是来源文章的子对象，不是独立法规索引条目。",
                                        },
                                        "notes": {"type": "string", "description": "必要的短备注；不要写长篇解读。"},
                                        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
                                    },
                                    "required": ["regulation_name", "regulation_status"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["source_status", "entries"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_export_regulation_index",
                    "description": "把当前法规索引库导出为结构化 JSON/CSV 文件，便于后续 skill 或人工流程调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "formats": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["json", "csv"]},
                                "description": "导出格式，默认 json 和 csv。",
                            },
                            "output_dir": {"type": "string", "description": "可选输出目录；未知或用户未指定时留空，后端写入默认 artifacts 目录。"},
                            "filename": {"type": "string", "description": "可选文件名，不含扩展名，默认 regulation_index。"},
                            "overwrite": {"type": "boolean", "description": "是否允许覆盖同名文件，默认 false；false 时自动避让重名。"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_load_source_slice",
                    "description": "按 evidence_id 加载一条 Source Evidence 的受限文本切片，用于渐进式披露和引用核对。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "collection_id": {"type": "string", "description": "regpilot_ingest_sources 返回的资料集合 ID。"},
                            "evidence_id": {"type": "string", "description": "regpilot_search_sources 返回的证据 ID。"},
                            "char_limit": {"type": "integer", "description": "返回文本字符上限，默认 4000，最大 64000。"},
                        },
                        "required": ["collection_id", "evidence_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "regpilot_generate_interpretation_report",
                    "description": "把模型基于 Source Evidence 写成的法规解读 Markdown 保存为本地 .md 和/或 .docx Regulatory Artifact。后端只负责校验证据 ID、落盘和 DOCX 导出，不替模型编造解读内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "collection_id": {"type": "string", "description": "regpilot_ingest_sources 返回的资料集合 ID。"},
                            "title": {"type": "string", "description": "报告标题。"},
                            "markdown": {"type": "string", "description": "完整报告 Markdown。必须由模型基于已加载 Source Evidence 编写。"},
                            "source_evidence_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "报告实际使用的 evidence_id 列表，必须属于 collection_id。",
                            },
                            "formats": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["md", "docx"]},
                                "description": "输出格式，默认同时生成 md 和 docx。",
                            },
                            "output_dir": {"type": "string", "description": "可选输出目录；未知或用户未指定时留空，后端写入默认 artifacts 目录。"},
                            "filename": {"type": "string", "description": "可选文件名，不含扩展名。默认从标题生成。"},
                            "overwrite": {"type": "boolean", "description": "是否允许覆盖已有文件，默认 false。"},
                        },
                        "required": ["collection_id", "title", "markdown", "source_evidence_ids"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _resolve_available_skill_identifier(self, identifier: str) -> str:
        return resolve_skill_identifier(identifier, self.agent_skills)

    def _resume_formfill_from_skill_command(self, session_id: str) -> dict[str, Any]:
        action = self._pending_continue_action(session_id)
        if action is None:
            assistant = {
                "status": "manual_resume_unavailable",
                "content": "当前没有等待人工修正的填报任务；请先按聊天中的阻塞提示完成页面修正，再点击“人工修正后继续”，或重新发起填报。",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {"ok": True, "session_id": session_id, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}
        return self.respond_to_human_action(str(action["action_id"]), "continue_after_manual_fix")

    def _agent_turn_decision(
        self,
        session_id: str,
        content: str,
        candidate_skills: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str, str]:
        parser = self._get_model_turn_decision_parser()
        if parser is None:
            return None, "unavailable", ""
        try:
            raw_decision = parser.decide(
                content,
                candidate_skills=candidate_skills,
                history=self._session_history_for_model(session_id, content),
            )
            decision = validate_agent_turn_decision(raw_decision, candidate_skills)
        except (ModelRuntimeError, ValueError) as exc:
            message = getattr(exc, "message", str(exc))
            self._append_event(
                session_id,
                "agent_turn_decision_failed",
                {"message": message},
            )
            return None, "failed", message
        self._append_event(
            session_id,
            "agent_turn_decision_parsed",
            _safe_turn_decision_event(decision),
        )
        return decision, "parsed", ""

    def _get_model_turn_decision_parser(self) -> Any | None:
        if self.model_turn_decision_parser is not None:
            return self.model_turn_decision_parser
        settings = self.settings.load()
        has_saved_provider = bool(settings.get("provider") and settings.get("model") and settings.get("api_key"))
        if not self.enable_model_intent and not has_saved_provider:
            return None
        self.model_turn_decision_parser = build_default_agent_turn_decision_parser(
            settings=settings,
            config_path=self.model_config_path if self.enable_model_intent else None,
        )
        return self.model_turn_decision_parser

    def _model_intent(self, session_id: str, content: str) -> tuple[dict[str, Any] | None, str, str]:
        parser = self._get_model_intent_parser()
        if parser is None:
            return None, "unavailable", ""
        try:
            intent = parser.parse(content)
        except ModelRuntimeError as exc:
            self._append_event(
                session_id,
                "model_intent_failed",
                {"code": exc.code, "message": exc.message},
            )
            return None, "failed", exc.message
        if intent is None:
            message = "模型没有识别到可执行填报任务。"
            self._append_event(session_id, "model_intent_no_action", {"message": message})
            return None, "no_action", message
        self._append_event(
            session_id,
            "model_intent_parsed",
            {
                "task_id": intent.get("task_id", ""),
                "sheet": intent.get("sheet", ""),
                "value_column": intent.get("value_column", ""),
                "intent_source": intent.get("intent_source", "model"),
            },
        )
        return intent, "parsed", ""

    def _get_model_intent_parser(self) -> Any | None:
        if self.model_intent_parser is not None:
            return self.model_intent_parser
        settings = self.settings.load()
        has_saved_provider = bool(settings.get("provider") and settings.get("model") and settings.get("api_key"))
        if not self.enable_model_intent and not has_saved_provider:
            return None
        self.model_intent_parser = build_default_model_intent_parser(
            settings=settings,
            config_path=self.model_config_path if self.enable_model_intent else None,
        )
        return self.model_intent_parser

    def _model_chat_response(self, session_id: str, content: str) -> str | None:
        responder = self._get_model_chat_responder()
        if responder is None:
            return None
        try:
            reply = str(responder.respond(content, history=self._session_history_for_model(session_id, content)) or "").strip()
        except ModelRuntimeError as exc:
            self._append_event(
                session_id,
                "model_chat_failed",
                {"code": exc.code, "message": exc.message},
            )
            return None
        if reply:
            self._append_event(session_id, "model_chat_completed", {"message": "RegPilot 已生成普通聊天回复。"})
            return reply
        return None

    def _get_model_chat_responder(self) -> Any | None:
        if self.model_chat_responder is not None:
            return self.model_chat_responder
        settings = self.settings.load()
        has_saved_provider = bool(settings.get("provider") and settings.get("model") and settings.get("api_key"))
        if not self.enable_model_intent and not has_saved_provider:
            return None
        self.model_chat_responder = build_default_model_chat_responder(
            settings=settings,
            config_path=self.model_config_path if self.enable_model_intent else None,
        )
        return self.model_chat_responder

    def _formfill_harness(self) -> Any:
        if self.formfill_harness is None:
            self.formfill_harness = build_default_formfill_harness()
        return self.formfill_harness

    def _record_fill_result(self, session_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self._update_runtime_state(result, session_id=session_id)
        self._append_event(session_id, "formfill_tool_completed", _safe_fill_event(result))
        assistant = _assistant_from_fill_result(result)
        self._append_event(session_id, "assistant_status", assistant)
        if result.get("human_handoff_required"):
            self._request_formfill_manual_action(session_id, result)
        return {
            "ok": True,
            "session_id": session_id,
            "assistant": assistant,
            "fill_result": result,
            "bootstrap": self.bootstrap(session_id),
        }

    def _request_formfill_manual_action(self, session_id: str, result: dict[str, Any]) -> dict[str, Any]:
        inputs = result.get("inputs") or {}
        return self.request_human_action(
            session_id=session_id,
            prompt=_manual_fix_prompt_from_result(result),
            options=[
                {"id": "continue_after_manual_fix", "label": "人工修正后继续"},
                {"id": "inspect_reason", "label": "查看原因"},
            ],
            risk_level="high",
            related_tool_run_id=str(result.get("session_id") or ""),
            metadata={
                "kind": "formfill_manual_fix",
                "resume_policy": str(inputs.get("auto_advance_policy") or "until_before_final_submit"),
                "include_values": False,
                "max_steps": 20,
            },
        )

    def _update_runtime_state(self, result: dict[str, Any], *, session_id: str | None = None) -> None:
        inputs = result.get("inputs") or {}
        summary = result.get("summary") or {}
        existing_context = {}
        if session_id and session_id in self.sessions and isinstance(self.sessions[session_id].get("task_context"), dict):
            existing_context = self.sessions[session_id]["task_context"]
        excel_path = str(inputs.get("excel_path") or "")
        value_column = str(inputs.get("value_column") or "")
        task_id = str(inputs.get("task_id") or result.get("task_id") or "")
        task_context = {
            "status": "ready" if result.get("ok", True) else str(result.get("status") or "needs_input"),
            "workspace": str(Path(excel_path).parent) if excel_path else str(existing_context.get("workspace") or ""),
            "master_workbook": excel_path or str(existing_context.get("master_workbook") or ""),
            "mapping_workbook": str(existing_context.get("mapping_workbook") or ""),
            "target_column": f"{value_column}列" if value_column else str(existing_context.get("target_column") or ""),
            "target_tool": _task_title(task_id) if task_id else str(existing_context.get("target_tool") or ""),
            "message": _task_context_message(result),
        }
        review_summary = _review_summary_from_result(result)
        execution_status = _execution_status_from_result(result)
        self._task_context = task_context
        self._review_summary = review_summary
        self._execution_status = execution_status
        if session_id and session_id in self.sessions:
            self.sessions[session_id]["task_context"] = task_context
            self.sessions[session_id]["review_summary"] = review_summary
            self.sessions[session_id]["execution_status"] = execution_status
            self.sessions[session_id]["updated_at"] = _utc_now()

    def request_human_action(
        self,
        *,
        session_id: str,
        prompt: str,
        options: list[dict[str, str]],
        risk_level: str = "medium",
        related_tool_run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if session_id not in self.sessions:
            raise ValueError(f"Unknown session_id: {session_id}")
        action = {
            "type": "human_action",
            "action_id": f"action_{uuid.uuid4().hex}",
            "session_id": session_id,
            "prompt": prompt,
            "options": [{"id": str(item["id"]), "label": str(item["label"])} for item in options],
            "risk_level": risk_level,
            "related_tool_run_id": related_tool_run_id,
            "metadata": dict(metadata or {}),
            "status": "pending",
            "created_at": _utc_now(),
            "resolved_at": "",
            "selected_option_id": "",
        }
        self.actions[action["action_id"]] = action
        self._append_event(session_id, "human_action_requested", action)
        self._write_audit_record("human_action_requested", action)
        return action

    def respond_to_human_action(self, action_id: str, selected_option_id: str) -> dict[str, Any]:
        action = self.actions.get(action_id)
        if action is None:
            raise ValueError(f"Unknown action_id: {action_id}")
        allowed = {option["id"] for option in action["options"]}
        if selected_option_id not in allowed:
            raise ValueError(f"Unknown option for action {action_id}: {selected_option_id}")
        action = {**action, "status": "resolved", "resolved_at": _utc_now(), "selected_option_id": selected_option_id}
        self.actions[action_id] = action
        self.active_session_id = str(action["session_id"])
        self._append_event(action["session_id"], "human_action_resolved", action)
        self._write_audit_record("human_action_resolved", action)
        if selected_option_id == "continue_after_manual_fix":
            return self._continue_after_manual_fix(action)
        if selected_option_id == "inspect_reason":
            assistant = {
                "status": "human_action_info",
                "content": f"当前阻塞原因：{action['prompt']}",
            }
            self._append_event(action["session_id"], "assistant_status", assistant)
            return {**action, "ok": True, "session_id": action["session_id"], "action": action, "assistant": assistant, "bootstrap": self.bootstrap(action["session_id"])}
        return {**action, "ok": True, "session_id": action["session_id"], "action": action, "bootstrap": self.bootstrap(action["session_id"])}

    def _continue_after_manual_fix(self, action: dict[str, Any]) -> dict[str, Any]:
        session_id = str(action["session_id"])
        fill_session_id = str(action.get("related_tool_run_id") or "").strip()
        metadata = action.get("metadata") if isinstance(action.get("metadata"), dict) else {}
        policy = str(metadata.get("resume_policy") or "until_before_final_submit")
        include_values = bool(metadata.get("include_values", False))
        max_steps = int(metadata.get("max_steps") or 20)
        if not fill_session_id:
            assistant = {
                "status": "manual_resume_unavailable",
                "content": "无法继续：这个人工动作缺少 FormFill 会话编号，请重新发起填报任务。",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {**action, "ok": True, "session_id": session_id, "action": action, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}

        self.refresh_controlled_chrome("before_fill_tool")
        self._append_event(
            session_id,
            "formfill_tool_started",
            {
                "tool": "formfill_resume_after_manual_fix",
                "task_id": "",
                "session_id": fill_session_id,
                "policy": policy,
            },
        )
        try:
            result = self._formfill_harness().resume_after_manual_fix(
                fill_session_id,
                policy=policy,
                include_values=include_values,
                max_steps=max_steps,
            )
        except Exception as exc:
            assistant = {
                "status": getattr(exc, "code", "formfill_error"),
                "content": f"FormFill 继续执行失败：{getattr(exc, 'message', str(exc))}",
            }
            self._append_event(session_id, "assistant_status", assistant)
            return {**action, "ok": True, "session_id": session_id, "action": action, "assistant": assistant, "bootstrap": self.bootstrap(session_id)}

        response = self._record_fill_result(session_id, result)
        return {
            **action,
            "ok": True,
            "session_id": session_id,
            "action": action,
            "assistant": response["assistant"],
            "fill_result": response["fill_result"],
            "bootstrap": response["bootstrap"],
        }

    def session_events(self, session_id: str) -> list[dict[str, Any]]:
        return list(self.events.get(session_id, []))

    def cancel_agent_turn(self, session_id: str | None = None, client_turn_id: str | None = None) -> dict[str, Any]:
        requested_session_id = str(session_id or "").strip()
        turn_id = _safe_client_turn_id(client_turn_id)
        with self._agent_turn_lock:
            if not turn_id and requested_session_id:
                turn_id = self._active_agent_turns_by_session.get(requested_session_id, "")
            if not turn_id:
                return {"ok": True, "cancelled": False, "message": "没有正在运行的 Agent 回合。"}
            already_cancelled = turn_id in self._cancelled_agent_turns
            self._cancelled_agent_turns.add(turn_id)
            known_session_id = self._active_agent_turn_sessions.get(turn_id) or (
                requested_session_id if requested_session_id in self.sessions else ""
            )
        if known_session_id and not already_cancelled:
            self._append_event(
                known_session_id,
                "agent_turn_cancel_requested",
                {"client_turn_id": turn_id},
            )
        return {
            "ok": True,
            "cancelled": True,
            "session_id": known_session_id or requested_session_id,
            "client_turn_id": turn_id,
        }

    def validate_fill_task_current_step(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_controlled_chrome("before_fill_tool")
        return _pending_fill_result(task_id, "validate_current_step")

    def prepare_fill_task(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_controlled_chrome("before_fill_tool")
        return _pending_fill_result(task_id, "prepare")

    def confirm_fill_task_current_step(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.refresh_controlled_chrome("before_fill_tool")
        return _pending_fill_result(task_id, "confirm_current_step")

    def _append_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = {"id": f"evt_{uuid.uuid4().hex}", "type": event_type, "created_at": _utc_now(), "payload": payload}
        self.events.setdefault(session_id, []).append(event)
        if session_id in self.sessions:
            self.sessions[session_id]["updated_at"] = event["created_at"]
        self._append_session_message_from_event(session_id, event_type, payload)
        self._persist_session(session_id)
        for sink in list(self._event_sinks):
            sink(session_id, event)

    def _emit_live_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        event = {"id": f"evt_{uuid.uuid4().hex}", "type": event_type, "created_at": _utc_now(), "payload": payload}
        for sink in list(self._event_sinks):
            sink(session_id, event)

    def _add_event_sink(self, sink: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._event_sinks.append(sink)

        def remove() -> None:
            if sink in self._event_sinks:
                self._event_sinks.remove(sink)

        return remove

    def _begin_agent_turn(self, session_id: str, turn_id: str) -> None:
        with self._agent_turn_lock:
            self._active_agent_turns_by_session[session_id] = turn_id
            self._active_agent_turn_sessions[turn_id] = session_id

    def _finish_agent_turn(self, session_id: str, turn_id: str) -> None:
        with self._agent_turn_lock:
            if self._active_agent_turns_by_session.get(session_id) == turn_id:
                self._active_agent_turns_by_session.pop(session_id, None)
            self._active_agent_turn_sessions.pop(turn_id, None)
            self._cancelled_agent_turns.discard(turn_id)

    def _is_agent_turn_cancelled(self, turn_id: str) -> bool:
        with self._agent_turn_lock:
            return turn_id in self._cancelled_agent_turns

    def _agent_turn_cancelled_response(self, session_id: str, turn_id: str) -> dict[str, Any]:
        assistant = {
            "status": "cancelled",
            "content": "已停止当前 Agent 回合。",
        }
        self._append_event(session_id, "assistant_status", assistant)
        return {
            "ok": True,
            "cancelled": True,
            "session_id": session_id,
            "client_turn_id": turn_id,
            "assistant": assistant,
            "bootstrap": self.bootstrap(session_id),
        }

    def _pending_action_count(self) -> int:
        return sum(1 for action in self.actions.values() if action.get("status") == "pending")

    def _pending_continue_action(self, session_id: str) -> dict[str, Any] | None:
        actions = [
            action
            for action in self.actions.values()
            if action.get("session_id") == session_id
            and action.get("status") == "pending"
            and any(option.get("id") == "continue_after_manual_fix" for option in action.get("options") or [])
        ]
        if not actions:
            return None
        actions.sort(key=lambda item: str(item.get("created_at") or ""))
        return actions[-1]

    def _append_session_message_from_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        role = ""
        content = ""
        if event_type == "message":
            role = str(payload.get("role") or "")
            content = str(payload.get("content") or "")
        elif event_type == "assistant_status":
            role = "assistant"
            content = str(payload.get("content") or "")
        if role not in {"user", "assistant"} or not content.strip():
            return
        session = self.sessions.get(session_id)
        if session is None:
            return
        messages = session.setdefault("messages", [])
        messages.append({"role": role, "content": content, "created_at": _utc_now()})
        self._compact_session_messages(session)

    def _session_history_for_model(self, session_id: str, current_content: str) -> list[dict[str, str]]:
        session = self.sessions.get(session_id) or {}
        messages = [
            {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
            for item in session.get("messages") or []
            if str(item.get("role") or "") in {"user", "assistant"} and str(item.get("content") or "").strip()
        ]
        if messages and messages[-1]["role"] == "user" and messages[-1]["content"] == current_content:
            messages = messages[:-1]
        summary = _context_summary_text(session)
        if not summary:
            return messages[-10:]
        return [{"role": "system", "content": f"本会话较早上下文摘要（自动压缩）：\n{summary}"}] + messages[-9:]

    def _compact_session_messages(self, session: dict[str, Any]) -> None:
        messages = session.get("messages")
        if not isinstance(messages, list) or len(messages) <= SESSION_MESSAGE_COMPACT_THRESHOLD:
            return
        older = [item for item in messages[:-SESSION_MESSAGE_RECENT_LIMIT] if isinstance(item, dict)]
        recent = [item for item in messages[-SESSION_MESSAGE_RECENT_LIMIT:] if isinstance(item, dict)]
        if not older:
            return
        existing = _context_summary_text(session)
        addition = _summarize_messages_for_context(older)
        summary = _trim_context_summary("\n".join(part for part in [existing, addition] if part.strip()))
        try:
            previous_source_count = int((session.get("context_summary") or {}).get("source_message_count") or 0)
        except (TypeError, ValueError):
            previous_source_count = 0
        source_count = previous_source_count + len(older)
        session["context_summary"] = {
            "content": summary,
            "source_message_count": source_count,
            "updated_at": _utc_now(),
        }
        session["messages"] = recent

    def _load_stored_sessions(self) -> None:
        if not self.session_store_dir.exists():
            return
        for path in sorted(self.session_store_dir.glob("*.json")):
            try:
                bundle = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                schema_version = int(bundle.get("schema_version") or 0)
            except (TypeError, ValueError):
                continue
            if schema_version > SESSION_SCHEMA_VERSION:
                continue
            session = bundle.get("session")
            if not isinstance(session, dict):
                continue
            session_id = str(session.get("session_id") or "")
            try:
                self._session_file_path(session_id)
            except ValueError:
                continue
            self.sessions[session_id] = _normalize_session(session)
            self.events[session_id] = [event for event in bundle.get("events") or [] if isinstance(event, dict)]
            for action in bundle.get("actions") or []:
                if isinstance(action, dict) and action.get("action_id"):
                    self.actions[str(action["action_id"])] = action
        self.active_session_id = self._latest_active_session_id()

    def _persist_session(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        self.session_store_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_file_path(session_id)
        bundle = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session": session,
            "events": self.events.get(session_id, []),
            "actions": [action for action in self.actions.values() if action.get("session_id") == session_id],
        }
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _delete_session_file(self, session_id: str) -> None:
        try:
            path = self._session_file_path(session_id)
        except ValueError:
            return
        if path.exists():
            path.unlink()

    def _session_file_path(self, session_id: str) -> Path:
        trimmed = str(session_id or "").strip()
        if not trimmed or not all(char.isascii() and (char.isalnum() or char in {"_", "-"}) for char in trimmed):
            raise ValueError(f"Invalid session_id: {session_id}")
        return self.session_store_dir / f"{trimmed}.json"

    def _latest_active_session_id(self) -> str:
        active_sessions = [
            session
            for session in self.sessions.values()
            if str(session.get("status") or "active") == "active"
        ]
        if not active_sessions:
            return ""
        active_sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return str(active_sessions[0].get("session_id") or "")

    def _write_audit_record(self, event_type: str, payload: dict[str, Any]) -> None:
        audit_dir = self.state_dir / "sessions"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / f"{payload['session_id']}.jsonl"
        record = {"type": event_type, "created_at": _utc_now(), "payload": payload}
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _selected_session(self, session_id: str | None = None) -> dict[str, Any] | None:
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            if str(session.get("status") or "active") == "active":
                self.active_session_id = session_id
            return session
        if self.active_session_id and self.active_session_id in self.sessions and str(self.sessions[self.active_session_id].get("status") or "active") == "active":
            return self.sessions[self.active_session_id]
        self.active_session_id = self._latest_active_session_id()
        if self.active_session_id:
            return self.sessions[self.active_session_id]
        return None

    def _require_chat_session(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"Unknown session_id: {session_id}")
        return session

    def _maybe_title_from_first_user_message(self, session_id: str, content: str) -> None:
        session = self.sessions.get(session_id)
        if session is None:
            return
        title = str(session.get("title") or "")
        has_user_message = any(
            event.get("type") == "message" and (event.get("payload") or {}).get("role") == "user"
            for event in self.events.get(session_id, [])
        )
        if has_user_message or not title.startswith("新会话"):
            return
        cleaned = _clean_session_title(content)
        if cleaned:
            session["title"] = cleaned[:28]


def _empty_context_summary() -> dict[str, Any]:
    return {"content": "", "source_message_count": 0, "updated_at": ""}


def _default_controlled_chrome_probe(reason: str) -> list[dict[str, Any]]:
    del reason
    return []


def _default_controlled_chrome_launcher() -> dict[str, Any]:
    return {
        "message": "公开作品版使用模拟填报适配器，不启动受控 Chrome。",
        "browser_key": "portfolio-demo",
    }


def _safe_controlled_chrome_launch_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    allowed = ("pid", "remote_debugging_port", "browser_key", "message")
    return {key: result[key] for key in allowed if key in result}


def _operator_tool_readiness(tools: list[dict[str, Any]]) -> dict[str, Any]:
    available_count = sum(1 for tool in tools if str(tool.get("status") or "") == "available")
    pending_count = sum(1 for tool in tools if str(tool.get("status") or "") == "pending_integration")
    if available_count and pending_count:
        status = "partial"
        label = "工具部分就绪"
    elif available_count:
        status = "available"
        label = "工具已就绪"
    else:
        status = "pending_integration"
        label = "工具待接入"
    return {
        "status": status,
        "label": label,
        "available_count": available_count,
        "pending_count": pending_count,
    }


def _controlled_chrome_status_from_entries(
    entries: list[dict[str, Any]],
    *,
    reason: str,
    error: str = "",
) -> dict[str, Any]:
    active = [_normalize_controlled_chrome_entry(entry) for entry in entries]
    active = [entry for entry in active if entry]
    connected = bool(active)
    return {
        "status": "connected" if connected else "missing",
        "label": CONTROLLED_CHROME_CONNECTED_LABEL if connected else CONTROLLED_CHROME_MISSING_LABEL,
        "active": active,
        "active_count": len(active),
        "reason": reason,
        "error": error,
    }


def _normalize_controlled_chrome_entry(entry: Any) -> dict[str, Any]:
    if hasattr(entry, "to_payload"):
        entry = entry.to_payload()
    if not isinstance(entry, dict):
        return {}
    debug_port = entry.get("debug_port")
    connected = bool(entry.get("connected", bool(debug_port)))
    if not connected:
        return {}
    return {
        "key": str(entry.get("key") or entry.get("task_id") or ""),
        "task_id": str(entry.get("task_id") or entry.get("key") or ""),
        "title": str(entry.get("title") or ""),
        "debug_port": debug_port,
        "last_url": str(entry.get("last_url") or ""),
        "profile_dir": str(entry.get("profile_dir") or ""),
    }


def _with_controlled_chrome_status(execution_status: dict[str, Any], chrome_status: dict[str, Any]) -> dict[str, Any]:
    result = dict(execution_status)
    connected = str(chrome_status.get("status") or "") == "connected"
    result["chrome"] = {
        "label": "Chrome",
        "status": "available" if connected else "missing",
        "value": str(chrome_status.get("label") or CONTROLLED_CHROME_MISSING_LABEL),
    }
    return result


def _empty_checklist() -> dict[str, Any]:
    return {
        "status": "pending_integration",
        "columns": ["参数", "当前值", "标准值"],
        "rows": [],
        "message": "等待法规解读结果接入后生成参数对照清单。",
    }


def _normalize_session(session: dict[str, Any]) -> dict[str, Any]:
    now = _utc_now()
    normalized = dict(session)
    normalized["session_id"] = str(normalized.get("session_id") or "")
    normalized["operator_label"] = str(normalized.get("operator_label") or "")
    normalized["title"] = _clean_session_title(str(normalized.get("title") or "")) or "未命名会话"
    normalized["created_at"] = str(normalized.get("created_at") or now)
    normalized["updated_at"] = str(normalized.get("updated_at") or normalized["created_at"])
    normalized["status"] = "archived" if str(normalized.get("status") or "").lower() == "archived" else "active"
    normalized["archived_at"] = str(normalized.get("archived_at") or "")
    normalized["messages"] = [item for item in normalized.get("messages") or [] if isinstance(item, dict)]
    normalized["task_context"] = normalized.get("task_context") if isinstance(normalized.get("task_context"), dict) else _empty_task_context()
    normalized["execution_status"] = normalized.get("execution_status") if isinstance(normalized.get("execution_status"), dict) else _empty_execution_status()
    normalized["review_summary"] = normalized.get("review_summary") if isinstance(normalized.get("review_summary"), dict) else _empty_review_summary()
    normalized["checklist"] = _normalize_checklist(normalized.get("checklist"))
    context_summary = normalized.get("context_summary") if isinstance(normalized.get("context_summary"), dict) else {}
    normalized["context_summary"] = {
        "content": str(context_summary.get("content") or ""),
        "source_message_count": int(context_summary.get("source_message_count") or 0),
        "updated_at": str(context_summary.get("updated_at") or ""),
    }
    return normalized


def _normalize_checklist(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    rows = []
    for item in data.get("rows") or []:
        if not isinstance(item, dict):
            continue
        parameter = _single_line(str(item.get("parameter") or item.get("参数") or item.get("name") or ""))
        current_value = _single_line(str(item.get("current_value") or item.get("currentValue") or item.get("当前值") or ""))
        standard_value = _single_line(str(item.get("standard_value") or item.get("standardValue") or item.get("标准值") or ""))
        if not any([parameter, current_value, standard_value]):
            continue
        rows.append(
            {
                "parameter": parameter,
                "current_value": current_value,
                "standard_value": standard_value,
            }
        )
    checklist = _empty_checklist()
    checklist["status"] = str(data.get("status") or ("available" if rows else "pending_integration"))
    checklist["rows"] = rows
    checklist["message"] = str(data.get("message") or checklist["message"])
    return checklist


def _context_summary_text(session: dict[str, Any]) -> str:
    context_summary = session.get("context_summary")
    if not isinstance(context_summary, dict):
        return ""
    return str(context_summary.get("content") or "").strip()


def _summarize_messages_for_context(messages: list[dict[str, Any]]) -> str:
    sampled = messages[-12:]
    lines = [f"已压缩 {len(messages)} 条较早消息，保留以下关键对话线索："]
    omitted = len(messages) - len(sampled)
    if omitted > 0:
        lines.append(f"- 更早还有 {omitted} 条消息已并入本摘要。")
    for item in sampled:
        role = "用户" if str(item.get("role") or "") == "user" else "RegPilot"
        content = _single_line(str(item.get("content") or ""))
        if content:
            lines.append(f"- {role}: {_shorten_text(content, 180)}")
    return "\n".join(lines)


def _trim_context_summary(summary: str) -> str:
    text = summary.strip()
    if len(text) <= SESSION_SUMMARY_CHAR_LIMIT:
        return text
    marker = "（较早摘要已再次压缩，保留最近上下文片段。）\n"
    return marker + text[-(SESSION_SUMMARY_CHAR_LIMIT - len(marker)) :].lstrip()


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _shorten_text(value: str, limit: int) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _pending_fill_result(task_id: str, operation: str) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "pending_integration",
        "status": "pending_integration",
        "task_id": task_id,
        "operation": operation,
        "can_fill": False,
        "written_count": 0,
        "message": "上层接口已定义；等待底层 Fill Service 对齐后接入，当前不会执行或伪造填报结果。",
    }


def _build_harness_request(content: str, model_intent: dict[str, Any] | None = None) -> Any | None:
    from .formfill_bridge import HarnessRequest, parse_user_intent

    intent = parse_user_intent(content)
    task_id = (model_intent or {}).get("task_id") or intent.task_id
    if not task_id:
        return None
    workbook_path, workspace_dir = _extract_path_inputs(content)
    workbook_path = (model_intent or {}).get("workbook_path") or workbook_path
    workspace_dir = (model_intent or {}).get("workspace_dir") or workspace_dir
    value_column = (model_intent or {}).get("value_column") or intent.value_column
    if not value_column and workbook_path and task_id == "shanghaiData_fill" and model_intent is None:
        value_column = "C"
    sheet = (model_intent or {}).get("sheet") or _default_sheet_for_task(task_id, workbook_path)
    attachment_folder = (model_intent or {}).get("attachment_folder")
    policy = (model_intent or {}).get("auto_advance_policy") or intent.auto_advance_policy
    return HarnessRequest(
        user_message=content,
        workspace_dir=workspace_dir,
        task_id=task_id,
        workbook_path=workbook_path,
        sheet=sheet,
        value_column=value_column,
        attachment_folder=attachment_folder,
        auto_advance_policy=policy,
        include_values=False,
        max_steps=20,
    )


def _extract_path_inputs(content: str) -> tuple[str | None, str | None]:
    path_text = _extract_path_text(content)
    if not path_text:
        return None, None
    cleaned = path_text.strip().strip("\"'“”")
    suffix = Path(cleaned).suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return cleaned, None
    return None, cleaned


def _extract_path_text(content: str) -> str | None:
    patterns = (
        r"[<《](?P<path>[^<>《》]+?\.xls(?:x|m)?)[>》]",
        r"[\"“](?P<path>[^\"”]+?\.xls(?:x|m)?)[\"”]",
        r"(?P<path>[A-Za-z]:[^\s，,；;<>\"'“”]+?\.xls(?:x|m)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            return match.group("path")
    return None


def _default_sheet_for_task(task_id: str, workbook_path: str | None) -> str | None:
    if task_id == "shanghaiData_fill":
        return "SHGL备案参数"
    if task_id == "landmark_fill":
        return "SHGL备案参数"
    if task_id == "ota_fill":
        return "REEV车型及功能备案细分"
    return None


def _empty_task_context() -> dict[str, Any]:
    return {
        "status": "pending_integration",
        "workspace": "",
        "master_workbook": "",
        "mapping_workbook": "",
        "target_column": "",
        "target_tool": "",
        "message": "等待用户在聊天中指定填报任务。",
    }


def _empty_execution_status() -> dict[str, Any]:
    return {
        "chrome": {"label": "Chrome", "status": "missing", "value": CONTROLLED_CHROME_MISSING_LABEL},
        "current_page": {"label": "当前页", "status": "idle", "value": "待执行"},
        "page_fingerprint": {"label": "页面指纹", "status": "idle", "value": "待执行"},
        "auto_next_step": {"label": "自动下一步", "status": "enabled", "value": "停在提交前"},
        "final_stop": {"label": "最终停留", "status": "idle", "value": "未提交 / 未保存"},
    }


def _empty_review_summary() -> dict[str, Any]:
    return {
        "status": "idle",
        "pages": [],
        "totals": {"green": 0, "yellow": 0, "red": 0},
        "message": "等待底层验证结果。",
    }


def _session_view(session: dict[str, Any], key: str) -> dict[str, Any]:
    value = session.get(key)
    if isinstance(value, dict):
        return dict(value)
    if key == "task_context":
        return _empty_task_context()
    if key == "execution_status":
        return _empty_execution_status()
    if key == "review_summary":
        return _empty_review_summary()
    return {}


def _public_session(session: dict[str, Any] | None) -> dict[str, Any]:
    if not session:
        return {}
    context_summary = session.get("context_summary") if isinstance(session.get("context_summary"), dict) else {}
    source_message_count = int(context_summary.get("source_message_count") or 0)
    recent_message_count = len(session.get("messages") or [])
    return {
        "session_id": str(session.get("session_id") or ""),
        "title": str(session.get("title") or "未命名会话"),
        "status": str(session.get("status") or "active"),
        "created_at": str(session.get("created_at") or ""),
        "updated_at": str(session.get("updated_at") or ""),
        "archived_at": str(session.get("archived_at") or ""),
        "message_count": source_message_count + recent_message_count,
        "recent_message_count": recent_message_count,
        "has_context_summary": bool(str(context_summary.get("content") or "").strip()),
    }


def _clean_session_title(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    return text[:60]


def _review_summary_from_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") or {}
    traffic = summary.get("traffic_light") or {}
    totals = {
        "green": int(traffic.get("green_count") or traffic.get("green") or 0),
        "yellow": int(traffic.get("yellow_count") or traffic.get("yellow") or 0),
        "red": int(traffic.get("red_count") or traffic.get("red") or 0),
    }
    label = str(summary.get("step_title") or result.get("status") or "当前停留页")
    return {
        "status": str(result.get("status") or summary.get("status") or ""),
        "pages": [{"label": label, **totals}] if any(totals.values()) else [],
        "totals": totals,
        "message": _review_message(result),
    }


def _review_summary_from_error(message: str) -> dict[str, Any]:
    return {
        "status": "formfill_error",
        "pages": [],
        "totals": {"green": 0, "yellow": 0, "red": 0},
        "message": message,
    }


def _execution_status_from_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") or {}
    status = str(result.get("status") or summary.get("status") or "")
    final_value = "已停在提交前" if status == "final_review" else _final_status_text(result)
    return {
        "chrome": {"label": "Chrome", "status": "available", "value": CONTROLLED_CHROME_CONNECTED_LABEL},
        "current_page": {"label": "当前页", "status": status or "unknown", "value": str(summary.get("step_title") or status or "未知")},
        "page_fingerprint": {"label": "页面指纹", "status": "available", "value": str(summary.get("step_id") or "FormFill Session")},
        "auto_next_step": {"label": "自动下一步", "status": "enabled", "value": "已启用，停在提交前"},
        "final_stop": {"label": "最终停留", "status": status or "unknown", "value": final_value},
    }


def _safe_fill_event(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status", ""),
        "task_id": result.get("task_id", ""),
        "session_id": result.get("session_id", ""),
        "recommended_next_action": result.get("recommended_next_action", ""),
        "human_handoff_required": bool(result.get("human_handoff_required", False)),
        "summary": result.get("summary") or {},
    }


def _safe_skill_command_event(command: dict[str, Any]) -> dict[str, Any]:
    inputs = command.get("inputs") if isinstance(command.get("inputs"), dict) else {}
    return {
        "type": str(command.get("type") or "skill_command"),
        "skill_id": str(command.get("skill_id") or ""),
        "skill_title": str(command.get("skill_title") or ""),
        "goal": str(command.get("goal") or ""),
        "run_policy": str(command.get("run_policy") or ""),
        "regulatory_tool": str(command.get("regulatory_tool") or ""),
        "task_id": str(inputs.get("task_id") or ""),
        "intent_source": str(command.get("intent_source") or "rules"),
    }


def _assistant_tool_call_message(turn: Any) -> dict[str, Any]:
    message = {
        "role": "assistant",
        "content": str(getattr(turn, "content", "") or ""),
        "tool_calls": [
            {
                "id": str(call.id),
                "type": "function",
                "function": {
                    "name": str(call.name),
                    "arguments": str(call.raw_arguments or "{}"),
                },
            }
            for call in getattr(turn, "tool_calls", [])
        ],
    }
    reasoning_content = str(getattr(turn, "reasoning_content", "") or "")
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    return message


def _tool_result_message(tool_call: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.id),
        "name": str(tool_call.name),
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _skill_list_tool_result(skills: list[dict[str, Any]]) -> dict[str, Any]:
    display_skills = []
    for skill in operator_skill_list(skills):
        description = _compact_display_text(skill.get("description"), fallback="可用业务 Skill。")
        display_skills.append(
            {
                "routing_id": str(skill.get("id") or ""),
                "title": str(skill.get("title") or ""),
                "display_name": str(skill.get("display_name") or skill.get("title") or ""),
                "source_title": str(skill.get("source_title") or skill.get("title") or ""),
                "summary": description,
                "status": str(skill.get("status") or "available"),
                "requires_confirmation": bool(skill.get("requires_confirmation", False)),
            }
        )
    return {
        "ok": True,
        "skills": display_skills,
        "presentation": {
            "audience": "法规人员",
            "style": "用简短中文回答，优先项目符号；不要使用 Markdown 表格或 emoji。",
            "show": ["display_name", "summary"],
            "hide_unless_asked": ["routing_id", "source_title", "status", "requires_confirmation", "tool schema", "operation nodes", "manifest fields"],
        },
    }


def _compact_display_text(value: Any, *, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return fallback
    parts = re.split(r"(?<=[。！？.!?])\s*", text, maxsplit=1)
    text = (parts[0] or text).strip()
    return text[:96].rstrip() + ("..." if len(text) > 96 else "")


def _safe_turn_decision_event(decision: dict[str, Any]) -> dict[str, Any]:
    if decision.get("type") == "skill_command":
        return _safe_skill_command_event(decision)
    return {
        "type": str(decision.get("type") or ""),
        "question_count": len(decision.get("questions") or []),
        "has_content": bool(str(decision.get("content") or "").strip()),
    }


def _intent_from_skill_command(command: dict[str, Any]) -> dict[str, Any]:
    inputs = command.get("inputs") if isinstance(command.get("inputs"), dict) else {}
    return {
        "task_id": str(inputs.get("task_id") or ""),
        "workbook_path": _optional_input(inputs.get("workbook_path")),
        "workspace_dir": _optional_input(inputs.get("workspace_dir")),
        "sheet": _optional_input(inputs.get("sheet")),
        "value_column": _optional_input(inputs.get("value_column")),
        "attachment_folder": _optional_input(inputs.get("attachment_folder")),
        "auto_advance_policy": str(inputs.get("auto_advance_policy") or command.get("run_policy") or "until_before_final_submit"),
        "intent_source": str(command.get("intent_source") or "model_turn_decision"),
    }


def _assistant_from_turn_decision(decision: dict[str, Any]) -> dict[str, str]:
    decision_type = str(decision.get("type") or "")
    if decision_type == "input_request":
        questions = "；".join(str(item) for item in decision.get("questions") or [] if str(item).strip())
        return {"status": "needs_input", "content": questions or "请补充填报所需信息。"}
    if decision_type == "chat":
        return {"status": "chat", "content": str(decision.get("content") or "好的。")}
    return {"status": "no_action", "content": "没有识别到需要调用的受控工具。"}


def _optional_input(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _event_attr(event: Any, key: str) -> str:
    if isinstance(event, dict):
        return str(event.get(key) or "")
    return str(getattr(event, key, "") or "")


def _event_payload(event: Any) -> dict[str, Any]:
    raw_payload = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", {})
    if not isinstance(raw_payload, dict):
        return {}
    try:
        payload = json.loads(json.dumps(raw_payload, ensure_ascii=False))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_argument(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_client_turn_id(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 96:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return ""
    return text


def _assistant_from_fill_result(result: dict[str, Any]) -> dict[str, str]:
    status = str(result.get("status") or "")
    if status == "needs_input" or not result.get("ok", True):
        questions = "；".join(str(item) for item in result.get("questions", []) if str(item).strip())
        message = str(result.get("message") or "需要补充或确认填报输入后才能继续。")
        return {"status": status or "needs_input", "content": f"{message}{(' ' + questions) if questions else ''}"}
    if result.get("human_handoff_required"):
        return {
            "status": status,
            "content": f"已执行到需要人工处理的位置：{_human_prompt_from_result(result)}",
        }
    if status == "final_review":
        return {"status": status, "content": "上海数据平台自动填报已推进到提交前；工具未保存、未提交。"}
    return {"status": status or "completed", "content": _review_message(result)}


def _human_prompt_from_result(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    items = summary.get("blocking_items") or []
    if items:
        first = items[0]
        label = str(first.get("label") or "当前页")
        message = str(first.get("message") or "需要人工处理后继续。")
        return f"{label}：{message}"
    return str(result.get("message") or "检测到阻碍项，需要人工处理后继续。")


def _manual_fix_prompt_from_result(result: dict[str, Any]) -> str:
    reason = _human_prompt_from_result(result)
    return (
        f"{reason}\n"
        "请在当前页修正上述阻塞项；修正后不要手动点击下一步或确认。"
        "修正完成后点击“人工修正后继续”，RegPilot 会重新验证当前页，并在允许时自动点击下一步/确认继续。"
    )


def _task_context_message(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    if status == "final_review":
        return "已自动推进到最终提交前。"
    if status == "needs_input":
        return str(result.get("message") or "等待补充输入。")
    if result.get("human_handoff_required"):
        return "当前存在阻碍项，等待人工处理。"
    return _review_message(result)


def _review_message(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    if result.get("human_handoff_required"):
        return _human_prompt_from_result(result)
    if str(result.get("status") or "") == "final_review":
        return "已停在提交前，等待人工最终复核。"
    return str(result.get("message") or summary.get("recommended_next_action") or result.get("status") or "填报任务已返回结果。")


def _final_status_text(result: dict[str, Any]) -> str:
    if result.get("human_handoff_required"):
        return "等待人工处理"
    if str(result.get("status") or "") == "needs_input":
        return "等待补充输入"
    return "未提交 / 未保存"


def _task_title(task_id: str) -> str:
    return {
        "shanghaiData_fill": "上海数据平台",
        "landmark_fill": "地标填报",
        "ota_fill": "OTA平台",
    }.get(task_id, task_id)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assistant_start_message(intent: dict[str, Any] | None) -> str:
    if intent and intent.get("intent_source") == "model":
        return "大模型已解析出受控填报任务，开始调用 FormFill 自动验证并填写到提交前。"
    return "已识别到受控填报任务，开始调用 FormFill 自动验证并填写到提交前。"


def _is_continue_after_manual_fix_message(content: str) -> bool:
    text = re.sub(r"\s+", "", str(content or "")).strip()
    if not text:
        return False
    explicit = {
        "继续",
        "继续执行",
        "接着来",
        "好了继续",
        "修好了",
        "已修正",
        "修正好了",
        "人工修正后继续",
        "人工修复后继续",
    }
    return text in explicit or text.startswith("继续")


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}
