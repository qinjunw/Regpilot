from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .resources import skills_root
from .source_documents import DocumentSourceStore


MANIFEST_BLOCK_RE = re.compile(r"```json\s+regpilot_manifest\s*(?P<body>\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
REGPILOT_STATE_FILE = ".regpilot.json"
SKILL_LOCATIONS = ("builtin", "installed", "drafts")
SUPPORTED_SKILL_TYPES = {"ai_workflow", "action_skill"}
SUPPORTED_INGEST_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xls", ".xlsm", ".md", ".txt"}
RESOURCE_DIRS = ("reference", "references", "scripts", "assets", "agents")


def load_builtin_agent_skills(root: str | Path | None = None) -> list[dict[str, Any]]:
    return load_available_agent_skills(root)


def load_available_agent_skills(root: str | Path | None = None) -> list[dict[str, Any]]:
    root_path = _skills_root(root)
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, location in _iter_skill_files(root_path, include_drafts=False):
        inspected = _inspect_skill_file(root_path, path, location)
        if not inspected.get("enabled"):
            continue
        skill_id = str(inspected.get("id") or "")
        if skill_id in seen:
            continue
        seen.add(skill_id)
        if inspected.get("skill_type") == "action_skill":
            raw = inspected.get("manifest") if isinstance(inspected.get("manifest"), dict) else {}
            skills.append(_normalize_manifest(raw, path, location=location, skill_type="action_skill"))
        elif inspected.get("skill_type") == "ai_workflow":
            skills.append(_normalize_ai_workflow(inspected, path, location))
    return skills


def inspect_skill_source(
    *,
    root: str | Path | None = None,
    path: str | Path | None = None,
    skill_id: str | None = None,
) -> dict[str, Any]:
    root_path = _skills_root(root)
    collection = _skill_collection_result(root_path, path)
    if collection is not None:
        return collection
    skill_file, location = _resolve_skill_file(root_path, path=path, skill_id=skill_id, include_drafts=True)
    inspected = _inspect_skill_file(root_path, skill_file, location)
    validation = _validate_inspected_skill(inspected)
    return {key: value for key, value in inspected.items() if key != "manifest"} | {"validation": validation}


def validate_skill_source(
    *,
    root: str | Path | None = None,
    path: str | Path | None = None,
    skill_id: str | None = None,
) -> dict[str, Any]:
    root_path = _skills_root(root)
    collection = _skill_collection_result(root_path, path)
    if collection is not None:
        return {
            "ok": False,
            "code": "skill_collection_requires_selection",
            "location": "collection",
            "errors": ["skill_collection_requires_selection"],
            "warnings": [],
            "candidates": collection["candidates"],
            "message": collection["message"],
        }
    skill_file, location = _resolve_skill_file(root_path, path=path, skill_id=skill_id, include_drafts=True)
    inspected = _inspect_skill_file(root_path, skill_file, location)
    validation = _validate_inspected_skill(inspected)
    return {
        "ok": validation["ok"],
        "skill_id": inspected.get("id") or "",
        "title": inspected.get("title") or "",
        "skill_type": inspected.get("skill_type") or "",
        "location": location,
        "errors": validation["errors"],
        "warnings": validation["warnings"],
    }


def create_skill_draft(
    *,
    root: str | Path | None = None,
    slug: str,
    name: str,
    title: str,
    description: str,
    skill_type: str = "ai_workflow",
) -> dict[str, Any]:
    root_path = _skills_root(root)
    clean_slug = _safe_slug(slug)
    clean_name = _safe_skill_id(name)
    clean_title = _single_line(title)
    clean_description = _single_line(description)
    clean_type = str(skill_type or "").strip()
    if clean_type not in SUPPORTED_SKILL_TYPES:
        raise ValueError(f"Unsupported skill_type: {skill_type}")
    if not clean_name or not clean_title or not clean_description:
        raise ValueError("Draft skill requires name, title, and description.")
    draft_dir = _ensure_within(root_path / "drafts", root_path / "drafts" / clean_slug)
    if draft_dir.exists():
        raise ValueError(f"Draft skill already exists: {clean_slug}")
    draft_dir.mkdir(parents=True, exist_ok=False)
    skill_text = _draft_skill_text(clean_name, clean_title, clean_description, clean_type)
    (draft_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
    _write_skill_state(
        draft_dir,
        {
            "enabled": False,
            "skill_type": clean_type,
            "status": "draft",
            "id": clean_name,
            "title": clean_title,
        },
    )
    return {
        "ok": True,
        "skill_id": clean_name,
        "title": clean_title,
        "skill_type": clean_type,
        "location": "drafts",
        "skill_dir": str(draft_dir),
        "skill_path": str(draft_dir / "SKILL.md"),
        "enabled": False,
    }


def install_skill(
    *,
    root: str | Path | None = None,
    path: str | Path | None = None,
    skill_id: str | None = None,
) -> dict[str, Any]:
    root_path = _skills_root(root)
    collection = _skill_collection_result(root_path, path)
    if collection is not None:
        return {
            "ok": False,
            "code": "skill_collection_requires_selection",
            "location": "collection",
            "candidates": collection["candidates"],
            "message": collection["message"],
        }
    source_file, source_location = _resolve_skill_file(root_path, path=path, skill_id=skill_id, include_drafts=True)
    inspected = _inspect_skill_file(root_path, source_file, source_location)
    validation = _validate_inspected_skill(inspected)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))
    if source_location == "installed":
        return {
            "ok": True,
            "skill_id": str(inspected.get("id") or ""),
            "title": str(inspected.get("title") or ""),
            "skill_type": str(inspected.get("skill_type") or ""),
            "location": "installed",
            "skill_dir": str(source_file.parent),
            "skill_path": str(source_file),
            "enabled": bool(inspected.get("enabled", False)),
        }
    target_dir = _ensure_within(root_path / "installed", root_path / "installed" / _safe_slug(source_file.parent.name))
    if target_dir.exists():
        installed_file = target_dir / "SKILL.md"
        if installed_file.exists():
            installed = _inspect_skill_file(root_path, installed_file, "installed")
            return {
                "ok": True,
                "skill_id": str(installed.get("id") or ""),
                "title": str(installed.get("title") or ""),
                "skill_type": str(installed.get("skill_type") or ""),
                "location": "installed",
                "skill_dir": str(target_dir),
                "skill_path": str(installed_file),
                "resources": _resource_inventory(target_dir),
                "trust": _trust_state(_read_skill_state(target_dir)),
                "enabled": bool(installed.get("enabled", False)),
            }
        raise ValueError(f"Installed skill already exists: {target_dir.name}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    _copy_skill_dir(source_file.parent, target_dir)
    _write_skill_state(
        target_dir,
        {
            "enabled": False,
            "skill_type": str(inspected.get("skill_type") or "ai_workflow"),
            "status": "installed",
            "id": str(inspected.get("id") or ""),
            "title": str(inspected.get("title") or ""),
        },
    )
    return {
        "ok": True,
        "skill_id": str(inspected.get("id") or ""),
        "title": str(inspected.get("title") or ""),
        "skill_type": str(inspected.get("skill_type") or ""),
        "location": "installed",
        "skill_dir": str(target_dir),
        "skill_path": str(target_dir / "SKILL.md"),
        "resources": _resource_inventory(target_dir),
        "trust": _trust_state({}),
        "enabled": False,
    }


def enable_skill(
    *,
    root: str | Path | None = None,
    skill_id: str,
) -> dict[str, Any]:
    root_path = _skills_root(root)
    skill_file, location = _resolve_skill_file(root_path, skill_id=skill_id, include_drafts=False)
    inspected = _inspect_skill_file(root_path, skill_file, location)
    if location not in {"builtin", "installed"}:
        if inspected.get("enabled"):
            return {
                "ok": True,
                "skill_id": str(inspected.get("id") or ""),
                "title": str(inspected.get("title") or ""),
                "skill_type": str(inspected.get("skill_type") or ""),
                "location": location,
                "enabled": True,
                "skill_dir": str(skill_file.parent),
                "message": "Skill is already available.",
            }
        return {
            "ok": False,
            "code": "skill_requires_install",
            "skill_id": str(inspected.get("id") or ""),
            "title": str(inspected.get("title") or ""),
            "skill_type": str(inspected.get("skill_type") or ""),
            "location": location,
            "enabled": False,
            "skill_dir": str(skill_file.parent),
            "message": "Skill must be installed before it can be enabled.",
            "next_tool": "regpilot_install_skill",
        }
    validation = _validate_inspected_skill(inspected)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))
    state = _read_skill_state(skill_file.parent)
    state.update(
        {
            "enabled": True,
            "skill_type": str(inspected.get("skill_type") or "ai_workflow"),
            "status": "available",
            "id": str(inspected.get("id") or ""),
            "title": str(inspected.get("title") or ""),
        }
    )
    _write_skill_state(skill_file.parent, state)
    return {
        "ok": True,
        "skill_id": str(inspected.get("id") or ""),
        "title": str(inspected.get("title") or ""),
        "skill_type": str(inspected.get("skill_type") or ""),
        "location": location,
        "enabled": True,
        "skill_dir": str(skill_file.parent),
    }


