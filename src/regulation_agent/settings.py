from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROVIDER_SETTING_DEFAULTS: dict[str, str] = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-pro",
    "api_key": "",
}

DEEPSEEK_SETTING_DEFAULTS: dict[str, Any] = {
    "request_timeout_seconds": "660",
    "deepseek_thinking": "enabled",
    "deepseek_reasoning_effort": "high",
    "deepseek_max_tokens": "",
    "deepseek_stream_include_usage": True,
    "deepseek_user_id": "",
    "deepseek_strict_tool_schema": False,
    "deepseek_retry_max_attempts": "2",
    "deepseek_retry_backoff_seconds": "0.25",
    "deepseek_json_empty_retry_attempts": "1",
}


def default_state_dir() -> Path:
    explicit = os.environ.get("REGULATION_AGENT_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "RegPilot"
    return Path.home() / ".regpilot"


class ProviderSettingsStore:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = state_dir or default_state_dir()
        self.path = self.state_dir / "provider_settings.json"

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.load()
        api_key = str(payload.get("api_key") or "")
        settings = {
            "provider": str(payload.get("provider") or "").strip(),
            "base_url": str(payload.get("base_url") or "").strip(),
            "model": str(payload.get("model") or "").strip(),
            "api_key": api_key if api_key else current.get("api_key", ""),
        }
        for key, default in DEEPSEEK_SETTING_DEFAULTS.items():
            if key in payload:
                settings[key] = _clean_setting_value(payload.get(key), default=default)
            else:
                settings[key] = current.get(key, default)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.public_view()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {**PROVIDER_SETTING_DEFAULTS, **DEEPSEEK_SETTING_DEFAULTS}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            "provider": str(data.get("provider") or PROVIDER_SETTING_DEFAULTS["provider"]),
            "base_url": str(data.get("base_url") or PROVIDER_SETTING_DEFAULTS["base_url"]),
            "model": str(data.get("model") or PROVIDER_SETTING_DEFAULTS["model"]),
            "api_key": str(data.get("api_key") or ""),
            **{key: _clean_setting_value(data.get(key, default), default=default) for key, default in DEEPSEEK_SETTING_DEFAULTS.items()},
        }

    def public_view(self) -> dict[str, Any]:
        settings = self.load()
        key = settings.get("api_key") or ""
        return {
            "provider": settings["provider"],
            "base_url": settings["base_url"],
            "model": settings["model"],
            "has_api_key": bool(key),
            "api_key_masked": _mask_secret(key),
            **{key: settings[key] for key in DEEPSEEK_SETTING_DEFAULTS},
        }


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-4:]}"


def _clean_setting_value(value: Any, *, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
        return default
    return str(value if value is not None else default).strip()
