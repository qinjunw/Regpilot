from __future__ import annotations

import os
import sys
from pathlib import Path


def project_resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(str(bundled_root)).resolve()
    return Path(__file__).resolve().parents[2]


def static_dir() -> Path:
    override = os.environ.get("REGULATION_AGENT_STATIC_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (project_resource_root() / "static").resolve()


def skills_root() -> Path:
    override = os.environ.get("REGULATION_AGENT_SKILLS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (project_resource_root() / "skills").resolve()


def release_model_config_path() -> Path:
    override = os.environ.get("REGULATION_AGENT_MODEL_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return (project_resource_root() / "config" / "model.default.toml").resolve()