def rename_skill(
    *,
    root: str | Path | None = None,
    skill_id: str,
    display_name: str,
) -> dict[str, Any]:
    root_path = _skills_root(root)
    clean_display_name = _clean_skill_display_name(display_name)
    skill_file, location = _resolve_skill_file_by_identifier(root_path, skill_id, include_drafts=False)
    if location not in {"builtin", "installed"}:
        raise ValueError("Skill must be builtin or installed before it can be renamed.")
    inspected = _inspect_skill_file(root_path, skill_file, location)
    validation = _validate_inspected_skill(inspected)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["errors"]))
    _ensure_display_name_is_unique(root_path, str(inspected.get("id") or ""), clean_display_name)
    state = _read_skill_state(skill_file.parent)
    state.update(
        {
            "enabled": bool(inspected.get("enabled", False)),
            "skill_type": str(inspected.get("skill_type") or "ai_workflow"),
            "status": str(inspected.get("status") or "available"),
            "id": str(inspected.get("id") or ""),
            "title": str(inspected.get("title") or ""),
            "display_name": clean_display_name,
        }
    )
    _write_skill_state(skill_file.parent, state)
    return {
        "ok": True,
        "skill_id": str(inspected.get("id") or ""),
        "title": str(inspected.get("title") or ""),
        "display_name": clean_display_name,
        "skill_type": str(inspected.get("skill_type") or ""),
        "location": location,
        "enabled": bool(inspected.get("enabled", False)),
        "skill_dir": str(skill_file.parent),
    }


