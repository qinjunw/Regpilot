from __future__ import annotations

import json
import os
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .prompt_builder import build_system_prompt
from .resources import release_model_config_path


ALLOWED_TASK_IDS = {"shanghaiData_fill", "landmark_fill", "ota_fill"}
ALLOWED_POLICIES = {"disabled", "until_blocked", "until_before_final_submit"}
FILL_ACTION = "formfill_run_until_stop"
NO_ACTIONS = {"no_action", "needs_input"}


class ModelRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class DeepSeekOptions:
    thinking: str = "enabled"
    reasoning_effort: str = "high"
    max_tokens: int | None = None
    stream_include_usage: bool = True
    user_id: str = ""
    strict_tool_schema: bool = False
    retry_max_attempts: int = 2
    retry_backoff_seconds: float = 0.25
    json_empty_retry_attempts: int = 1


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout: float = 180.0
    source: str = ""
    deepseek: DeepSeekOptions = field(default_factory=DeepSeekOptions)

    def public_view(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "has_api_key": bool(self.api_key),
            "api_key_masked": _mask_secret(self.api_key),
            "source": self.source,
        }


@dataclass(frozen=True)
class ModelToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True)
class ModelToolTurn:
    content: str
    tool_calls: list[ModelToolCall]
    reasoning_content: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelStreamEvent:
    type: str
    delta: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class JsonChatClient(Protocol):
    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        ...


