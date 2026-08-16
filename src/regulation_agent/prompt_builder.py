from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Literal

PromptMode = Literal["chat", "tool_loop", "turn_router"]

_MODE_FILES: dict[str, str] = {
    "chat": "modes/chat.md",
    "tool_loop": "modes/tool_loop.md",
    "turn_router": "modes/turn_router.md",
}


def build_system_prompt(mode: PromptMode) -> str:
    """Build the RegPilot system prompt from shared layers and a mode overlay."""

    mode_file = _MODE_FILES.get(mode)
    if mode_file is None:
        raise ValueError(f"Unknown RegPilot prompt mode: {mode}")

    layers = ["identity.md"]
    if mode != "turn_router":
        layers.extend(["capabilities.md", "style.md"])
    layers.append(mode_file)
    return "\n\n".join(_prompt_text(path) for path in layers).strip()


@lru_cache(maxsize=16)
def _prompt_text(resource_name: str) -> str:
    return (
        resources.files("regulation_agent.prompts")
        .joinpath(resource_name)
        .read_text(encoding="utf-8")
        .strip()
    )