def load_agent_skill(
    *,
    root: str | Path | None = None,
    skill_id: str,
) -> dict[str, Any]:
    root_path = _skills_root(root)
    skills = load_available_agent_skills(root_path)
    skill = _find_skill_by_identifier(skills, skill_id)
    if skill is None:
        raise ValueError(f"Skill is not enabled or does not exist: {skill_id}")
    skill_file = Path(str(skill.get("skill_path") or ""))
    instructions = skill_file.read_text(encoding="utf-8")
    references = _reference_records(skill_file.parent, [str(item) for item in skill.get("reference_paths") or []])
    return {
        "ok": True,
        "skill_id": str(skill.get("id") or ""),
        "title": str(skill.get("title") or ""),
        "display_name": _skill_display_name(skill),
        "description": str(skill.get("description") or ""),
        "skill_type": str(skill.get("skill_type") or ""),
        "location": str(skill.get("location") or ""),
        "instructions": instructions,
        "references": references,
        "resources": skill.get("resources") if isinstance(skill.get("resources"), dict) else _resource_inventory(skill_file.parent),
        "trust": skill.get("trust") if isinstance(skill.get("trust"), dict) else _trust_state({}),
        "source": {
            "skill_path": str(skill_file),
            "reference_paths": [record["path"] for record in references],
        },
    }


def resolve_skill_identifier(identifier: str, skills: list[dict[str, Any]]) -> str:
    skill = _find_skill_by_identifier(skills, identifier)
    if skill is None:
        raise ValueError(f"Skill is not available: {identifier}")
    return str(skill.get("id") or "")


def ingest_sources_placeholder(
    *,
    root: str | Path | None = None,
    skill_id: str,
    source_paths: list[str],
) -> dict[str, Any]:
    load_agent_skill(root=root, skill_id=skill_id)
    root_path = _skills_root(root)
    result = DocumentSourceStore(root_path.parent / ".source_documents").ingest_source_paths(source_paths)
    result["skill_id"] = str(skill_id or "")
    return result


def operator_skill_list(skills: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "id": str(skill["id"]),
            "title": _skill_display_name(skill),
            "source_title": str(skill["title"]),
            "display_name": _skill_display_name(skill),
            "description": str(skill.get("description") or ""),
            "category": str(skill.get("category") or "general"),
            "status": str(skill.get("status") or "available"),
            "risk_level": str(skill.get("risk_level") or "medium"),
            "requires_confirmation": bool(skill.get("requires_confirmation", False)),
        }
        for skill in (skills if skills is not None else load_builtin_agent_skills())
        if str(skill.get("status") or "") != "hidden"
    ]