class TextChatClient(Protocol):
    def complete_text(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        ...


class ToolChatClient(Protocol):
    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> ModelToolTurn:
        ...


Transport = Callable[..., Any]


def default_config_path() -> Path:
    return release_model_config_path()


def load_provider_config(config_path: str | Path | None = None, *, overrides: dict[str, Any] | None = None) -> ProviderConfig:
    override = _clean_settings(overrides or {})
    if override.get("provider") and override.get("model") and override.get("api_key"):
        provider = str(override["provider"])
        if provider == "anthropic_compatible":
            raise ModelRuntimeError("model_provider_unsupported", "当前只接入 OpenAI-compatible/DeepSeek 风格接口。")
        return ProviderConfig(
            provider=provider,
            base_url=str(override.get("base_url") or _default_base_url(provider)),
            model=str(override["model"]),
            api_key=str(override["api_key"]),
            timeout=_coerce_timeout(override.get("request_timeout_seconds") or override.get("timeout_seconds")),
            deepseek=_deepseek_options_from(provider, override, {}),
            source="settings",
        )

    raw_path = config_path or os.environ.get("REGULATION_AGENT_MODEL_CONFIG") or default_config_path()
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise ModelRuntimeError("model_config_missing", f"模型配置文件不存在：{path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    provider = str(data.get("provider") or "").strip()
    provider_data = _provider_table(data, provider)
    api_key = str(provider_data.get("api_key") or data.get("api_key") or "").strip()
    model = str(provider_data.get("model") or data.get("default_text_model") or data.get("model") or "").strip()
    base_url = str(provider_data.get("base_url") or data.get("base_url") or _default_base_url(provider)).strip()
    if not provider:
        raise ModelRuntimeError("model_config_invalid", "模型配置缺少 provider。")
    if provider == "anthropic_compatible":
        raise ModelRuntimeError("model_provider_unsupported", "当前只接入 OpenAI-compatible/DeepSeek 风格接口。")
    if not api_key:
        raise ModelRuntimeError("model_config_invalid", "模型配置缺少 api_key。")
    if not model:
        raise ModelRuntimeError("model_config_invalid", "模型配置缺少 model/default_text_model。")
    timeout = _coerce_timeout(
        provider_data.get("request_timeout_seconds")
        or provider_data.get("timeout_seconds")
        or data.get("request_timeout_seconds")
        or data.get("timeout_seconds")
    )
    return ProviderConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=timeout,
        source=str(path),
        deepseek=_deepseek_options_from(provider, provider_data, data),
    )


class OpenAICompatibleChatClient:
    def __init__(self, config: ProviderConfig, *, transport: Transport | None = None, sleep: Callable[[float], None] | None = None) -> None:
        self.config = config
        self.transport = transport or urlopen
        self.sleep = sleep or time.sleep

    def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        self._apply_provider_options(payload, temperature=0, stream=False)
        empty_attempts = self.config.deepseek.json_empty_retry_attempts if _is_deepseek_config(self.config) else 0
        content = ""
        for attempt in range(max(0, empty_attempts) + 1):
            body = self._post(payload)
            content = _message_content(body)
            if content.strip():
                return _parse_json_content(content)
            if attempt < empty_attempts:
                continue
        raise ModelRuntimeError("model_bad_response", "模型 JSON 回复为空。")

    def complete_text(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        payload = {
            "model": self.config.model,
            "messages": messages,
        }
        self._apply_provider_options(payload, temperature=temperature, stream=False)
        return self._complete(payload).strip()

    def complete_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> ModelToolTurn:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "tools": self._tools_for_request(tools),
            "tool_choice": "auto",
        }
        self._apply_provider_options(payload, temperature=temperature, stream=False)
        return _parse_tool_turn(self._post(payload))

    def complete_with_tools_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        on_event: Callable[[ModelStreamEvent], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ModelToolTurn:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "tools": self._tools_for_request(tools),
            "tool_choice": "auto",
            "stream": True,
        }
        self._apply_provider_options(payload, temperature=temperature, stream=True)
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_buffers: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}
        for body in self._post_stream(payload):
            if should_cancel and should_cancel():
                raise ModelRuntimeError("model_cancelled", "模型工具回合已停止。")
            raw_usage = body.get("usage") if isinstance(body, dict) else None
            if isinstance(raw_usage, dict):
                usage = raw_usage
                if on_event:
                    on_event(ModelStreamEvent(type="usage", payload=raw_usage))
            for choice in _stream_choices(body):
                _raise_for_choice_finish_reason(choice)
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                reasoning_delta = delta.get("reasoning_content")
                if reasoning_delta is not None:
                    reasoning_parts.append(str(reasoning_delta))
                content_delta = delta.get("content")
                if content_delta is not None:
                    text = str(content_delta)
                    if text:
                        content_parts.append(text)
                        if on_event:
                            on_event(ModelStreamEvent(type="content_delta", delta=text))
                raw_tool_calls = delta.get("tool_calls") or []
                if isinstance(raw_tool_calls, list):
                    for raw_call in raw_tool_calls:
                        _merge_tool_call_delta(tool_buffers, raw_call)
        return _parse_tool_turn(_stream_buffers_to_response(content_parts, tool_buffers, reasoning_parts, usage))

    def _apply_provider_options(self, payload: dict[str, Any], *, temperature: float | None, stream: bool) -> None:
        if not _is_deepseek_config(self.config):
            if temperature is not None:
                payload["temperature"] = temperature
            return
        options = self.config.deepseek
        thinking = _deepseek_thinking(options.thinking)
        payload["thinking"] = {"type": thinking}
        if thinking == "enabled":
            payload["reasoning_effort"] = _deepseek_reasoning_effort(options.reasoning_effort)
        elif temperature is not None:
            payload["temperature"] = temperature
        if options.max_tokens is not None and options.max_tokens > 0:
            payload["max_tokens"] = int(options.max_tokens)
        user_id = _clean_user_id(options.user_id)
        if user_id:
            payload["user_id"] = user_id
        if stream and options.stream_include_usage:
            payload["stream_options"] = {"include_usage": True}

    def _tools_for_request(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if _is_deepseek_config(self.config) and self.config.deepseek.strict_tool_schema:
            return _deepseek_strict_tools(tools)
        return tools

    def _complete(self, payload: dict[str, Any]) -> str:
        body = self._post(payload)
        return _message_content(body)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] | None = None
        for attempt in range(_request_attempts(self.config)):
            request = self._request(payload)
            try:
                with self.transport(request, timeout=self.config.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                _raise_for_response_finish_reason(body)
                return body
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                if _should_retry_http(exc.code, attempt, self.config):
                    self.sleep(_retry_delay(self.config, attempt))
                    continue
                raise ModelRuntimeError("model_http_error", _short_error(f"HTTP {exc.code}: {raw}"), {"http_status": exc.code}) from exc
            except (URLError, OSError, TimeoutError) as exc:
                if _should_retry_connection(attempt, self.config):
                    self.sleep(_retry_delay(self.config, attempt))
                    continue
                raise ModelRuntimeError("model_connection_error", _short_error(str(exc))) from exc
            except json.JSONDecodeError as exc:
                raise ModelRuntimeError("model_bad_response", "模型响应不是 JSON。") from exc
        if body is None:
            raise ModelRuntimeError("model_bad_response", "模型响应为空。")
        return body

    def _post_stream(self, payload: dict[str, Any]):
        for attempt in range(_request_attempts(self.config)):
            request = self._request(payload)
            try:
                with self.transport(request, timeout=self.config.timeout) as response:
                    yield from _iter_sse_json(response)
                return
            except HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                if _should_retry_http(exc.code, attempt, self.config):
                    self.sleep(_retry_delay(self.config, attempt))
                    continue
                raise ModelRuntimeError("model_http_error", _short_error(f"HTTP {exc.code}: {raw}"), {"http_status": exc.code}) from exc
            except (URLError, OSError, TimeoutError) as exc:
                if _should_retry_connection(attempt, self.config):
                    self.sleep(_retry_delay(self.config, attempt))
                    continue
                raise ModelRuntimeError("model_connection_error", _short_error(str(exc))) from exc
            except json.JSONDecodeError as exc:
                raise ModelRuntimeError("model_bad_response", "模型流式响应不是 JSON。") from exc

    def _request(self, payload: dict[str, Any]) -> Request:
        return Request(
            _chat_completions_endpoint(_request_base_url(self.config, payload)),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )


class ModelIntentParser:
    def __init__(self, client: JsonChatClient) -> None:
        self.client = client

    def parse(self, content: str) -> dict[str, Any] | None:
        payload = self.client.complete_json(_intent_messages(content))
        action = str(payload.get("action") or "").strip()
        if action in NO_ACTIONS:
            return None
        if action != FILL_ACTION:
            raise ModelRuntimeError("model_intent_rejected", f"模型返回了未授权动作：{action or '(blank)'}")
        explicit_task_id = _explicit_task_id_from_user_text(content)
        if not explicit_task_id:
            return None
        task_id = explicit_task_id or str(payload.get("task_id") or "").strip()
        if task_id not in ALLOWED_TASK_IDS:
            raise ModelRuntimeError("model_intent_rejected", f"模型返回了未授权填报平台：{task_id or '(blank)'}")
        value_column = _normalize_column(payload.get("value_column"))
        policy = str(payload.get("auto_advance_policy") or "until_before_final_submit").strip()
        if policy not in ALLOWED_POLICIES:
            raise ModelRuntimeError("model_intent_rejected", f"模型返回了未知自动推进策略：{policy}")
        return {
            "task_id": task_id,
            "workbook_path": _optional_text(payload.get("workbook_path")),
            "workspace_dir": _optional_text(payload.get("workspace_dir")),
            "sheet": _optional_text(payload.get("sheet")),
            "value_column": value_column,
            "attachment_folder": _optional_text(payload.get("attachment_folder")),
            "auto_advance_policy": policy,
            "intent_source": "model",
        }


class AgentTurnDecisionParser:
    def __init__(self, client: JsonChatClient) -> None:
        self.client = client

    def decide(
        self,
        content: str,
        *,
        candidate_skills: list[dict[str, Any]],
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return self.client.complete_json(_agent_turn_decision_messages(content, candidate_skills, history=history))


class ModelChatResponder:
    def __init__(self, client: TextChatClient) -> None:
        self.client = client

    def respond(self, content: str, *, history: list[dict[str, str]] | None = None) -> str:
        reply = self.client.complete_text(_regpilot_messages(content, history=history), temperature=0.2)
        if not reply:
            raise ModelRuntimeError("model_bad_response", "模型回复为空。")
        return reply


def build_default_model_intent_parser(
    *,
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> ModelIntentParser | None:
    try:
        config = load_provider_config(config_path, overrides=settings)
    except ModelRuntimeError:
        return None
    return ModelIntentParser(OpenAICompatibleChatClient(config))


def build_default_model_chat_responder(
    *,
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> ModelChatResponder | None:
    try:
        config = load_provider_config(config_path, overrides=settings)
    except ModelRuntimeError:
        return None
    return ModelChatResponder(OpenAICompatibleChatClient(config))


def build_default_agent_turn_decision_parser(
    *,
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> AgentTurnDecisionParser | None:
    try:
        config = load_provider_config(config_path, overrides=settings)
    except ModelRuntimeError:
        return None
    return AgentTurnDecisionParser(OpenAICompatibleChatClient(config))


def build_default_tool_chat_client(
    *,
    settings: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> ToolChatClient | None:
    try:
        config = load_provider_config(config_path, overrides=settings)
    except ModelRuntimeError:
        return None
    return OpenAICompatibleChatClient(config)


def _intent_messages(content: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是汽车法规填报工作台的意图解析器。只返回一个 JSON 对象，不要解释。"
                "你不能执行网页或文件操作，只能把用户请求转成受控填报指令。"
                "允许 action: formfill_run_until_stop, needs_input, no_action。"
                "允许 task_id: shanghaiData_fill, landmark_fill, ota_fill。"
                "用户没有明确填报平台时返回 needs_input。"
                "用户没有明确值所在列时 value_column 为空。"
                "上海数据平台默认 sheet 是 SHGL备案参数；OTA 默认 sheet 是 REEV车型及功能备案细分。"
                "自动推进默认 until_before_final_submit，表示停在保存/提交前。"
                "必须从最后一条用户消息提取路径和列号，不要照抄字段说明中的占位符。"
                "平台必须来自用户正文明确说法，不要根据文件名或文件夹名猜平台。"
            ),
        },
        {
            "role": "user",
            "content": (
                "返回 JSON 字段说明："
                '{"action":"formfill_run_until_stop|needs_input|no_action",'
                '"task_id":"shanghaiData_fill|landmark_fill|ota_fill|",'
                '"workbook_path":"用户消息中的 xlsx/xlsm 路径，未知则空字符串",'
                '"workspace_dir":"用户消息中的文件夹路径，未知则空字符串",'
                '"sheet":"工作表名，未知可按平台默认",'
                '"value_column":"用户指定的值所在列，只能是 A-Z 字母，未知则空字符串",'
                '"attachment_folder":"OTA 附件文件夹，未知则空字符串",'
                '"auto_advance_policy":"until_before_final_submit|until_blocked|disabled"}'
            ),
        },
        {"role": "user", "content": str(content or "")},
    ]


def _agent_turn_decision_messages(
    content: str,
    candidate_skills: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    messages = [
        {
            "role": "system",
            "content": build_system_prompt("turn_router"),
        }
    ]
    for item in (history or [])[-6:]:
        role = str(item.get("role") or "")
        item_content = str(item.get("content") or "")
        if role in {"user", "assistant"} and item_content.strip():
            messages.append({"role": role, "content": item_content})
    messages.append(
        {
            "role": "user",
            "content": (
                "candidate_skills:\n"
                f"{json.dumps(_candidate_skill_cards(candidate_skills), ensure_ascii=False, indent=2)}\n\n"
                "operator_message:\n"
                f"{str(content or '')}"
            ),
        }
    )
    return messages


def _candidate_skill_cards(candidate_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    for skill in candidate_skills:
        cards.append(
            {
                "id": str(skill.get("id") or ""),
                "title": str(skill.get("display_name") or skill.get("title") or ""),
                "source_title": str(skill.get("title") or ""),
                "display_name": str(skill.get("display_name") or skill.get("title") or ""),
                "description": str(skill.get("description") or ""),
                "task_id": str(skill.get("task_id") or ""),
                "default_inputs": skill.get("default_inputs") if isinstance(skill.get("default_inputs"), dict) else {},
                "allowed_tools": [str(item) for item in skill.get("allowed_tools") or []],
                "run_policy_default": str(skill.get("run_policy_default") or ""),
                "allowed_run_policies": [str(item) for item in skill.get("allowed_run_policies") or []],
                "submission_safety_boundary": bool(skill.get("submission_safety_boundary", False)),
                "reference": _short_error(str(skill.get("reference_text") or ""), limit=1800),
            }
        )
    return cards


def _regpilot_messages(content: str, *, history: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": build_system_prompt("chat")}]
    for item in (history or [])[-10:]:
        role = str(item.get("role") or "")
        item_content = str(item.get("content") or "")
        if role in {"system", "user", "assistant"} and item_content.strip():
            messages.append({"role": role, "content": item_content})
    messages.append({"role": "user", "content": str(content or "")})
    return messages


def _provider_table(data: dict[str, Any], provider: str) -> dict[str, Any]:
    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        return {}
    table = providers.get(provider) or {}
    return table if isinstance(table, dict) else {}


def _deepseek_options_from(provider: str, primary: dict[str, Any], fallback: dict[str, Any]) -> DeepSeekOptions:
    if str(provider or "").strip().lower() != "deepseek":
        return DeepSeekOptions()
    return DeepSeekOptions(
        thinking=_first_text(primary, fallback, "deepseek_thinking", "thinking") or "enabled",
        reasoning_effort=_first_text(primary, fallback, "deepseek_reasoning_effort", "reasoning_effort") or "high",
        max_tokens=_optional_int(_first_value(primary, fallback, "deepseek_max_tokens", "max_tokens")),
        stream_include_usage=_optional_bool(
            _first_value(primary, fallback, "deepseek_stream_include_usage", "stream_include_usage"),
            default=True,
        ),
        user_id=_first_text(primary, fallback, "deepseek_user_id", "user_id"),
        strict_tool_schema=_optional_bool(
            _first_value(primary, fallback, "deepseek_strict_tool_schema", "strict_tool_schema"),
            default=False,
        ),
        retry_max_attempts=_optional_int(_first_value(primary, fallback, "deepseek_retry_max_attempts", "retry_max_attempts")) or 2,
        retry_backoff_seconds=_optional_float(
            _first_value(primary, fallback, "deepseek_retry_backoff_seconds", "retry_backoff_seconds"),
            default=0.25,
        ),
        json_empty_retry_attempts=_optional_int(
            _first_value(primary, fallback, "deepseek_json_empty_retry_attempts", "json_empty_retry_attempts")
        )
        or 1,
    )


def _first_value(primary: dict[str, Any], fallback: dict[str, Any], *keys: str) -> Any:
    for source in (primary, fallback):
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip() != "":
                return value
    return ""


def _first_text(primary: dict[str, Any], fallback: dict[str, Any], *keys: str) -> str:
    return str(_first_value(primary, fallback, *keys) or "").strip()


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_float(value: Any, *, default: float) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _explicit_task_id_from_user_text(content: str) -> str | None:
    text = _strip_paths(str(content or ""))
    upper_text = text.upper()
    if "OTA" in upper_text or "在线升级" in text:
        return "ota_fill"
    if "地标" in text:
        return "landmark_fill"
    if "上海数据" in text or "上海平台" in text or "数据平台" in text or "数据中心" in text:
        return "shanghaiData_fill"
    return None


def _strip_paths(content: str) -> str:
    text = re.sub(r"[<《][^<>《》]+?[>》]", "", content)
    text = re.sub(r"[\"“][^\"”]+?\.xls(?:x|m)?[\"”]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]:.*?\.xls(?:x|m)?", "", text, flags=re.IGNORECASE)
    return text


def _clean_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(settings.get("provider") or "").strip(),
        "base_url": str(settings.get("base_url") or "").strip(),
        "model": str(settings.get("model") or "").strip(),
        "api_key": str(settings.get("api_key") or "").strip(),
        "request_timeout_seconds": str(settings.get("request_timeout_seconds") or settings.get("timeout_seconds") or "").strip(),
        "timeout_seconds": str(settings.get("timeout_seconds") or "").strip(),
        "deepseek_thinking": str(settings.get("deepseek_thinking") or settings.get("thinking") or "").strip(),
        "deepseek_reasoning_effort": str(settings.get("deepseek_reasoning_effort") or settings.get("reasoning_effort") or "").strip(),
        "deepseek_max_tokens": str(settings.get("deepseek_max_tokens") or settings.get("max_tokens") or "").strip(),
        "deepseek_stream_include_usage": settings.get("deepseek_stream_include_usage", settings.get("stream_include_usage", "")),
        "deepseek_user_id": str(settings.get("deepseek_user_id") or settings.get("user_id") or "").strip(),
        "deepseek_strict_tool_schema": settings.get("deepseek_strict_tool_schema", settings.get("strict_tool_schema", "")),
        "deepseek_retry_max_attempts": str(settings.get("deepseek_retry_max_attempts") or settings.get("retry_max_attempts") or "").strip(),
        "deepseek_retry_backoff_seconds": str(settings.get("deepseek_retry_backoff_seconds") or settings.get("retry_backoff_seconds") or "").strip(),
        "deepseek_json_empty_retry_attempts": str(
            settings.get("deepseek_json_empty_retry_attempts") or settings.get("json_empty_retry_attempts") or ""
        ).strip(),
    }


def _coerce_timeout(value: Any, *, default: float = 180.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _default_base_url(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == "deepseek":
        return "https://api.deepseek.com"
    if normalized in {"openai", "openai_compatible"}:
        return "https://api.openai.com/v1"
    if normalized == "openrouter":
        return "https://openrouter.ai/api/v1"
    return ""


def _chat_completions_endpoint(base_url: str) -> str:
    clean = str(base_url or "").rstrip("/")
    if not clean:
        raise ModelRuntimeError("model_config_invalid", "模型配置缺少 base_url。")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def _request_base_url(config: ProviderConfig, payload: dict[str, Any]) -> str:
    if _is_deepseek_config(config) and _payload_uses_strict_tools(payload):
        return _deepseek_beta_base_url(config.base_url)
    return config.base_url


def _deepseek_beta_base_url(base_url: str) -> str:
    clean = str(base_url or "").rstrip("/")
    if clean.endswith("/chat/completions"):
        clean = clean[: -len("/chat/completions")]
    if clean.endswith("/beta"):
        return clean
    if clean == "https://api.deepseek.com":
        return f"{clean}/beta"
    return clean


def _is_deepseek_config(config: ProviderConfig) -> bool:
    return str(config.provider or "").strip().lower() == "deepseek"


def _deepseek_thinking(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"enabled", "disabled"} else "enabled"


def _deepseek_reasoning_effort(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"max", "xhigh"}:
        return "max"
    return "high"


def _clean_user_id(value: str) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_-]{1,512}", text) else ""


def _request_attempts(config: ProviderConfig) -> int:
    if not _is_deepseek_config(config):
        return 1
    return max(1, int(config.deepseek.retry_max_attempts or 1))


def _retry_delay(config: ProviderConfig, attempt: int) -> float:
    if not _is_deepseek_config(config):
        return 0
    base = max(0.0, float(config.deepseek.retry_backoff_seconds or 0))
    return base * (2**attempt)


def _should_retry_http(status: int, attempt: int, config: ProviderConfig) -> bool:
    return status in {429, 500, 503} and attempt + 1 < _request_attempts(config)


def _should_retry_connection(attempt: int, config: ProviderConfig) -> bool:
    return attempt + 1 < _request_attempts(config)


def _payload_uses_strict_tools(payload: dict[str, Any]) -> bool:
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) and isinstance(tool.get("function"), dict) else {}
        if function.get("strict") is True:
            return True
    return False


def _deepseek_strict_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strict_tools: list[dict[str, Any]] = []
    for tool in tools:
        cloned = json.loads(json.dumps(tool, ensure_ascii=False))
        function = cloned.get("function") if isinstance(cloned.get("function"), dict) else {}
        function["strict"] = True
        function["parameters"] = _deepseek_strict_schema(function.get("parameters") or {"type": "object", "properties": {}})
        strict_tools.append(cloned)
    return strict_tools


def _deepseek_strict_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    result = {key: value for key, value in schema.items() if key not in {"nullable", "minLength", "maxLength", "minItems", "maxItems"}}
    if "anyOf" in result and isinstance(result["anyOf"], list):
        result["anyOf"] = [_deepseek_strict_schema(item) for item in result["anyOf"]]
    schema_type = result.get("type")
    if schema_type == "object" or isinstance(result.get("properties"), dict):
        properties = result.get("properties") if isinstance(result.get("properties"), dict) else {}
        result["type"] = "object"
        result["properties"] = {str(key): _deepseek_strict_schema(value) for key, value in properties.items()}
        result["required"] = list(result["properties"].keys())
        result["additionalProperties"] = False
        return result
    if schema_type == "array" and "items" in result:
        result["items"] = _deepseek_strict_schema(result["items"])
    return result


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelRuntimeError("model_bad_response", _short_error(f"模型没有返回合法 JSON：{content}")) from exc
    if not isinstance(payload, dict):
        raise ModelRuntimeError("model_bad_response", "模型 JSON 顶层必须是对象。")
    return payload


def _message_content(body: dict[str, Any]) -> str:
    _raise_for_response_finish_reason(body)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRuntimeError("model_bad_response", "模型响应缺少 choices[0].message.content。") from exc
    return "" if content is None else str(content)


def _raise_for_response_finish_reason(body: dict[str, Any]) -> None:
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        return
    first = choices[0] if isinstance(choices[0], dict) else {}
    _raise_for_choice_finish_reason(first)


def _raise_for_choice_finish_reason(choice: dict[str, Any]) -> None:
    reason = str(choice.get("finish_reason") or "")
    if reason == "length":
        raise ModelRuntimeError("model_output_truncated", "模型输出达到 max_tokens 或上下文长度限制，内容可能被截断。")
    if reason == "content_filter":
        raise ModelRuntimeError("model_content_filtered", "模型输出被内容安全策略过滤。")
    if reason == "insufficient_system_resource":
        raise ModelRuntimeError("model_insufficient_system_resource", "DeepSeek 后端推理资源不足，请稍后重试。")


def _parse_tool_turn(body: dict[str, Any]) -> ModelToolTurn:
    _raise_for_response_finish_reason(body)
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelRuntimeError("model_bad_response", "模型响应缺少 choices[0].message。") from exc
    if not isinstance(message, dict):
        raise ModelRuntimeError("model_bad_response", "模型响应 message 必须是对象。")
    content = "" if message.get("content") is None else str(message.get("content") or "")
    reasoning_content = "" if message.get("reasoning_content") is None else str(message.get("reasoning_content") or "")
    tool_calls = []
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise ModelRuntimeError("model_bad_response", "模型 tool_calls 必须是数组。")
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        raw_arguments = str(function.get("arguments") or "{}")
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ModelRuntimeError("model_bad_tool_call", f"模型工具参数不是合法 JSON：{_short_error(raw_arguments)}") from exc
        if not isinstance(arguments, dict):
            raise ModelRuntimeError("model_bad_tool_call", f"模型工具参数必须是对象：{name}")
        tool_calls.append(
            ModelToolCall(
                id=str(raw.get("id") or f"call_{len(tool_calls) + 1}"),
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return ModelToolTurn(content=content, tool_calls=tool_calls, reasoning_content=reasoning_content, usage=usage)


def _iter_sse_json(response: Any):
    data_lines: list[str] = []
    while True:
        raw_line = response.readline()
        if raw_line == b"" or raw_line == "":
            if data_lines:
                parsed = _parse_sse_data(data_lines)
                if parsed is not None:
                    yield parsed
            return
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                parsed = _parse_sse_data(data_lines)
                data_lines = []
                if parsed is not None:
                    yield parsed
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())


def _parse_sse_data(data_lines: list[str]) -> dict[str, Any] | None:
    data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return None
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ModelRuntimeError("model_bad_response", "模型流式响应事件必须是 JSON 对象。")
    return payload


def _stream_choices(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not body:
        return []
    choices = body.get("choices")
    if not isinstance(choices, list):
        return []
    return [choice for choice in choices if isinstance(choice, dict)]


def _merge_tool_call_delta(tool_buffers: dict[int, dict[str, str]], raw_call: Any) -> None:
    if not isinstance(raw_call, dict):
        return
    try:
        index = int(raw_call.get("index") or 0)
    except (TypeError, ValueError):
        index = len(tool_buffers)
    buffer = tool_buffers.setdefault(
        index,
        {
            "id": f"call_{index + 1}",
            "type": "function",
            "name": "",
            "arguments": "",
        },
    )
    if raw_call.get("id"):
        buffer["id"] = str(raw_call.get("id") or buffer["id"])
    if raw_call.get("type"):
        buffer["type"] = str(raw_call.get("type") or "function")
    function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
    name_delta = function.get("name")
    if name_delta:
        buffer["name"] = f"{buffer.get('name', '')}{str(name_delta)}"
    if function.get("arguments") is not None:
        buffer["arguments"] = f"{buffer.get('arguments', '')}{str(function.get('arguments') or '')}"


def _stream_buffers_to_response(
    content_parts: list[str],
    tool_buffers: dict[int, dict[str, str]],
    reasoning_parts: list[str] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool_calls = []
    for index in sorted(tool_buffers):
        item = tool_buffers[index]
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        tool_calls.append(
            {
                "id": str(item.get("id") or f"call_{index + 1}"),
                "type": str(item.get("type") or "function"),
                "function": {
                    "name": name,
                    "arguments": str(item.get("arguments") or "{}"),
                },
            }
        )
    return {
        "choices": [
            {
                "message": {
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts or []),
                    "tool_calls": tool_calls,
                }
            }
        ],
        "usage": usage or {},
    }


def _normalize_column(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if not re.fullmatch(r"[A-Z]{1,3}", text):
        raise ModelRuntimeError("model_intent_rejected", f"模型返回的值所在列非法：{text}")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


def _short_error(message: str, *, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", str(message)).strip()
    return text[:limit]
