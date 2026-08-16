const state = {
  sessionId: "",
  eventIds: new Set(),
  sessions: [],
  busy: false,
  stopping: false,
  currentTurnId: "",
  currentStreamController: null,
  stopNoticeShown: false,
  thinkingElement: null,
  streamingAgentElement: null,
  streamingAgentText: "",
  streamingAgentQueue: [],
  streamingAgentTimer: null,
  streamingAgentFinalText: null,
  agentProgressElement: null,
  agentProgressCount: 0,
  agentProgressHistory: [],
  agentProgressHideTimer: null,
  chromeStatusBusy: false,
  showArchived: false,
};

const SUPPORTED_PROVIDERS = new Set(["", "deepseek", "openai_compatible"]);
const AVATAR_ASSETS = {
  agent: "/static/assets/agent-avatar.png",
  user: "/static/assets/user-avatar.png",
};

const WELCOME_LINES = {
  title: "我是 RegPilot，你的行业法规与市场准入合规领航员。",
  action: "告诉我你的需求",
  motto: [
    "※※※我们的目标是星辰大海※※※",
    "※※※Our goal is to reach for the stars and beyond※※※",
    "※※※Notre objectif est d’atteindre les étoiles, et d’aller au-delà※※※",
    "※※※Unser Ziel ist es, nach den Sternen zu greifen und darüber hinauszugehen※※※",
  ],
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function jsonOptions(method, payload) {
  return {
    method,
    headers: {"Content-Type": "application/json; charset=utf-8"},
    body: JSON.stringify(payload),
  };
}

function newClientTurnId() {
  if (window.crypto?.randomUUID) return `turn_${window.crypto.randomUUID()}`;
  return `turn_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function formatRegPilotToolName(name) {
  return ({
    regpilot_list_skills: "读取 Skill 列表",
    regpilot_use_skill: "调用业务 Skill",
    regpilot_inspect_skill: "检查 Skill",
    regpilot_create_skill_draft: "创建 Skill 草案",
    regpilot_validate_skill: "校验 Skill",
    regpilot_install_skill: "安装 Skill",
    regpilot_enable_skill: "启用 Skill",
    regpilot_rename_skill: "重命名 Skill",
    regpilot_load_skill: "加载 Skill",
    regpilot_ingest_sources: "记录来源文件",
    regpilot_search_sources: "检索资料",
    regpilot_build_evidence_bundle: "整理证据包",
    regpilot_load_source_slice: "读取证据片段",
    regpilot_generate_interpretation_report: "生成报告文件",
    regpilot_stage_regulation_sources: "登记法规来源",
    regpilot_record_regulation_entries: "记录法规索引",
    regpilot_export_regulation_index: "导出法规索引",
  })[name] || name || "模型工具";
}

function progressToolLabel(name) {
  return ({
    regpilot_list_skills: "确认可用能力",
    regpilot_use_skill: "进入业务能力",
    regpilot_inspect_skill: "检查 Skill",
    regpilot_create_skill_draft: "创建 Skill 草案",
    regpilot_validate_skill: "校验 Skill",
    regpilot_install_skill: "安装 Skill",
    regpilot_enable_skill: "启用 Skill",
    regpilot_rename_skill: "重命名 Skill",
    regpilot_load_skill: "准备业务指南",
    regpilot_ingest_sources: "整理来源资料",
    regpilot_search_sources: "检索资料证据",
    regpilot_build_evidence_bundle: "汇总资料证据",
    regpilot_load_source_slice: "读取证据片段",
    regpilot_generate_interpretation_report: "生成报告文件",
    regpilot_stage_regulation_sources: "登记法规来源",
    regpilot_record_regulation_entries: "记录法规索引",
    regpilot_export_regulation_index: "导出法规索引",
    formfill_run_until_stop: "推进填报流程",
  })[name] || formatRegPilotToolName(name);
}

function isQuietProgressTool(name) {
  return new Set(["regpilot_load_skill", "regpilot_ingest_sources", "regpilot_list_skills", "regpilot_stage_regulation_sources"]).has(name);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function splitMarkdownTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return [];
  return trimmed
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isMarkdownTableSeparator(line) {
  const cells = splitMarkdownTableRow(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdownTable(lines) {
  const rows = lines
    .filter((line) => !isMarkdownTableSeparator(line))
    .map(splitMarkdownTableRow)
    .filter((cells) => cells.length > 1);
  if (!rows.length) return "";
  const head = rows[0];
  const body = rows.slice(1);
  const header = `<thead><tr>${head.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead>`;
  const rowsHtml = body.length
    ? body.map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")
    : "";
  return `<div class="message-table-wrap"><table>${header}<tbody>${rowsHtml}</tbody></table></div>`;
}

function renderMessageContent(content) {
  const lines = String(content ?? "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let listType = "";
  let tableLines = [];
  let inCode = false;
  let codeLines = [];

  const closeList = () => {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = "";
  };
  const flushTable = () => {
    if (!tableLines.length) return;
    closeList();
    html.push(renderMarkdownTable(tableLines));
    tableLines = [];
  };
  const openList = (type) => {
    flushTable();
    if (listType === type) return;
    closeList();
    listType = type;
    html.push(`<${type}>`);
  };
  const flushCode = () => {
    if (!codeLines.length) return;
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    codeLines = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      flushTable();
      closeList();
      if (inCode) flushCode();
      inCode = !inCode;
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!trimmed) {
      flushTable();
      closeList();
      html.push('<div class="message-gap"></div>');
      continue;
    }

    if (splitMarkdownTableRow(trimmed).length > 1) {
      closeList();
      tableLines.push(trimmed);
      continue;
    }

    flushTable();

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      html.push(`<div class="message-heading level-${heading[1].length}">${renderInlineMarkdown(heading[2])}</div>`);
      continue;
    }

    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      openList("ul");
      html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
      continue;
    }

    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (ordered) {
      openList("ol");
      html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
      continue;
    }

    closeList();
    html.push(`<p>${renderInlineMarkdown(line)}</p>`);
  }

  if (inCode) flushCode();
  flushTable();
  closeList();
  return html.filter(Boolean).join("");
}

function setText(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
}

function renderToolReadiness(readiness) {
  const element = $("tool-readiness-status");
  if (!element) return;
  const status = readiness?.status || "pending_integration";
  element.textContent = readiness?.label || (status === "available" ? "工具已就绪" : "工具待接入");
  element.classList.toggle("muted", status !== "available");
}

async function bootstrap(showCard = true) {
  const suffix = state.sessionId ? `?session_id=${encodeURIComponent(state.sessionId)}` : "";
  const data = await api(`/api/bootstrap${suffix}`);
  const coldStart = !state.sessionId && showCard;
  state.sessions = data.sessions || [];
  state.sessionId = coldStart ? "" : (data.selected_session?.session_id || state.sessionId || "");
  const viewData = {
    ...data,
    selected_session: coldStart ? {} : (data.selected_session || {}),
  };
  renderBootstrap(viewData);
  renderSettings(data.provider || {});
  if (state.showArchived) {
    await refreshSessionList();
  }
  if (showCard) {
    addWelcomeCard();
  }
}

function renderBootstrap(data) {
  setText("local-status", data.app?.status === "running" ? "本地运行" : "本地状态未知");
  renderControlledChromeStatus(data.controlled_chrome || {});
  renderToolReadiness(data.tool_readiness || {});
  setText("automation-policy", data.execution_status?.auto_next_step?.value || "自动推进：停在提交前");
  setText("context-status", statusText(data.task_context?.status));
  renderTaskContext(data.task_context || {});
  renderExecutionStatus(data.execution_status || {});
  renderReviewSummary(data.review_summary || {});
  renderChecklist(data.checklist || {});
  renderTools(data.skills || data.tools || []);
  renderHumanBudget(data.human_budget || {});
  renderSessions(data.sessions || [], data.selected_session || {});
}

function renderSessionView(data) {
  setText("context-status", statusText(data.task_context?.status));
  renderTaskContext(data.task_context || {});
  renderExecutionStatus(data.execution_status || {});
  renderReviewSummary(data.review_summary || {});
  renderChecklist(data.checklist || {});
  renderHumanBudget(data.human_budget || {});
  state.sessionId = data.selected_session?.session_id || state.sessionId || "";
  renderSessions(data.sessions || state.sessions || [], data.selected_session || {});
}

function renderControlledChromeStatus(status) {
  const chrome = $("chrome-status");
  if (!chrome) return;
  chrome.textContent = status.label || "未检测到填报chrome";
  chrome.classList.toggle("muted", status.status !== "connected");
  chrome.classList.toggle("busy", status.status === "checking" || status.status === "launching");
  chrome.disabled = state.chromeStatusBusy;
  const active = Array.isArray(status.active) ? status.active : [];
  const first = active[0] || {};
  chrome.title = first.title
    ? `${first.title}${first.debug_port ? ` · port ${first.debug_port}` : ""}${first.last_url ? ` · ${first.last_url}` : ""}`
    : `${chrome.textContent}。点击检测或打开填报 Chrome。`;
}

function renderControlledChromeBusy(label, status = "checking") {
  renderControlledChromeStatus({status, label});
}

async function activateControlledChrome() {
  if (state.chromeStatusBusy) return;
  state.chromeStatusBusy = true;
  let latestStatus = null;
  try {
    renderControlledChromeBusy("正在检测填报chrome", "checking");
    const checked = await api("/api/controlled-chrome");
    latestStatus = checked.controlled_chrome || {};
    if (latestStatus.status !== "connected") {
      renderControlledChromeBusy("正在打开填报chrome", "launching");
      const opened = await api("/api/controlled-chrome/open", jsonOptions("POST", {}));
      latestStatus = opened.controlled_chrome || {};
    }
  } catch (error) {
    latestStatus = {status: "missing", label: "未检测到填报chrome"};
    addErrorStatusCard({code: "chrome_open_failed", message: error.message || "填报 Chrome 打开失败。"});
  } finally {
    state.chromeStatusBusy = false;
    renderControlledChromeStatus(latestStatus || {status: "missing", label: "未检测到填报chrome"});
  }
}

function renderSessions(sessions, selected) {
  state.sessions = sessions;
  const selectedId = selected.session_id || state.sessionId;
  $("session-list").innerHTML = sessions.length ? sessions.map((session) => `
    <div class="session-item ${session.session_id === selectedId && !state.showArchived ? "active" : ""} ${session.status === "archived" ? "archived" : ""}" data-session="${escapeHtml(session.session_id)}">
      <button class="session-title" type="button" ${state.showArchived ? "" : `data-select-session="${escapeHtml(session.session_id)}"`} title="${escapeHtml(session.title)}">${escapeHtml(session.title)}</button>
      ${session.status === "archived" ? `
        <button class="session-action" type="button" data-restore-session="${escapeHtml(session.session_id)}" title="恢复">复</button>
        <button class="session-action" type="button" data-delete-session="${escapeHtml(session.session_id)}" title="删除">删</button>
      ` : `
        <button class="session-action" type="button" data-rename-session="${escapeHtml(session.session_id)}" title="重命名">改</button>
        <button class="session-action" type="button" data-archive-session="${escapeHtml(session.session_id)}" title="归档">归</button>
        <button class="session-action" type="button" data-delete-session="${escapeHtml(session.session_id)}" title="删除">删</button>
      `}
      <span class="session-time">${escapeHtml(formatSessionTime(session.updated_at))}</span>
      <span class="session-memory">${escapeHtml(sessionMemoryLabel(session))}</span>
    </div>
  `).join("") : `<div class="session-empty">${state.showArchived ? "暂无归档会话" : "暂无会话"}</div>`;
  setText("toggle-archived", state.showArchived ? "活动会话" : "归档箱");
  setText("session-note", state.showArchived
    ? "归档会话保存在本地，恢复后可继续。"
    : (selectedId ? "点击会话可切换上下文。" : "新建或发送消息后自动创建会话。"));
}

async function refreshSessionList() {
  const suffix = state.showArchived ? "?archived=1" : "";
  const data = await api(`/api/sessions${suffix}`);
  renderSessions(data.sessions || [], state.showArchived ? {} : {session_id: state.sessionId});
}

async function switchSession(sessionId) {
  state.sessionId = sessionId;
  clearChat();
  renderSessions(state.sessions, {session_id: sessionId});
  const view = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  renderSessionView(view);
  await refreshEvents();
}

function sessionMemoryLabel(session) {
  const count = Number(session.message_count || 0);
  const summary = session.has_context_summary ? " · 已压缩" : "";
  return `${count} 条上下文${summary}`;
}

function renderTaskContext(context) {
  const labels = [
    ["workspace", "工作空间"],
    ["master_workbook", "总表文件"],
    ["mapping_workbook", "映射表"],
    ["target_column", "目标列"],
    ["target_tool", "目标工具"],
  ];
  $("task-context").innerHTML = labels.map(([key, label]) => `
    <dt>${label}</dt><dd>${escapeHtml(context[key] || "待后端接入")}</dd>
  `).join("");
}

function renderExecutionStatus(status) {
  const rows = ["chrome", "current_page", "page_fingerprint", "auto_next_step", "final_stop"]
    .map((key) => status[key])
    .filter(Boolean);
  $("execution-status").innerHTML = rows.map((item) => `
    <dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.value || statusText(item.status))}</dd>
  `).join("");
}

function renderReviewSummary(summary) {
  const rows = summary.pages || [];
  const totals = summary.totals || {green: 0, yellow: 0, red: 0};
  const renderedRows = rows.map((row) => trafficRow(row.label, row.green, row.yellow, row.red));
  renderedRows.push(trafficRow("总计", totals.green, totals.yellow, totals.red));
  $("review-summary").innerHTML = `
    <div class="traffic-row"><strong>页面</strong><strong class="green">绿灯</strong><strong class="amber">黄灯</strong><strong class="red">红灯</strong></div>
    ${renderedRows.join("")}
  `;
  setText("review-note", summary.message || "等待底层验证结果接入。");
}

function renderChecklist(checklist) {
  const rows = Array.isArray(checklist.rows) ? checklist.rows : [];
  const body = rows.length ? rows.map((row) => `
    <div class="checklist-row" role="row">
      <span role="cell">${escapeHtml(row.parameter || "")}</span>
      <span role="cell">${escapeHtml(row.current_value || "")}</span>
      <span role="cell">${escapeHtml(row.standard_value || "")}</span>
    </div>
  `).join("") : `
    <div class="checklist-row checklist-empty" role="row">
      <span role="cell">等待法规解读</span>
      <span role="cell">待企业参数</span>
      <span role="cell">待公开标准值</span>
    </div>
  `;
  $("checklist-table").innerHTML = `
    <div class="checklist-row checklist-head" role="row">
      <strong role="columnheader">参数</strong>
      <strong role="columnheader">当前值</strong>
      <strong role="columnheader">标准值</strong>
    </div>
    ${body}
  `;
}

function trafficRow(label, green, yellow, red) {
  return `
    <div class="traffic-row">
      <span>${escapeHtml(label)}</span>
      <span class="green">${green || 0}</span>
      <span class="amber">${yellow || 0}</span>
      <span class="red">${red || 0}</span>
    </div>
  `;
}

function renderTools(tools) {
  $("tool-cloud").innerHTML = tools.map((tool) => `
    <span class="tool-chip ${tool.status === "pending_integration" ? "pending" : ""}" title="${escapeHtml(tool.unavailable_reason || tool.description || "")}">
      ${escapeHtml(tool.title)}
    </span>
  `).join("");
}

function renderHumanBudget(budget) {
  setText("pending-actions", String(budget.pending_count || 0));
  setText("human-budget", `本次预计 ${budget.planned_count || 0} 次中途点击；最终提交前 ${budget.remaining_before_submit || 0} 次人工复核。`);
}

function isValidDeepSeekUserId(value) {
  const text = String(value || "").trim();
  return !text || /^[A-Za-z0-9_-]{1,512}$/.test(text);
}

function renderSettings(provider) {
  const providerId = provider.provider || "";
  $("provider").value = SUPPORTED_PROVIDERS.has(providerId) ? providerId : "";
  $("base-url").value = provider.base_url || "";
  $("model").value = provider.model || "";
  $("request-timeout").value = provider.request_timeout_seconds || "660";
  $("deepseek-thinking").value = provider.deepseek_thinking || "enabled";
  $("deepseek-reasoning-effort").value = provider.deepseek_reasoning_effort || "high";
  $("deepseek-max-tokens").value = provider.deepseek_max_tokens || "";
  $("deepseek-stream-include-usage").checked = provider.deepseek_stream_include_usage !== false;
  $("deepseek-user-id").value = provider.deepseek_user_id || "";
  $("deepseek-strict-tool-schema").checked = Boolean(provider.deepseek_strict_tool_schema);
  $("deepseek-retry-max-attempts").value = provider.deepseek_retry_max_attempts || "2";
  $("deepseek-retry-backoff-seconds").value = provider.deepseek_retry_backoff_seconds || "0.25";
  $("deepseek-json-empty-retry-attempts").value = provider.deepseek_json_empty_retry_attempts || "1";
  $("api-key").placeholder = provider.has_api_key ? provider.api_key_masked : "留空则不更新密钥";
  if (providerId && !SUPPORTED_PROVIDERS.has(providerId)) {
    setText("settings-note", "当前仅支持 DeepSeekPro / OpenAI-compatible，请重新保存模型配置。");
  }
}

function statusText(status) {
  if (status === "pending_integration") return "待接入";
  if (status === "available") return "可用";
  if (status === "running") return "运行中";
  if (status === "disabled") return "已禁用";
  return status || "未知";
}

function formatSessionTime(value) {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
}

function clearChat() {
  $("chat-log").innerHTML = "";
  state.eventIds.clear();
  state.thinkingElement = null;
  resetStreamingAgentMessage();
  resetAgentProgress();
}

function setBusy(value) {
  state.busy = Boolean(value);
  if (!state.busy) state.stopping = false;
  document.querySelectorAll(".session-add, .session-filter, .session-action, .session-title, .human-action-card button").forEach((element) => {
    element.disabled = state.busy;
  });
  updateSendButton();
}

function updateSendButton() {
  const button = document.querySelector(".send-button");
  if (!button) return;
  button.disabled = state.stopping;
  button.classList.toggle("is-stop", state.busy);
  button.classList.toggle("is-stopping", state.stopping);
  button.textContent = state.busy ? (state.stopping ? "…" : "■") : "➤";
  button.setAttribute("aria-label", state.busy ? (state.stopping ? "正在停止" : "停止") : "发送");
}

function showStopNotice(content) {
  if (state.stopNoticeShown) return;
  state.stopNoticeShown = true;
  addSystemCard(content);
}

function guardSessionAction() {
  if (!state.busy) return false;
  setText("session-note", "当前任务执行中，请等待完成。");
  return true;
}

function scrollChatToBottom() {
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

function resizeMessageInput() {
  const input = $("message-input");
  if (!input) return;
  input.style.height = "auto";
  const styles = window.getComputedStyle(input);
  const maxHeight = Number.parseFloat(styles.maxHeight) || 156;
  const minHeight = Number.parseFloat(styles.minHeight) || 58;
  const nextHeight = Math.min(Math.max(input.scrollHeight, minHeight), maxHeight);
  input.style.height = `${nextHeight}px`;
  input.style.overflowY = input.scrollHeight > maxHeight ? "auto" : "hidden";
}

function avatarMarkup(role) {
  const src = role === "agent" ? AVATAR_ASSETS.agent : AVATAR_ASSETS.user;
  return `<div class="avatar" aria-hidden="true"><img src="${src}" alt=""></div>`;
}

function removeWelcomeCard() {
  document.querySelectorAll(".welcome-card").forEach((element) => element.remove());
}

function addMessage(role, content, options = {}) {
  removeWelcomeCard();
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  if (options.pending) wrap.dataset.pending = role;
  if (options.contentKey) wrap.dataset.contentKey = options.contentKey;
  const body = role === "agent" ? renderMessageContent(content) : escapeHtml(content);
  wrap.innerHTML = `
    ${avatarMarkup(role)}
    <div class="bubble"><div class="message-content">${body}</div></div>
  `;
  $("chat-log").appendChild(wrap);
  scrollChatToBottom();
  return wrap;
}

function createStreamingAgentMessage() {
  clearThinkingStatus();
  const wrap = document.createElement("div");
  wrap.className = "message agent";
  wrap.innerHTML = `
    ${avatarMarkup("agent")}
    <div class="bubble"><div class="message-content"></div></div>
  `;
  $("chat-log").appendChild(wrap);
  state.streamingAgentElement = wrap;
  state.streamingAgentText = "";
  state.streamingAgentFinalText = null;
  scrollChatToBottom();
  return wrap;
}

function ensureStreamingAgentMessage() {
  if (state.streamingAgentElement && document.body.contains(state.streamingAgentElement)) {
    return state.streamingAgentElement;
  }
  return createStreamingAgentMessage();
}

function renderStreamingAgentText() {
  const content = state.streamingAgentElement?.querySelector(".message-content");
  if (!content) return;
  content.innerHTML = state.streamingAgentText ? renderMessageContent(state.streamingAgentText) : "";
  scrollChatToBottom();
}

function scheduleStreamingAgentPump() {
  if (state.streamingAgentTimer) return;
  state.streamingAgentTimer = window.setTimeout(pumpStreamingAgentQueue, 18);
}

function pumpStreamingAgentQueue() {
  state.streamingAgentTimer = null;
  if (!state.streamingAgentElement) return;
  const nextCharacter = state.streamingAgentQueue.shift();
  if (nextCharacter !== undefined) {
    state.streamingAgentText += nextCharacter;
    renderStreamingAgentText();
    scheduleStreamingAgentPump();
    return;
  }
  if (state.streamingAgentFinalText !== null) {
    state.streamingAgentText = state.streamingAgentFinalText;
    state.streamingAgentFinalText = null;
    renderStreamingAgentText();
    state.streamingAgentElement = null;
  }
}

function appendStreamingAgentDelta(delta) {
  const text = String(delta ?? "");
  if (!text) return;
  ensureStreamingAgentMessage();
  state.streamingAgentQueue.push(...Array.from(text));
  scheduleStreamingAgentPump();
}

function finishStreamingAgentMessage(content) {
  ensureStreamingAgentMessage();
  state.streamingAgentFinalText = String(content ?? "");
  scheduleStreamingAgentPump();
}

function resetStreamingAgentMessage() {
  if (state.streamingAgentTimer) {
    window.clearTimeout(state.streamingAgentTimer);
  }
  state.streamingAgentElement = null;
  state.streamingAgentText = "";
  state.streamingAgentQueue = [];
  state.streamingAgentTimer = null;
  state.streamingAgentFinalText = null;
}

function finishStoppedAgentMessage() {
  clearThinkingStatus();
  resetAgentProgress();
  const partialText = `${state.streamingAgentText}${state.streamingAgentQueue.join("")}`.trim();
  if (state.streamingAgentElement && partialText) {
    state.streamingAgentQueue = [];
    finishStreamingAgentMessage(partialText);
    return;
  }
  resetStreamingAgentMessage();
  showStopNotice("已请求停止当前 Agent 回合。");
}

function confirmPendingUserMessage(content) {
  const pending = Array.from(document.querySelectorAll('.message.user[data-pending="user"]'))
    .find((element) => element.dataset.contentKey === content);
  if (!pending) return false;
  delete pending.dataset.pending;
  delete pending.dataset.contentKey;
  return true;
}

function showThinkingStatus() {
  clearThinkingStatus();
  const wrap = document.createElement("div");
  wrap.className = "message agent thinking-message";
  wrap.innerHTML = `
    ${avatarMarkup("agent")}
    <div class="bubble thinking-bubble">
      <span class="thinking-dot"></span>
      <span>RegPilot 正在思考...</span>
    </div>
  `;
  $("chat-log").appendChild(wrap);
  state.thinkingElement = wrap;
  scrollChatToBottom();
}

function clearThinkingStatus() {
  if (state.thinkingElement) {
    state.thinkingElement.remove();
    state.thinkingElement = null;
  }
}

function addSystemCard(content) {
  const card = document.createElement("div");
  card.className = "system-card";
  card.textContent = content;
  $("chat-log").appendChild(card);
  scrollChatToBottom();
}

function addWelcomeCard() {
  const card = document.createElement("div");
  card.className = "welcome-card";
  card.innerHTML = `
    <div class="welcome-title">${escapeHtml(WELCOME_LINES.title)}</div>
    <div class="welcome-action">${escapeHtml(WELCOME_LINES.action)}</div>
    <div class="welcome-motto">
      ${WELCOME_LINES.motto.map((line) => `<div>${escapeHtml(line)}</div>`).join("")}
    </div>
  `;
  $("chat-log").appendChild(card);
  scrollChatToBottom();
}

function addErrorStatusCard(error) {
  const payload = error && typeof error === "object" ? error : {};
  const code = String(payload.code || "runtime_error");
  const message = String(payload.message || payload.error || "模型调用失败。");
  const card = document.createElement("div");
  card.className = "error-status-card";
  card.innerHTML = `
    <strong>模型调用失败</strong>
    <div class="model-error-code">${escapeHtml(code)}</div>
    <div class="model-error-message">${escapeHtml(message)}</div>
  `;
  $("chat-log").appendChild(card);
  scrollChatToBottom();
}

function resetAgentProgress() {
  if (state.agentProgressHideTimer) {
    window.clearTimeout(state.agentProgressHideTimer);
  }
  if (state.agentProgressElement) {
    state.agentProgressElement.remove();
  }
  state.agentProgressElement = null;
  state.agentProgressCount = 0;
  state.agentProgressHistory = [];
  state.agentProgressHideTimer = null;
}

function hideAgentProgressSoon() {
  if (!state.agentProgressElement || state.agentProgressHideTimer) return;
  state.agentProgressHideTimer = window.setTimeout(() => {
    resetAgentProgress();
  }, 900);
}

function ensureAgentProgress() {
  clearThinkingStatus();
  if (state.agentProgressHideTimer) {
    window.clearTimeout(state.agentProgressHideTimer);
    state.agentProgressHideTimer = null;
  }
  if (state.agentProgressElement && document.body.contains(state.agentProgressElement)) {
    return state.agentProgressElement;
  }
  const element = document.createElement("div");
  element.className = "agent-progress-card";
  element.innerHTML = `
    <span class="agent-progress-pulse"></span>
    <div class="agent-progress-copy">
      <div class="agent-progress-title">RegPilot 正在处理</div>
      <div class="agent-progress-current"></div>
      <div class="agent-progress-meta"></div>
    </div>
  `;
  $("chat-log").appendChild(element);
  state.agentProgressElement = element;
  scrollChatToBottom();
  return element;
}

function renderAgentProgress(currentText) {
  const element = ensureAgentProgress();
  const current = element.querySelector(".agent-progress-current");
  const meta = element.querySelector(".agent-progress-meta");
  if (current) current.textContent = currentText;
  if (meta) {
    const processed = state.agentProgressCount > 0 ? `已处理 ${state.agentProgressCount} 步` : "";
    const history = state.agentProgressCount > 8 ? "" : state.agentProgressHistory.slice(-3).join(" · ");
    meta.textContent = [processed, history].filter(Boolean).join(" / ");
  }
  scrollChatToBottom();
}

function tokenCount(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return 0;
  return Math.trunc(number);
}

function formatTokenCount(value) {
  return tokenCount(value).toLocaleString("zh-CN");
}

function renderModelUsage(usage) {
  if (!usage || typeof usage !== "object") return;
  const cacheHit = tokenCount(usage.prompt_cache_hit_tokens);
  const cacheMiss = tokenCount(usage.prompt_cache_miss_tokens);
  const reasoningTokens = tokenCount(usage.completion_tokens_details?.reasoning_tokens);
  const totalTokens = tokenCount(usage.total_tokens);
  const parts = [];
  if (totalTokens) parts.push(`总计 ${formatTokenCount(totalTokens)} tokens`);
  if (cacheHit || cacheMiss) parts.push(`缓存 命中 ${formatTokenCount(cacheHit)} / 未命中 ${formatTokenCount(cacheMiss)}`);
  if (reasoningTokens) parts.push(`推理 ${formatTokenCount(reasoningTokens)} tokens`);
  if (!parts.length) return;
  renderAgentProgress("模型流式响应中");
  const meta = state.agentProgressElement?.querySelector(".agent-progress-meta");
  if (meta) meta.textContent = parts.join(" · ");
}

function updateAgentProgress(toolName, phase = "running", code = "") {
  const label = progressToolLabel(toolName);
  const quiet = isQuietProgressTool(toolName);
  if (phase === "completed") {
    state.agentProgressCount += 1;
    if (!quiet && label) {
      state.agentProgressHistory.push(label);
      state.agentProgressHistory = state.agentProgressHistory.slice(-3);
    }
    renderAgentProgress(code ? `继续处理（${code}）` : "继续处理下一步");
    return;
  }
  renderAgentProgress(quiet ? "正在准备上下文" : `正在${label}`);
}

function finishAgentProgress() {
  hideAgentProgressSoon();
}

function addHumanActionCard(action) {
  if (!action || $(action.action_id)) return;
  const card = document.createElement("div");
  card.className = "human-action-card";
  card.id = action.action_id;
  const buttons = (action.options || []).map((option, index) => `
    <button type="button" class="${index === 0 ? "primary-action" : ""}" data-action="${escapeHtml(action.action_id)}" data-option="${escapeHtml(option.id)}">
      ${escapeHtml(option.label)}
    </button>
  `).join("");
  card.innerHTML = `
    <strong>需要人工操作</strong>
    <p>${escapeHtml(action.prompt)}</p>
    <div class="action-row">${buttons}</div>
  `;
  $("chat-log").appendChild(card);
  scrollChatToBottom();
}

async function refreshEvents() {
  if (!state.sessionId) return;
  const response = await fetch(`/api/sessions/${encodeURIComponent(state.sessionId)}/events`);
  const text = await response.text();
  for (const event of parseSse(text)) {
    if (state.eventIds.has(event.id)) continue;
    state.eventIds.add(event.id);
    handleEvent(event);
  }
}

function parseSse(text) {
  return text.split(/\n\n+/).filter((chunk) => chunk.trim()).map(parseSseEvent);
}

function parseSseEvent(chunk) {
  const event = {id: "", type: "message", data: {}};
  const dataLines = [];
  for (const line of chunk.replace(/\r/g, "").split("\n")) {
    if (line.startsWith("id:")) event.id = line.slice(3).trim();
    if (line.startsWith("event:")) event.type = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length) event.data = JSON.parse(dataLines.join("\n") || "{}");
  return event;
}

function handleEvent(event) {
  if (event.type === "message" && event.data.role === "user" && !confirmPendingUserMessage(event.data.content)) {
    addMessage("user", event.data.content);
  }
  if (event.type === "stream_session") {
    state.sessionId = event.data.session_id || state.sessionId;
  }
  if (event.type === "assistant_status") {
    clearThinkingStatus();
    resetAgentProgress();
    addMessage("agent", event.data.content);
  }
  if (event.type === "agent_turn_cancel_requested") {
    showStopNotice("已请求停止当前 Agent 回合。");
  }
  if (event.type === "model_tool_turn_failed") {
    resetAgentProgress();
    addErrorStatusCard(event.data || {});
  }
  if (event.type === "model_tool_call_started") {
    updateAgentProgress(event.data.tool, "running");
  }
  if (event.type === "model_tool_call_completed") {
    updateAgentProgress(event.data.tool, "completed", event.data.code || "");
  }
  if (event.type === "skill_command_selected") {
    const title = event.data.skill_title || event.data.skill_id || "未知 Skill";
    renderAgentProgress(`使用 ${title}`);
  }
  if (event.type === "formfill_tool_started") updateAgentProgress(event.data.tool || "formfill_run_until_stop", "running");
  if (event.type === "formfill_tool_completed") updateAgentProgress(event.data.tool || "formfill_run_until_stop", "completed", event.data.status || "");
  if (event.type === "model_usage") renderModelUsage(event.data || {});
  if (event.type === "human_action_requested") addHumanActionCard(event.data);
  if (event.type === "human_action_resolved") addSystemCard(`人工操作已选择：${event.data.selected_option_id}`);
}

function handleStreamEvent(event) {
  if (event.id && state.eventIds.has(event.id)) return null;
  if (event.id) state.eventIds.add(event.id);
  if (event.type === "assistant_delta") {
    resetAgentProgress();
    appendStreamingAgentDelta(event.data.delta);
    return null;
  }
  if (event.type === "assistant_status") {
    clearThinkingStatus();
    finishAgentProgress();
    finishStreamingAgentMessage(event.data.content);
    return null;
  }
  if (event.type === "stream_done") {
    state.sessionId = event.data.session_id || state.sessionId;
    return event.data;
  }
  if (event.type === "stream_error") {
    throw new Error(event.data.error || "消息发送失败。");
  }
  handleEvent(event);
  return null;
}

async function streamChatMessage(content, clientTurnId, signal) {
  const request = jsonOptions("POST", {content, session_id: state.sessionId, client_turn_id: clientTurnId});
  request.signal = signal;
  const response = await fetch("/api/chat/messages/stream", request);
  if (!response.ok) {
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    throw new Error(data.error || response.statusText);
  }
  if (!response.body) {
    throw new Error("当前浏览器不支持流式响应。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let streamResult = null;

  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (chunk.trim()) {
        streamResult = handleStreamEvent(parseSseEvent(chunk)) || streamResult;
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    streamResult = handleStreamEvent(parseSseEvent(buffer)) || streamResult;
  }
  return streamResult || {ok: true, session_id: state.sessionId};
}

async function stopCurrentAgentTurn() {
  if (!state.busy || state.stopping) return;
  state.stopping = true;
  updateSendButton();
  try {
    const result = await api(
      "/api/chat/turns/cancel",
      jsonOptions("POST", {session_id: state.sessionId, client_turn_id: state.currentTurnId}),
    );
    state.sessionId = result.session_id || state.sessionId;
  } catch (error) {
    showStopNotice(error.message || "停止请求发送失败。");
  } finally {
    if (state.currentStreamController) state.currentStreamController.abort();
  }
}

$("settings-toggle").addEventListener("click", () => {
  $("settings-panel").classList.toggle("hidden");
});

$("status-toggle").addEventListener("click", () => {
  $("runtime-panel").classList.toggle("hidden");
});

$("chrome-status").addEventListener("click", activateControlledChrome);

$("save-settings").addEventListener("click", async () => {
  const deepseekUserId = $("deepseek-user-id").value.trim();
  if (!isValidDeepSeekUserId(deepseekUserId)) {
    setText("settings-note", "DeepSeek user_id 仅允许字母、数字、下划线和连字符，最多 512 个字符；不要填写隐私信息。");
    $("deepseek-user-id").focus();
    return;
  }
  const payload = {
    provider: $("provider").value,
    base_url: $("base-url").value,
    model: $("model").value,
    api_key: $("api-key").value,
    request_timeout_seconds: $("request-timeout").value,
    deepseek_thinking: $("deepseek-thinking").value,
    deepseek_reasoning_effort: $("deepseek-reasoning-effort").value,
    deepseek_max_tokens: $("deepseek-max-tokens").value,
    deepseek_stream_include_usage: $("deepseek-stream-include-usage").checked,
    deepseek_user_id: deepseekUserId,
    deepseek_strict_tool_schema: $("deepseek-strict-tool-schema").checked,
    deepseek_retry_max_attempts: $("deepseek-retry-max-attempts").value,
    deepseek_retry_backoff_seconds: $("deepseek-retry-backoff-seconds").value,
    deepseek_json_empty_retry_attempts: $("deepseek-json-empty-retry-attempts").value,
  };
  try {
    const publicView = await api("/api/settings/provider", jsonOptions("PUT", payload));
    $("api-key").value = "";
    renderSettings(publicView);
    setText("settings-note", "设置已保存；当前支持 DeepSeekPro / OpenAI-compatible，公开响应仅显示脱敏密钥。");
  } catch (error) {
    setText("settings-note", error.message || "设置保存失败。");
  }
});

$("new-session").addEventListener("click", async () => {
  if (guardSessionAction()) return;
  state.showArchived = false;
  const created = await api("/api/sessions", jsonOptions("POST", {title: "新会话"}));
  state.sessionId = created.session_id;
  clearChat();
  await bootstrap(false);
  await refreshEvents();
  addSystemCard("已创建新会话。");
});

$("toggle-archived").addEventListener("click", async () => {
  if (guardSessionAction()) return;
  state.showArchived = !state.showArchived;
  await refreshSessionList();
});

$("session-list").addEventListener("click", async (event) => {
  const selectButton = event.target.closest("button[data-select-session]");
  const renameButton = event.target.closest("button[data-rename-session]");
  const archiveButton = event.target.closest("button[data-archive-session]");
  const restoreButton = event.target.closest("button[data-restore-session]");
  const deleteButton = event.target.closest("button[data-delete-session]");
  if (!selectButton && !renameButton && !archiveButton && !restoreButton && !deleteButton) return;
  if (guardSessionAction()) return;

  if (selectButton) {
    const nextId = selectButton.dataset.selectSession;
    if (!nextId || nextId === state.sessionId) return;
    await switchSession(nextId);
    return;
  }

  if (renameButton) {
    const session = state.sessions.find((item) => item.session_id === renameButton.dataset.renameSession);
    const nextTitle = window.prompt("新的会话名称", session?.title || "");
    if (nextTitle === null) return;
    await api(`/api/sessions/${encodeURIComponent(renameButton.dataset.renameSession)}`, jsonOptions("PUT", {title: nextTitle}));
    await bootstrap(false);
    return;
  }

  if (archiveButton) {
    const archivedId = archiveButton.dataset.archiveSession;
    const archived = await api(`/api/sessions/${encodeURIComponent(archivedId)}/archive`, jsonOptions("POST", {}));
    if (state.sessionId === archivedId) {
      state.sessionId = archived.active_session_id || "";
      clearChat();
      await bootstrap(false);
      await refreshEvents();
      if (!state.sessionId) addSystemCard("会话已归档。");
      return;
    }
    await bootstrap(false);
    return;
  }

  if (restoreButton) {
    const restored = await api(`/api/sessions/${encodeURIComponent(restoreButton.dataset.restoreSession)}/restore`, jsonOptions("POST", {}));
    state.showArchived = false;
    state.sessionId = restored.active_session_id || restored.restored_session_id || "";
    clearChat();
    await bootstrap(false);
    await refreshEvents();
    addSystemCard("会话已恢复。");
    return;
  }

  if (deleteButton) {
    const session = state.sessions.find((item) => item.session_id === deleteButton.dataset.deleteSession);
    if (!window.confirm(`删除会话“${session?.title || "未命名会话"}”？`)) return;
    const deleted = await api(`/api/sessions/${encodeURIComponent(deleteButton.dataset.deleteSession)}`, {method: "DELETE"});
    if (state.sessionId === deleteButton.dataset.deleteSession) {
      state.sessionId = deleted.active_session_id || "";
      clearChat();
      await bootstrap(false);
      await refreshEvents();
      if (!state.sessionId) addSystemCard("会话已删除。");
      return;
    }
    await refreshSessionList();
  }
});

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) {
    await stopCurrentAgentTurn();
    return;
  }
  const content = $("message-input").value.trim();
  if (!content) return;
  $("message-input").value = "";
  resizeMessageInput();
  const clientTurnId = newClientTurnId();
  const controller = new AbortController();
  state.currentTurnId = clientTurnId;
  state.currentStreamController = controller;
  state.stopNoticeShown = false;
  addMessage("user", content, {pending: true, contentKey: content});
  showThinkingStatus();
  setBusy(true);
  try {
    const result = await streamChatMessage(content, clientTurnId, controller.signal);
    state.sessionId = result.session_id;
    if (result.bootstrap) renderBootstrap(result.bootstrap);
  } catch (error) {
    if (error.name === "AbortError") {
      finishStoppedAgentMessage();
      if (state.sessionId) {
        try {
          const view = await api(`/api/sessions/${encodeURIComponent(state.sessionId)}`);
          renderSessionView(view);
          await refreshEvents();
        } catch {
          // Stop is best-effort; the active stream may finish persisting after abort.
        }
      }
      return;
    }
    clearThinkingStatus();
    resetStreamingAgentMessage();
    resetAgentProgress();
    addErrorStatusCard({code: "stream_error", message: error.message || "消息发送失败。"});
  } finally {
    state.currentTurnId = "";
    state.currentStreamController = null;
    setBusy(false);
  }
});

$("chat-log").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  showThinkingStatus();
  setBusy(true);
  button.disabled = true;
  try {
    const result = await api(`/api/human-actions/${encodeURIComponent(button.dataset.action)}/responses`, jsonOptions("POST", {
      selected_option_id: button.dataset.option,
    }));
    state.sessionId = result.session_id || state.sessionId;
    await refreshEvents();
    if (result.bootstrap) renderBootstrap(result.bootstrap);
  } catch (error) {
    clearThinkingStatus();
    addSystemCard(error.message || "人工操作响应失败。");
    button.disabled = false;
  } finally {
    clearThinkingStatus();
    setBusy(false);
  }
});

$("message-input").addEventListener("input", resizeMessageInput);

$("message-input").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  $("composer").requestSubmit();
});

resizeMessageInput();

bootstrap().catch((error) => addSystemCard(error.message));