def select_skill_candidates(
    content: str,
    *,
    model_intent: dict[str, Any] | None = None,
    skills: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    text = _strip_paths(str(content or ""))
    result = []
    for skill in _skill_source(skills):
        if str(skill.get("status") or "") not in {"available", "pending_integration"}:
            continue
        if _matches_task_intent(skill, model_intent) or _matches_text_intent(skill, text):
            result.append(skill)
    return result


def build_skill_command(
    content: str,
    model_intent: dict[str, Any] | None = None,
    *,
    skills: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    candidates = select_skill_candidates(content, model_intent=model_intent, skills=skills)
    if not candidates:
        return None
    skill = candidates[0]
    policy = _run_policy(content, model_intent, skill)
    command = skill.get("command") if isinstance(skill.get("command"), dict) else {}
    inputs = {
        "task_id": str(skill.get("task_id") or ""),
        "auto_advance_policy": policy,
    }
    if model_intent:
        inputs.update(_validated_skill_inputs(model_intent))
    return {
        "type": "skill_command",
        "skill_id": str(skill["id"]),
        "skill_title": _skill_display_name(skill),
        "goal": str(command.get("default_goal") or "run_until_stop"),
        "run_policy": policy,
        "regulatory_tool": str(command.get("regulatory_tool") or "formfill_run_until_stop"),
        "inputs": inputs,
        "intent_source": "model" if model_intent else "rules",
    }


def _skill_source(skills: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return skills if skills is not None else load_builtin_agent_skills()


def _load_regpilot_manifest(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)
    if frontmatter.get("regpilot_skill") is not True:
        return None
    match = MANIFEST_BLOCK_RE.search(text)
    if match is None:
        raise ValueError(f"RegPilot Agent Skill missing regpilot_manifest block: {path}")
    raw = json.loads(match.group("body"))
    if not isinstance(raw, dict):
        raise ValueError(f"RegPilot Agent Skill manifest must be a JSON object: {path}")
    return raw


def _frontmatter(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, Any] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            continue
        if value.casefold() == "true":
            data[key] = True
        elif value.casefold() == "false":
            data[key] = False
        else:
            data[key] = value
    return data


def _normalize_manifest(
    raw: dict[str, Any],
    path: Path,
    *,
    location: str = "legacy",
    skill_type: str = "action_skill",
) -> dict[str, Any]:
    state = _read_skill_state(path.parent)
    frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
    skill = dict(raw)
    skill["id"] = str(raw.get("id") or "").strip()
    skill["name"] = str(frontmatter.get("name") or skill["id"])
    skill["title"] = str(raw.get("title") or "").strip()
    skill["display_name"] = str(state.get("display_name") or skill["title"]).strip()
    skill["skill_type"] = skill_type
    skill["location"] = location
    skill["task_id"] = str(raw.get("task_id") or "").strip()
    skill["triggers"] = _string_list(raw.get("triggers"))
    skill["intent_keywords"] = _string_list(raw.get("intent_keywords"))
    skill["required_inputs"] = _string_list(raw.get("required_inputs"))
    skill["default_inputs"] = raw.get("default_inputs") if isinstance(raw.get("default_inputs"), dict) else {}
    skill["allowed_tools"] = _string_list(raw.get("allowed_tools"))
    skill["allowed_run_policies"] = _string_list(raw.get("allowed_run_policies"))
    skill["operation_nodes"] = [node for node in raw.get("operation_nodes") or [] if isinstance(node, dict)]
    skill["reference_paths"] = _string_list(raw.get("references"))
    skill["reference_text"] = _reference_text(path.parent, skill["reference_paths"])
    skill["skill_path"] = str(path)
    skill["skill_dir"] = str(path.parent)
    skill["resources"] = _resource_inventory(path.parent)
    skill["trust"] = _trust_state(state)
    skill["manifest_path"] = str(path)
    if not skill["id"] or not skill["title"]:
        raise ValueError(f"Invalid RegPilot Agent Skill manifest: {path}")
    return skill


def _normalize_ai_workflow(inspected: dict[str, Any], path: Path, location: str) -> dict[str, Any]:
    reference_paths = _string_list(inspected.get("reference_paths"))
    return {
        "id": str(inspected.get("id") or ""),
        "title": str(inspected.get("title") or ""),
        "display_name": str(inspected.get("display_name") or inspected.get("title") or ""),
        "description": str(inspected.get("description") or ""),
        "category": str(inspected.get("category") or "workflow"),
        "status": str(inspected.get("status") or "available"),
        "risk_level": str(inspected.get("risk_level") or "medium"),
        "requires_confirmation": bool(inspected.get("requires_confirmation", False)),
        "skill_type": "ai_workflow",
        "location": location,
        "reference_paths": reference_paths,
        "reference_text": _reference_text(path.parent, reference_paths),
        "skill_path": str(path),
        "skill_dir": str(path.parent),
        "resources": inspected.get("resources") if isinstance(inspected.get("resources"), dict) else {},
        "trust": inspected.get("trust") if isinstance(inspected.get("trust"), dict) else _trust_state({}),
        "allowed_tools": [],
        "operation_nodes": [],
        "triggers": _string_list(inspected.get("triggers")),
        "intent_keywords": _string_list(inspected.get("intent_keywords")),
    }


def _skills_root(root: str | Path | None = None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else skills_root()


def _iter_skill_files(root: Path, *, include_drafts: bool) -> list[tuple[Path, str]]:
    results: list[tuple[Path, str]] = []
    for location in ("builtin", "installed"):
        location_dir = root / location
        if location_dir.exists():
            results.extend((path, location) for path in sorted(location_dir.glob("*/SKILL.md")))
    if root.exists():
        for path in sorted(root.glob("*/SKILL.md")):
            if path.parent.name not in SKILL_LOCATIONS:
                results.append((path, "legacy"))
    if include_drafts:
        drafts_dir = root / "drafts"
        if drafts_dir.exists():
            results.extend((path, "drafts") for path in sorted(drafts_dir.glob("*/SKILL.md")))
    return results


def _skill_collection_result(root: Path, path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    raw_path = Path(_clean_skill_path_argument(path)).expanduser()
    if not raw_path.is_absolute():
        raw_path = _ensure_within(root, root / raw_path)
    if not raw_path.exists() or not raw_path.is_dir() or (raw_path / "SKILL.md").exists():
        return None
    candidates = _skill_collection_candidates(root, raw_path)
    if not candidates:
        return None
    return {
        "ok": True,
        "location": "collection",
        "status": "selection_required",
        "path": str(raw_path),
        "message": "Skill path contains multiple skill packages; choose one skill directory or SKILL.md.",
        "candidates": candidates,
        "validation": {
            "ok": False,
            "errors": ["skill_collection_requires_selection"],
            "warnings": [],
        },
    }


def _skill_collection_candidates(root: Path, collection_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for skill_file in sorted(collection_dir.rglob("SKILL.md")):
        try:
            relative = skill_file.relative_to(collection_dir)
        except ValueError:
            continue
        if len(relative.parts) not in {2, 3}:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        resolved = skill_file.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        location = _location_for_path(root, skill_file)
        inspected = _inspect_skill_file(root, skill_file, location)
        candidates.append(
            {
                "skill_id": str(inspected.get("id") or ""),
                "title": str(inspected.get("title") or ""),
                "skill_type": str(inspected.get("skill_type") or ""),
                "location": location,
                "status": str(inspected.get("status") or ""),
                "enabled": bool(inspected.get("enabled", False)),
                "skill_dir": str(skill_file.parent),
                "skill_path": str(skill_file),
            }
        )
    return candidates


def _resolve_skill_file(
    root: Path,
    *,
    path: str | Path | None = None,
    skill_id: str | None = None,
    include_drafts: bool,
) -> tuple[Path, str]:
    if path is not None:
        raw_path = Path(_clean_skill_path_argument(path)).expanduser()
        if not raw_path.is_absolute():
            raw_path = _ensure_within(root, root / raw_path)
        skill_file = raw_path / "SKILL.md" if raw_path.is_dir() else raw_path
        if skill_file.name != "SKILL.md":
            raise ValueError("Skill path must point to a skill directory or SKILL.md.")
        if not skill_file.exists():
            raise ValueError(f"Skill file does not exist: {skill_file}")
        return skill_file.resolve(), _location_for_path(root, skill_file)
    target_id = str(skill_id or "").strip()
    if not target_id:
        raise ValueError("skill_id or path is required.")
    for candidate, location in _iter_skill_files(root, include_drafts=include_drafts):
        inspected = _inspect_skill_file(root, candidate, location)
        if target_id in {str(inspected.get("id") or ""), candidate.parent.name}:
            return candidate, location
    raise ValueError(f"Skill not found: {target_id}")


def _resolve_skill_file_by_identifier(root: Path, identifier: str, *, include_drafts: bool) -> tuple[Path, str]:
    try:
        return _resolve_skill_file(root, skill_id=identifier, include_drafts=include_drafts)
    except ValueError:
        target = str(identifier or "").strip()
        if not target:
            raise
    matches: list[tuple[Path, str]] = []
    normalized = _normalized_skill_identifier(target)
    for candidate, location in _iter_skill_files(root, include_drafts=include_drafts):
        inspected = _inspect_skill_file(root, candidate, location)
        aliases = {_normalized_skill_identifier(value) for value in _skill_identifier_values(inspected)}
        if normalized and normalized in aliases:
            matches.append((candidate, location))
    if len(matches) > 1:
        raise ValueError(f"Skill name is ambiguous: {identifier}")
    if matches:
        return matches[0]
    raise ValueError(f"Skill not found: {identifier}")


def _clean_skill_path_argument(path: str | Path) -> str:
    text = str(path or "").strip().strip("\ufeff")
    if not text:
        return text
    text = text.replace("\u200b", "")
    text = _strip_common_path_wrappers(text)
    if "\r" in text or "\n" in text:
        text = re.sub(r"[\r\n]+", "", text).strip()
        text = _strip_common_path_wrappers(text)
    return text


def _strip_common_path_wrappers(text: str) -> str:
    pairs = (
        ('"', '"'),
        ("'", "'"),
        ("`", "`"),
        ("<", ">"),
        ("“", "”"),
        ("‘", "’"),
    )
    cleaned = text.strip()
    for _ in range(4):
        before = cleaned
        for left, right in pairs:
            if cleaned.startswith(left) and cleaned.endswith(right) and len(cleaned) >= len(left) + len(right):
                cleaned = cleaned[len(left) : len(cleaned) - len(right)].strip()
                break
        if cleaned == before:
            return cleaned
    return cleaned


def _location_for_path(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return "external"
    if relative.parts:
        first = relative.parts[0]
        if first in SKILL_LOCATIONS:
            return first
    return "legacy"


def _inspect_skill_file(root: Path, path: Path, location: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)
    state = _read_skill_state(path.parent)
    manifest = None
    manifest_error = ""
    match = MANIFEST_BLOCK_RE.search(text)
    if match is not None:
        try:
            parsed = json.loads(match.group("body"))
            if isinstance(parsed, dict):
                manifest = parsed
            else:
                manifest_error = "regpilot_manifest must be a JSON object."
        except json.JSONDecodeError as exc:
            manifest_error = f"regpilot_manifest is invalid JSON: {exc}"
    elif frontmatter.get("regpilot_skill") is True:
        manifest_error = "RegPilot action skill is missing regpilot_manifest block."

    skill_type = str(state.get("skill_type") or (manifest or {}).get("skill_type") or "").strip()
    if not skill_type:
        skill_type = "action_skill" if frontmatter.get("regpilot_skill") is True or manifest is not None else "ai_workflow"
    skill_id = str(state.get("id") or (manifest or {}).get("id") or frontmatter.get("name") or path.parent.name).strip()
    title = str(state.get("title") or (manifest or {}).get("title") or _first_heading(text) or skill_id).strip()
    display_name = str(state.get("display_name") or title).strip()
    description = str((manifest or {}).get("description") or frontmatter.get("description") or "").strip()
    manifest_enabled = frontmatter.get("regpilot_skill") is True and manifest is not None and location != "drafts"
    enabled = bool(state.get("enabled", manifest_enabled)) and location != "drafts"
    reference_paths = _string_list((manifest or {}).get("references")) or _discover_reference_paths(path.parent, text)
    status = str(
        state.get("status")
        or (manifest or {}).get("status")
        or _default_skill_status(location=location, enabled=enabled, skill_type=skill_type)
    ).strip()
    return {
        "ok": True,
        "id": skill_id,
        "name": str(frontmatter.get("name") or skill_id),
        "title": title,
        "display_name": display_name,
        "description": description,
        "skill_type": skill_type,
        "enabled": enabled,
        "status": status,
        "location": location,
        "category": str((manifest or {}).get("category") or state.get("category") or "workflow"),
        "risk_level": str((manifest or {}).get("risk_level") or state.get("risk_level") or "medium"),
        "requires_confirmation": bool((manifest or {}).get("requires_confirmation", state.get("requires_confirmation", False))),
        "skill_dir": str(path.parent),
        "skill_path": str(path),
        "reference_paths": reference_paths,
        "resources": _resource_inventory(path.parent),
        "trust": _trust_state(state),
        "has_regpilot_manifest": manifest is not None,
        "manifest_error": manifest_error,
        "manifest": manifest or {},
        "triggers": _string_list((manifest or {}).get("triggers")),
        "intent_keywords": _string_list((manifest or {}).get("intent_keywords")),
    }


def _default_skill_status(*, location: str, enabled: bool, skill_type: str) -> str:
    if enabled:
        return "available"
    if location == "drafts":
        return "draft"
    if location == "installed":
        return "installed"
    if location in {"legacy", "external"} and skill_type == "ai_workflow":
        return "local_source"
    return "installed"


def _validate_inspected_skill(inspected: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    skill_type = str(inspected.get("skill_type") or "")
    if skill_type not in SUPPORTED_SKILL_TYPES:
        errors.append(f"Unsupported skill_type: {skill_type or '(blank)'}")
    if not str(inspected.get("id") or "").strip():
        errors.append("Skill id/name is required.")
    if not str(inspected.get("title") or "").strip():
        errors.append("Skill title or first heading is required.")
    if skill_type == "action_skill":
        if not inspected.get("has_regpilot_manifest"):
            errors.append(str(inspected.get("manifest_error") or "Action skill requires regpilot_manifest."))
        manifest = inspected.get("manifest") if isinstance(inspected.get("manifest"), dict) else {}
        for key in ("id", "title", "allowed_tools"):
            if not manifest.get(key):
                errors.append(f"Action skill manifest requires {key}.")
    if skill_type == "ai_workflow" and inspected.get("has_regpilot_manifest"):
        warnings.append("ai_workflow skill has a regpilot_manifest block; it will be loaded as workflow guidance only.")
    resources = inspected.get("resources") if isinstance(inspected.get("resources"), dict) else {}
    if resources.get("scripts"):
        warnings.append("scripts_require_trust")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _read_skill_state(skill_dir: Path) -> dict[str, Any]:
    state_path = skill_dir / REGPILOT_STATE_FILE
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_skill_state(skill_dir: Path, state: dict[str, Any]) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / REGPILOT_STATE_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_within(root: Path, path: Path) -> Path:
    root_resolved = root.resolve()
    path_resolved = path.resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path is outside allowed skills root: {path}") from exc
    return path_resolved


def _safe_slug(value: str) -> str:
    slug = str(value or "").strip().lower().replace("\\", "/").split("/")[-1]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", slug):
        raise ValueError(f"Invalid skill slug: {value}")
    return slug


def _safe_skill_id(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", text):
        raise ValueError(f"Invalid skill name/id: {value}")
    return text


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_skill_display_name(value: str) -> str:
    text = _single_line(value)
    if not text:
        raise ValueError("Skill display_name is required.")
    if len(text) > 80:
        raise ValueError("Skill display_name must be 80 characters or fewer.")
    return text


def _skill_display_name(skill: dict[str, Any]) -> str:
    return str(skill.get("display_name") or skill.get("title") or skill.get("id") or "").strip()


def _normalized_skill_identifier(value: str) -> str:
    return _single_line(value).casefold()


def _skill_identifier_values(skill: dict[str, Any]) -> list[str]:
    values = [
        str(skill.get("id") or ""),
        str(skill.get("name") or ""),
        str(skill.get("display_name") or ""),
        str(skill.get("title") or ""),
    ]
    return [value for value in values if value.strip()]


def _find_skill_by_identifier(skills: list[dict[str, Any]], identifier: str) -> dict[str, Any] | None:
    target = str(identifier or "").strip()
    if not target:
        return None
    for skill in skills:
        if target == str(skill.get("id") or ""):
            return skill
    normalized = _normalized_skill_identifier(target)
    matches = [
        skill
        for skill in skills
        if normalized and normalized in {_normalized_skill_identifier(value) for value in _skill_identifier_values(skill)}
    ]
    if len(matches) > 1:
        raise ValueError(f"Skill name is ambiguous: {identifier}")
    return matches[0] if matches else None


def _ensure_display_name_is_unique(root: Path, skill_id: str, display_name: str) -> None:
    normalized = _normalized_skill_identifier(display_name)
    for skill_file, location in _iter_skill_files(root, include_drafts=False):
        inspected = _inspect_skill_file(root, skill_file, location)
        if str(inspected.get("id") or "") == skill_id:
            continue
        aliases = {_normalized_skill_identifier(value) for value in _skill_identifier_values(inspected)}
        if normalized in aliases:
            raise ValueError(f"Skill display_name conflicts with another skill: {display_name}")


def _draft_skill_text(name: str, title: str, description: str, skill_type: str) -> str:
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if skill_type == "action_skill":
        lines.append("regpilot_skill: true")
    lines.extend(["---", "", f"# {title}", "", description, ""])
    if skill_type == "action_skill":
        lines.extend(
            [
                "```json regpilot_manifest",
                json.dumps(
                    {
                        "id": name,
                        "title": title,
                        "description": description,
                        "skill_type": "action_skill",
                        "status": "draft",
                        "allowed_tools": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _copy_skill_dir(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=False)
    for item in source_dir.rglob("*"):
        relative = item.relative_to(source_dir)
        if item.name == REGPILOT_STATE_FILE:
            continue
        target = target_dir / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not _should_copy_skill_file(relative):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _should_copy_skill_file(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if any(part.startswith(".") for part in parts):
        return False
    if relative.name == "SKILL.md":
        return True
    if parts[0] in RESOURCE_DIRS:
        return True
    return relative.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".txt"}


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _discover_reference_paths(skill_dir: Path, text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"`(?P<path>references?/[^`]+?\.md)`", text):
        paths.append(match.group("path").replace("\\", "/"))
    for dirname in ("reference", "references"):
        reference_dir = skill_dir / dirname
        if reference_dir.exists():
            for path in sorted(reference_dir.glob("*.md")):
                relative = path.relative_to(skill_dir).as_posix()
                if relative not in paths:
                    paths.append(relative)
    return paths


def _reference_records(skill_dir: Path, reference_paths: list[str]) -> list[dict[str, str]]:
    records = []
    for reference_path in reference_paths:
        path = skill_dir / reference_path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.append({"path": str(path), "content": text})
    return records


def _resource_inventory(skill_dir: Path) -> dict[str, list[str]]:
    inventory = {"references": [], "scripts": [], "assets": [], "agents": []}
    for dirname, key in (
        ("reference", "references"),
        ("references", "references"),
        ("scripts", "scripts"),
        ("assets", "assets"),
        ("agents", "agents"),
    ):
        root = skill_dir / dirname
        if not root.exists():
            continue
        for item in sorted(path for path in root.rglob("*") if path.is_file()):
            if item.name.startswith("."):
                continue
            relative = item.relative_to(skill_dir).as_posix()
            if relative not in inventory[key]:
                inventory[key].append(relative)
    return inventory


def _trust_state(state: dict[str, Any]) -> dict[str, bool]:
    trust = state.get("trust") if isinstance(state.get("trust"), dict) else {}
    return {
        "scripts_trusted": bool(trust.get("scripts_trusted", False)),
    }


def validate_agent_turn_decision(decision: dict[str, Any], candidate_skills: list[dict[str, Any]]) -> dict[str, Any]:
    decision_type = str(decision.get("decision_type") or decision.get("type") or "").strip()
    if decision_type == "skill_command" and isinstance(decision.get("skill_command"), dict):
        nested = dict(decision["skill_command"])
        nested["decision_type"] = "skill_command"
        decision = nested
    if decision_type in {"chat", "no_action", "input_request"}:
        return {
            "type": decision_type,
            "content": str(decision.get("content") or decision.get("message") or ""),
            "questions": _string_list(decision.get("questions")),
        }
    if decision_type != "skill_command":
        raise ValueError(f"未知 Agent Turn Decision：{decision_type or '(blank)'}")
    if str(decision.get("node_id") or "").strip():
        raise ValueError("模型不能在默认填报命令中直接指定内部 Skill Operation Node。")

    skill_identifier = str(decision.get("skill_id") or decision.get("skill_name") or decision.get("display_name") or "").strip()
    skill = _find_skill_by_identifier(candidate_skills, skill_identifier)
    if skill is None:
        raise ValueError(f"模型选择了候选列表之外的 skill：{skill_identifier or '(blank)'}")
    skill_id = str(skill.get("id") or "")

    command = skill.get("command") if isinstance(skill.get("command"), dict) else {}
    goal = str(command.get("default_goal") or "run_until_stop")
    tool = str(decision.get("regulatory_tool") or command.get("regulatory_tool") or "").strip()
    if tool not in set(skill.get("allowed_tools") or []):
        raise ValueError(f"模型返回了未授权 Regulatory Tool：{tool or '(blank)'}")

    policy = str(decision.get("run_policy") or skill.get("run_policy_default") or "until_before_final_submit").strip()
    if policy not in set(skill.get("allowed_run_policies") or []):
        raise ValueError(f"模型返回了未知 Workflow Run Policy：{policy}")

    raw_inputs = decision.get("inputs") if isinstance(decision.get("inputs"), dict) else {}
    inputs = _validated_skill_inputs(skill.get("default_inputs") if isinstance(skill.get("default_inputs"), dict) else {})
    inputs.update(_validated_skill_inputs(raw_inputs))
    inputs["task_id"] = str(skill.get("task_id") or "")
    inputs["auto_advance_policy"] = policy
    return {
        "type": "skill_command",
        "skill_id": skill_id,
        "skill_title": _skill_display_name(skill),
        "goal": goal,
        "run_policy": policy,
        "regulatory_tool": tool,
        "inputs": inputs,
        "intent_source": "model_turn_decision",
    }


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]


def _reference_text(skill_dir: Path, reference_paths: list[str]) -> str:
    parts = []
    for reference_path in reference_paths:
        path = skill_dir / reference_path
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _validated_skill_inputs(raw_inputs: dict[str, Any]) -> dict[str, Any]:
    allowed = {"workbook_path", "workspace_dir", "sheet", "value_column", "attachment_folder"}
    inputs: dict[str, Any] = {}
    if "value_column" not in raw_inputs and "column" in raw_inputs:
        raw_inputs = {**raw_inputs, "value_column": raw_inputs.get("column")}
    for key in allowed:
        value = raw_inputs.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            inputs[key] = text
    if "value_column" in inputs:
        value_column = str(inputs["value_column"]).upper()
        if not re.fullmatch(r"[A-Z]{1,3}", value_column):
            raise ValueError(f"模型返回的值所在列非法：{value_column}")
        inputs["value_column"] = value_column
    return inputs


def _matches_task_intent(skill: dict[str, Any], model_intent: dict[str, Any] | None) -> bool:
    if not model_intent:
        return False
    return str(model_intent.get("task_id") or "") == str(skill.get("task_id") or "")


def _matches_text_intent(skill: dict[str, Any], text: str) -> bool:
    normalized = text.casefold()
    display_name = _skill_display_name(skill)
    has_trigger = bool(display_name and display_name.casefold() in normalized)
    has_trigger = has_trigger or any(trigger.casefold() in normalized for trigger in skill.get("triggers") or [])
    has_action = any(keyword.casefold() in normalized for keyword in skill.get("intent_keywords") or [])
    return has_trigger and has_action


def _run_policy(content: str, model_intent: dict[str, Any] | None, skill: dict[str, Any]) -> str:
    policy = str((model_intent or {}).get("auto_advance_policy") or "").strip()
    if not policy:
        policy = _policy_from_text(content) or str(skill.get("run_policy_default") or "until_before_final_submit")
    allowed = set(skill.get("allowed_run_policies") or [])
    if allowed and policy not in allowed:
        return str(skill.get("run_policy_default") or "until_before_final_submit")
    return policy


def _policy_from_text(content: str) -> str | None:
    text = str(content or "")
    if any(token in text for token in ("只填当前页", "手动", "不要自动下一步")):
        return "disabled"
    if any(token in text for token in ("直到阻塞", "遇到阻塞")):
        return "until_blocked"
    return None


def _strip_paths(content: str) -> str:
    text = re.sub(r"[<《][^<>《》]+?[>》]", "", content)
    text = re.sub(r"[\"“][^\"”]+?\.xls(?:x|m)?[\"”]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[A-Za-z]:.*?\.xls(?:x|m)?", "", text, flags=re.IGNORECASE)
    return text
