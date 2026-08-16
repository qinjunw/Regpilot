from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticLayoutTests(unittest.TestCase):
    def test_static_branding_uses_regpilot(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("<title>RegPilot</title>", html)
        self.assertIn("<h1>RegPilot</h1>", html)
        self.assertNotIn("汽车法规 Agent</h1>", html)

    def test_brand_and_chat_avatars_use_image_assets(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        for asset_name in ("regpilot-logo.png", "agent-avatar.png", "user-avatar.png"):
            self.assertTrue((ROOT / "static" / "assets" / asset_name).exists(), asset_name)
            self.assertEqual(_png_size(ROOT / "static" / "assets" / asset_name), (512, 512))
        self.assertIn('<div class="brand-mark" aria-hidden="true"><img src="/static/assets/regpilot-logo.png" alt=""></div>', html)
        self.assertIn('agent: "/static/assets/agent-avatar.png"', js)
        self.assertIn('user: "/static/assets/user-avatar.png"', js)
        self.assertIn('function avatarMarkup(role)', js)
        self.assertIn('<div class="avatar" aria-hidden="true"><img src="${src}" alt=""></div>', js)
        self.assertNotIn('<div class="avatar">A</div>', js)
        self.assertNotIn('<div class="avatar">${role === "agent" ? "A" : "你"}</div>', js)
        self.assertIn("grid-template-columns: 48px auto 1fr auto", _block(css, ".topbar"))
        self.assertIn("grid-template-columns: 48px minmax(0, 1fr)", _block(css, ".message"))
        self.assertIn("object-fit: cover", _block(css, ".brand-mark img"))
        self.assertIn("border-radius: 50%", _block(css, ".avatar"))
        self.assertIn("border-radius: 50%", _block(css, ".avatar img"))

    def test_release_build_uses_regpilot_exe_icon(self) -> None:
        script = (ROOT / "tools" / "build_regpilot_package.ps1").read_text(encoding="utf-8")

        self.assertTrue((ROOT / "resources" / "regpilot.ico").exists())
        self.assertIn((256, 256), _ico_sizes(ROOT / "resources" / "regpilot.ico"))
        self.assertIn('$AppIcon = Join-Path $RegPilotRoot "resources\\regpilot.ico"', script)
        self.assertIn("--icon $AppIcon", script)

    def test_release_build_packages_prompt_directory(self) -> None:
        script = (ROOT / "tools" / "build_regpilot_package.ps1").read_text(encoding="utf-8")

        self.assertIn('Join-Path $RegPilotRoot "src\\regulation_agent\\prompts"', script)
        self.assertIn('regulation_agent\\prompts', script)
        self.assertNotIn("regpilot_system.md", script)

    def test_provider_selector_includes_deepseek(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('<option value="deepseek">DeepSeekPro</option>', html)
        self.assertIn('<option value="openai_compatible">OpenAI-compatible</option>', html)
        self.assertNotIn("anthropic_compatible", html)

    def test_settings_panel_exposes_deepseek_specialized_runtime_options(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        for element_id in (
            "request-timeout",
            "deepseek-thinking",
            "deepseek-reasoning-effort",
            "deepseek-max-tokens",
            "deepseek-stream-include-usage",
            "deepseek-user-id",
            "deepseek-strict-tool-schema",
            "deepseek-retry-max-attempts",
            "deepseek-retry-backoff-seconds",
            "deepseek-json-empty-retry-attempts",
        ):
            self.assertIn(f'id="{element_id}"', html)
            self.assertIn(element_id, js)
        self.assertIn("deepseek_thinking", js)
        self.assertIn("deepseek_reasoning_effort", js)
        self.assertIn("deepseek_stream_include_usage", js)
        self.assertIn("deepseek_strict_tool_schema", js)
        self.assertIn("deepseek_retry_backoff_seconds", js)

    def test_deepseek_user_id_is_validated_before_save(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="deepseek-user-id"', html)
        self.assertIn('maxlength="512"', html)
        self.assertIn('pattern="[A-Za-z0-9_-]*"', html)
        self.assertIn("function isValidDeepSeekUserId", js)
        self.assertIn('/^[A-Za-z0-9_-]{1,512}$/.test(text)', js)
        self.assertIn('"deepseek-user-id").focus()', js)

    def test_chat_submit_uses_optimistic_message_and_thinking_state(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('addMessage("user", content, {pending: true, contentKey: content})', js)
        self.assertIn("showThinkingStatus();", js)
        self.assertIn("function confirmPendingUserMessage(content)", js)
        self.assertIn("clearThinkingStatus();", js)
        self.assertIn("RegPilot 正在思考...", js)
        self.assertIn(".thinking-bubble", css)

    def test_model_tool_progress_events_render_as_replaceable_status(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('event.type === "model_tool_call_started"', js)
        self.assertIn('event.type === "model_tool_call_completed"', js)
        self.assertIn("function updateAgentProgress", js)
        self.assertIn("function resetAgentProgress", js)
        self.assertIn("agent-progress-card", js)
        self.assertIn("state.agentProgressHistory = state.agentProgressHistory.slice(-3)", js)
        self.assertIn('renderAgentProgress(quiet ? "正在准备上下文"', js)
        self.assertIn(".agent-progress-card", css)
        self.assertIn("text-overflow: ellipsis", _block(css, ".agent-progress-current,\n.agent-progress-meta"))

    def test_skill_management_tool_names_have_frontend_labels(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        for tool_name in (
            "regpilot_inspect_skill",
            "regpilot_create_skill_draft",
            "regpilot_validate_skill",
            "regpilot_install_skill",
            "regpilot_enable_skill",
            "regpilot_rename_skill",
            "regpilot_stage_regulation_sources",
            "regpilot_record_regulation_entries",
            "regpilot_export_regulation_index",
        ):
            self.assertIn(tool_name, js)
        self.assertIn('regpilot_rename_skill: "重命名 Skill"', js)
        self.assertIn('regpilot_stage_regulation_sources: "登记法规来源"', js)
        self.assertIn('regpilot_record_regulation_entries: "记录法规索引"', js)
        self.assertIn('regpilot_export_regulation_index: "导出法规索引"', js)

    def test_chat_submit_uses_streaming_endpoint_and_assistant_deltas(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('streamChatMessage(content, clientTurnId, controller.signal)', js)
        self.assertIn('/api/chat/messages/stream', js)
        self.assertIn("response.body.getReader()", js)
        self.assertIn('event.type === "assistant_delta"', js)
        self.assertIn("appendStreamingAgentDelta", js)

    def test_model_usage_events_render_as_progress_metadata(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('event.type === "model_usage"', js)
        self.assertIn("function renderModelUsage", js)
        self.assertIn("prompt_cache_hit_tokens", js)
        self.assertIn("prompt_cache_miss_tokens", js)
        self.assertIn("completion_tokens_details?.reasoning_tokens", js)
        self.assertIn('renderAgentProgress("模型流式响应中")', js)

    def test_model_error_status_renders_in_chat_only_from_error_events(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('event.type === "model_tool_turn_failed"', js)
        self.assertIn("function addErrorStatusCard", js)
        self.assertIn("model-error-code", js)
        self.assertIn("model-error-message", js)
        self.assertIn('addErrorStatusCard({code: "stream_error"', js)
        self.assertIn(".error-status-card", css)
        self.assertIn("border-left: 3px solid var(--red)", _block(css, ".error-status-card"))

    def test_send_button_switches_to_stop_during_agent_turn(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function stopCurrentAgentTurn()", js)
        self.assertIn('"/api/chat/turns/cancel"', js)
        self.assertIn("new AbortController()", js)
        self.assertIn("state.currentStreamController.abort()", js)
        self.assertIn('button.classList.toggle("is-stop", state.busy)', js)
        self.assertIn('button.setAttribute("aria-label", state.busy ? (state.stopping ? "正在停止" : "停止") : "发送")', js)
        self.assertIn(".send-button.is-stop", css)

    def test_composer_uses_bounded_multiline_textarea(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('<textarea id="message-input"', html)
        self.assertIn("Shift+Enter 换行", html)
        self.assertIn("function resizeMessageInput()", js)
        self.assertIn('event.key !== "Enter" || event.shiftKey || event.isComposing', js)
        self.assertIn('$("composer").requestSubmit();', js)
        self.assertIn("resizeMessageInput();", js)
        self.assertIn("align-items: end", _block(css, ".composer"))
        input_css = _block(css, "#message-input")
        self.assertIn("min-height: 58px", input_css)
        self.assertIn("max-height: 156px", input_css)
        self.assertIn("overflow-x: hidden", input_css)
        self.assertIn("overflow-y: auto", input_css)
        self.assertIn("resize: none", input_css)
        self.assertIn("white-space: pre-wrap", _block(css, ".message.user .message-content"))

    def test_agent_messages_render_readable_markdown_subset(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("function renderMessageContent(content)", js)
        self.assertIn("function renderMarkdownTable(lines)", js)
        self.assertIn('role === "agent" ? renderMessageContent(content) : escapeHtml(content)', js)
        self.assertIn('class="bubble"><div class="message-content">${body}</div></div>', js)
        self.assertIn(".message-content ul", css)
        self.assertIn(".message-content table", css)
        self.assertIn(".message-heading", css)

    def test_chat_layout_wraps_long_paths_in_narrow_windows(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: 260px minmax(0, 1fr) minmax(360px, 540px)", _block(css, ".workspace-grid"))
        self.assertIn("padding: 22px clamp(14px, 5vw, 76px) 18px", _block(css, ".chat-scroll"))
        self.assertIn("min-width: 0", _block(css, ".message"))
        self.assertIn("overflow-wrap: anywhere", _block(css, ".bubble"))
        self.assertIn("overflow-wrap: anywhere", _block(css, ".message-content"))
        self.assertIn("white-space: pre-wrap", _block(css, ".message-content pre"))

    def test_user_messages_align_to_right(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(0, 1fr) 48px", _block(css, ".message.user"))
        self.assertIn("grid-column: 2", _block(css, ".message.user .avatar"))
        self.assertIn("justify-self: end", _block(css, ".message.user .avatar"))
        self.assertIn("grid-column: 1", _block(css, ".message.user .bubble"))
        self.assertIn("justify-self: end", _block(css, ".message.user .bubble"))

    def test_scrollbars_match_dark_app_chrome(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("--scroll-track", css)
        self.assertIn("--scroll-thumb", css)
        self.assertIn("scrollbar-width: thin", _block(css, "*"))
        self.assertIn("scrollbar-color: var(--scroll-thumb) var(--scroll-track)", _block(css, "*"))
        self.assertIn("width: 10px", _block(css, "*::-webkit-scrollbar"))
        self.assertIn("height: 10px", _block(css, "*::-webkit-scrollbar"))
        self.assertIn("border-radius: 999px", _block(css, "*::-webkit-scrollbar-thumb"))
        self.assertIn("display: none", _block(css, "*::-webkit-scrollbar-button"))

    def test_cold_start_shows_welcome_without_selecting_previous_session(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("我是 RegPilot，你的行业法规与市场准入合规领航员。", js)
        self.assertIn("告诉我你的需求", js)
        self.assertIn("Our goal is to reach for the stars and beyond", js)
        self.assertIn("Notre objectif est d’atteindre les étoiles", js)
        self.assertIn("Unser Ziel ist es, nach den Sternen zu greifen", js)
        self.assertIn("function addWelcomeCard()", js)
        self.assertIn("const coldStart = !state.sessionId && showCard;", js)
        self.assertIn("selected_session: coldStart ? {} : (data.selected_session || {})", js)
        self.assertNotIn("data.active_session_id || state.sessionId", js)
        self.assertIn("text-align: center", _block(css, ".welcome-card"))
        self.assertIn("margin: 10px auto 26px", _block(css, ".welcome-card"))
        self.assertIn("overflow-wrap: anywhere", _block(css, ".welcome-card"))

    def test_first_chat_message_removes_welcome_card(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function removeWelcomeCard()", js)
        self.assertIn('document.querySelectorAll(".welcome-card").forEach((element) => element.remove())', js)
        self.assertIn("removeWelcomeCard();\n  const wrap = document.createElement(\"div\");", js)

    def test_task_context_and_execution_status_are_collapsible(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="status-toggle"', html)
        self.assertIn('<section class="runtime-panel hidden" id="runtime-panel"', html)
        self.assertIn('<details class="side-panel collapsible-panel" id="task-context-panel">', html)
        self.assertIn('<summary class="panel-title">', html)
        self.assertIn('id="execution-status-panel"', html)
        self.assertIn('id="tool-readiness-status"', html)
        self.assertNotIn('id="mcp-status"', html)
        self.assertNotIn("MCP 已连接", js)
        self.assertNotIn("MCP 待接入", html)
        self.assertNotIn("MCP 待接入", js)
        self.assertIn("function renderToolReadiness(readiness)", js)
        self.assertIn('status-toggle").addEventListener("click"', js)
        self.assertIn(".collapsible-panel > summary", css)
        self.assertIn(".runtime-panel", css)

    def test_right_panel_labels_and_hidden_budget(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("<h2>填报结果</h2>", html)
        self.assertNotIn("<h2>分页结果</h2>", html)
        self.assertIn("<h2>Skills</h2>", html)
        self.assertNotIn("Agent 能力 / MCP Tools / 插件能力", html)
        self.assertIn('class="side-panel budget-panel hidden"', html)
        self.assertIn('class="side-panel skills-panel"', html)
        self.assertIn("renderTools(data.skills || data.tools || [])", js)
        self.assertIn('event.type === "skill_command_selected"', js)
        self.assertIn("renderAgentProgress(`使用 ${title}`)", js)
        self.assertIn("overflow-y: auto", _block(css, ".tool-cloud"))
        self.assertIn("display: grid", _block(css, ".tool-cloud"))
        self.assertIn("grid-template-columns: 1fr", _block(css, ".tool-cloud"))
        self.assertIn("max-height: 180px", _block(css, ".tool-cloud"))
        self.assertIn("width: 100%", _block(css, ".tool-chip"))

    def test_top_chrome_status_uses_two_operator_states(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="chrome-status" type="button"', html)
        self.assertIn("未检测到填报chrome", html)
        self.assertIn("function renderControlledChromeStatus(status)", js)
        self.assertIn('status.label || "未检测到填报chrome"', js)
        self.assertIn("function activateControlledChrome", js)
        self.assertIn('api("/api/controlled-chrome")', js)
        self.assertIn('api("/api/controlled-chrome/open", jsonOptions("POST", {}))', js)
        self.assertIn('$("chrome-status").addEventListener("click", activateControlledChrome)', js)
        self.assertIn('chrome.classList.toggle("muted", status.status !== "connected")', js)
        self.assertIn(".status-button", css)
        self.assertIn(".status-pill.busy::before", css)
        self.assertNotIn("Chrome ${data.execution_status?.chrome?.value", js)

    def test_checklist_placeholder_has_three_columns(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="checklist-panel"', html)
        self.assertIn("<h2>Checklist</h2>", html)
        self.assertIn('id="checklist-table"', html)
        self.assertIn("参数", html)
        self.assertIn("当前值", html)
        self.assertIn("标准值", html)
        self.assertIn("function renderChecklist(checklist)", js)
        self.assertIn("renderChecklist(data.checklist || {})", js)
        self.assertIn("overflow-y: auto", _block(css, ".checklist-table"))
        self.assertIn("grid-template-columns: 1fr 1fr 1fr", _block(css, ".checklist-row"))
        self.assertIn("min-height: 260px", _block(css, ".checklist-panel"))
        self.assertIn("max-height: 220px", _block(css, ".checklist-table"))

    def test_review_summary_has_room_for_two_lines(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('class="side-panel review-panel"', html)
        self.assertIn("min-height: 42px", _block(css, ".review-note"))

    def test_human_action_response_refreshes_bootstrap_state(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const result = await api(`/api/human-actions/", js)
        self.assertIn("if (result.bootstrap) renderBootstrap(result.bootstrap);", js)

    def test_session_sidebar_exposes_archive_management(self) -> None:
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="toggle-archived"', html)
        self.assertIn("data-archive-session", js)
        self.assertIn("data-restore-session", js)
        self.assertIn("/archive", js)
        self.assertIn("/restore", js)
        self.assertIn(".session-memory", css)

    def test_session_selection_uses_lightweight_session_view(self) -> None:
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function switchSession(sessionId)", js)
        self.assertIn("await api(`/api/sessions/${encodeURIComponent(sessionId)}`)", js)
        self.assertIn("renderSessionView(view);", js)
        self.assertIn("await switchSession(nextId);", js)
        self.assertNotIn("state.sessionId = nextId;\n    clearChat();\n    await bootstrap(false);", js)

    def test_desktop_shell_uses_viewport_height_with_internal_column_scrollers(self) -> None:
        css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("padding: 16px 0", _block(css, "body"))
        self.assertIn("height: calc(100vh - 32px)", _block(css, ".app-shell"))
        self.assertIn("margin: 0 auto", _block(css, ".app-shell"))
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr)", _block(css, ".app-shell"))
        self.assertIn("height: 100%", _block(css, ".workspace-grid"))
        self.assertIn("min-height: 0", _block(css, ".workspace-grid"))
        self.assertNotIn("min-height: calc(100vh", _block(css, ".chat-plane"))
        self.assertIn("height: 100%", _block(css, ".chat-plane"))
        self.assertIn("min-height: 0", _block(css, ".chat-plane"))
        self.assertIn("min-height: 0", _block(css, ".chat-scroll"))
        self.assertIn("max-height: 100%", _block(css, ".side-stack"))
        self.assertIn("overflow: auto", _block(css, ".side-stack"))


def _block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\}}", css, re.DOTALL)
    if match is None:
        raise AssertionError(f"Missing CSS block for {selector}")
    return match.group("body")


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"Not a PNG file: {path}")
    return struct.unpack(">II", data[16:24])


def _ico_sizes(path: Path) -> list[tuple[int, int]]:
    data = path.read_bytes()
    reserved, image_type, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or image_type != 1:
        raise AssertionError(f"Not an ICO file: {path}")
    sizes: list[tuple[int, int]] = []
    offset = 6
    for _ in range(count):
        width = data[offset] or 256
        height = data[offset + 1] or 256
        sizes.append((width, height))
        offset += 16
    return sizes


if __name__ == "__main__":
    unittest.main()
