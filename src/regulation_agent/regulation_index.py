from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INDEXABLE_SUFFIXES = {".md", ".txt", ".html", ".htm", ".json", ".jsonl", ".csv", ".docx", ".xlsx", ".pdf"}
ATTACHMENT_SUFFIXES = INDEXABLE_SUFFIXES | {".doc", ".xls", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".zip"}
TEXT_FIRST_SUFFIXES = {".md", ".txt", ".html", ".htm", ".json", ".jsonl", ".csv"}
EXCLUDED_DIRECTORY_NAMES = {"_cleanup-manifests", "__pycache__", ".git", ".pytest_cache"}
MAX_STAGE_SOURCES = 20000
DEFAULT_STAGE_SOURCES = 200
MAX_NEXT_SOURCES = 50
MAX_ENTRIES_PER_SOURCE = 100
EXPORT_FORMATS = {"json", "csv"}
SOURCE_STATUS_VALUES = {"unprocessed", "processed", "processed_with_no_entries"}
CONFIDENCE_VALUES = {"high", "medium", "low", "unknown"}
STATUS_ORDER = {
    "未知": 0,
    "立项": 1,
    "征求意见": 2,
    "报批稿": 3,
    "发布": 4,
    "实施": 5,
    "废止": 6,
    "替代": 6,
}
MAIN_SOURCE_SUFFIX_PRIORITY = {
    ".md": 0,
    ".txt": 1,
    ".json": 2,
    ".jsonl": 3,
    ".csv": 4,
    ".html": 5,
    ".htm": 6,
    ".docx": 7,
    ".xlsx": 8,
    ".pdf": 9,
}


class RegulationIndexStore:
    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir).expanduser() / "regulation_index"
        self.registry_path = self.root / "registry.json"
        self.processed_dir = self.root / "processed"
        self.unprocessed_dir = self.root / "unprocessed"
        self.default_export_dir = Path(state_dir).expanduser() / "artifacts" / "regulation_index"
        for directory in (self.root, self.processed_dir, self.unprocessed_dir, self.default_export_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def stage_sources(
        self,
        source_paths: list[str],
        *,
        recursive: bool = True,
        max_sources: int = DEFAULT_STAGE_SOURCES,
    ) -> dict[str, Any]:
        registry = self._read_registry()
        clean_paths = [_clean_path(value) for value in source_paths if str(value or "").strip()]
        if not clean_paths:
            raise ValueError("source_paths is required.")

        limit = _bounded_int(max_sources, default=DEFAULT_STAGE_SOURCES, minimum=1, maximum=MAX_STAGE_SOURCES)
        source_by_path = _source_by_path(registry)
        candidates = _candidate_source_articles(clean_paths, recursive=recursive)
        staged_count = 0
        already_staged_count = 0
        already_processed_count = 0
        duplicate_input_count = 0
        unsupported_count = 0
        unsupported_samples = []
        seen_articles: set[str] = set()
        truncated = False

        for candidate in candidates:
            resolved = str(candidate.get("resolved_path") or "")
            if resolved in seen_articles:
                duplicate_input_count += 1
                continue
            seen_articles.add(resolved)
            main_path = Path(str(candidate.get("main_path") or ""))
            suffix = main_path.suffix.lower()
            if suffix not in INDEXABLE_SUFFIXES:
                unsupported_count += 1
                if len(unsupported_samples) < 10:
                    unsupported_samples.append({"path": str(main_path), "suffix": suffix, "reason": "unsupported_suffix"})
                continue

            existing_for_path = source_by_path.get(resolved)
            if existing_for_path and _is_processed_status(str(existing_for_path.get("status") or "")):
                already_processed_count += 1
                continue

            if staged_count >= limit:
                truncated = True
                break

            record = _source_record(candidate, existing=existing_for_path)
            if existing_for_path and str(existing_for_path.get("status") or "") == "unprocessed":
                already_staged_count += 1
            else:
                staged_count += 1
            registry["sources"][record["source_id"]] = record
            self._write_source_state(record)

        self._write_registry(registry)
        next_sources = self._next_unprocessed(registry, limit=MAX_NEXT_SOURCES)
        return {
            "ok": True,
            "registry_id": registry["registry_id"],
            "registry_path": str(self.registry_path),
            "staged_count": staged_count,
            "already_staged_count": already_staged_count,
            "already_processed_count": already_processed_count,
            "duplicate_input_count": duplicate_input_count,
            "unsupported_count": unsupported_count,
            "unsupported_samples": unsupported_samples,
            "truncated": truncated,
            "next_sources": next_sources,
            "message": (
                f"已登记 {staged_count} 个待整理来源文章，已有 {already_staged_count} 个仍待整理，"
                f"{already_processed_count} 个已整理来源已跳过。"
            ),
        }

    def record_entries(
        self,
        *,
        source_id: str = "",
        source_path: str = "",
        source_status: str = "processed",
        entries: list[dict[str, Any]] | None = None,
        collection_id: str = "",
        source_title: str = "",
        source_url: str = "",
        message: str = "",
    ) -> dict[str, Any]:
        registry = self._read_registry()
        source = self._resolve_source(registry, source_id=source_id, source_path=source_path)
        clean_status = str(source_status or "processed").strip()
        if clean_status not in SOURCE_STATUS_VALUES:
            raise ValueError("source_status must be processed, processed_with_no_entries, or unprocessed.")
        raw_entries = entries if isinstance(entries, list) else []
        if len(raw_entries) > MAX_ENTRIES_PER_SOURCE:
            raise ValueError(f"entries exceeds per-source limit: {MAX_ENTRIES_PER_SOURCE}")

        now = _utc_now()
        created_count = 0
        updated_count = 0
        duplicate_count = 0
        regulation_ids = []
        evidence_ids: set[str] = set()
        for raw_entry in raw_entries:
            normalized = _normalize_entry(raw_entry, source=source, collection_id=collection_id, now=now)
            for evidence_id in normalized["source_evidence_ids"]:
                evidence_ids.add(evidence_id)
            existing_id = _resolve_regulation_id(registry, normalized)
            if existing_id and existing_id in registry["regulations"]:
                duplicate_count += 1
                updated_count += 1
                regulation = _merge_regulation(registry["regulations"][existing_id], normalized, now=now)
                registry["regulations"][existing_id] = regulation
                _index_regulation_identity(registry, regulation)
                regulation_ids.append(existing_id)
                continue
            regulation_id = "regidx_" + hashlib.sha256(normalized["identity_key"].encode("utf-8")).hexdigest()[:16]
            normalized["regulation_id"] = regulation_id
            registry["regulations"][regulation_id] = normalized
            _index_regulation_identity(registry, normalized)
            regulation_ids.append(regulation_id)
            created_count += 1

        source["status"] = clean_status
        source["source_title"] = _single_line(source_title) or str(source.get("source_title") or "")
        source["source_url"] = _single_line(source_url) or str(source.get("source_url") or "")
        source["message"] = _single_line(message)
        source["regulation_ids"] = sorted(set(regulation_ids))
        source["entry_ids"] = source["regulation_ids"]
        source["collection_id"] = _single_line(collection_id)
        source["source_evidence_ids"] = sorted(evidence_ids)
        source["updated_at"] = now
        if _is_processed_status(clean_status):
            source["processed_at"] = now
        registry["sources"][source["source_id"]] = source
        self._write_source_state(source)
        self._write_registry(registry)

        return {
            "ok": True,
            "registry_id": registry["registry_id"],
            "source_id": source["source_id"],
            "source_status": source["status"],
            "entry_ids": sorted(set(regulation_ids)),
            "regulation_ids": sorted(set(regulation_ids)),
            "created_count": created_count,
            "updated_count": updated_count,
            "duplicate_count": duplicate_count,
            "registry_path": str(self.registry_path),
            "message": f"来源文章已标记为 {source['status']}，新增 {created_count} 条，更新 {updated_count} 条。",
        }

    def export_index(
        self,
        *,
        output_dir: str | Path | None = None,
        formats: list[str] | None = None,
        filename: str = "regulation_index",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        registry = self._read_registry()
        target_dir = Path(output_dir).expanduser() if output_dir else self.default_export_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        clean_formats = _clean_formats(formats)
        base_name = _safe_filename(filename or "regulation_index")
        snapshot = _export_snapshot(registry)
        artifacts = []
        for fmt in clean_formats:
            path = target_dir / f"{base_name}.{fmt}"
            if not overwrite:
                path = _unique_path(path)
            if fmt == "json":
                path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            elif fmt == "csv":
                _write_csv(path, snapshot["regulations"])
            artifacts.append({"format": fmt, "path": str(path)})
        return {
            "ok": True,
            "registry_id": registry["registry_id"],
            "entry_count": len(snapshot["regulations"]),
            "source_count": len(snapshot["source_articles"]),
            "artifacts": artifacts,
            "message": f"已导出 {len(snapshot['regulations'])} 条法规索引记录。",
        }

    def _read_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {
                "schema_version": 1,
                "registry_id": "regulation_index_default",
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "sources": {},
                "regulations": {},
                "identity_index": {},
            }
        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("regulation index registry is invalid.")
        data.setdefault("schema_version", 1)
        data.setdefault("registry_id", "regulation_index_default")
        data.setdefault("sources", {})
        data.setdefault("regulations", data.pop("entries", {}) if isinstance(data.get("entries"), dict) else {})
        data.setdefault("identity_index", data.pop("dedupe", {}) if isinstance(data.get("dedupe"), dict) else {})
        return data

    def _write_registry(self, registry: dict[str, Any]) -> None:
        registry["updated_at"] = _utc_now()
        self.registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_source_state(self, source: dict[str, Any]) -> None:
        source_id = str(source.get("source_id") or "")
        if not source_id:
            return
        processed_path = self.processed_dir / f"{source_id}.json"
        unprocessed_path = self.unprocessed_dir / f"{source_id}.json"
        if _is_processed_status(str(source.get("status") or "")):
            processed_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
            if unprocessed_path.exists():
                unprocessed_path.unlink()
            return
        unprocessed_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
        if processed_path.exists():
            processed_path.unlink()

    def _next_unprocessed(self, registry: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        records = [
            source
            for source in registry.get("sources", {}).values()
            if isinstance(source, dict) and str(source.get("status") or "") == "unprocessed"
        ]
        records.sort(key=lambda source: (str(source.get("first_seen_at") or ""), str(source.get("path") or "")))
        return [_public_source(source) for source in records[: max(1, min(limit, MAX_NEXT_SOURCES))]]

    def _resolve_source(self, registry: dict[str, Any], *, source_id: str, source_path: str) -> dict[str, Any]:
        clean_id = str(source_id or "").strip()
        if clean_id and clean_id in registry["sources"]:
            return dict(registry["sources"][clean_id])
        clean_path = _resolved_path(Path(_clean_path(source_path))) if str(source_path or "").strip() else ""
        if clean_path:
            for source in registry["sources"].values():
                if not isinstance(source, dict):
                    continue
                if clean_path in {
                    str(source.get("resolved_path") or ""),
                    str(source.get("main_resolved_path") or ""),
                    str(source.get("article_dir_resolved") or ""),
                }:
                    return dict(source)
        raise ValueError("source_id or source_path must identify a staged regulation source.")


def _candidate_source_articles(raw_paths: list[str], *, recursive: bool) -> list[dict[str, Any]]:
    files: list[Path] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser()
        if path.is_file():
            if not _is_excluded_path(path):
                files.append(path)
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            files.extend(item for item in iterator if item.is_file() and not _is_excluded_path(item))
    files.sort(key=lambda path: (0 if path.suffix.lower() in TEXT_FIRST_SUFFIXES else 1, str(path).lower()))
    resolved_by_path = {path: _resolved_path(path) for path in files}
    direct_files_by_parent: dict[str, list[Path]] = {}
    for path in files:
        direct_files_by_parent.setdefault(_resolved_path(path.parent), []).append(path)
    claimed: set[str] = set()
    articles: list[dict[str, Any]] = []

    for entry_path in [path for path in files if path.name.lower() == "entry.json"]:
        article_dir = entry_path.parent
        article_files = [item for item in files if _is_relative_to(item, article_dir)]
        claimed.update(resolved_by_path[item] for item in article_files)
        articles.append(_article_candidate(main_path=entry_path, article_dir=article_dir, attachments=_article_attachments(entry_path, article_dir)))

    for detail_path in [path for path in files if path.name.lower() in {"detail.html", "detail.htm"}]:
        if resolved_by_path[detail_path] in claimed:
            continue
        article_dir = detail_path.parent
        article_files = [item for item in files if _is_relative_to(item, article_dir) and item.name.lower() not in {"entry.json"}]
        claimed.update(resolved_by_path[item] for item in article_files)
        articles.append(_article_candidate(main_path=detail_path, article_dir=article_dir, attachments=_article_attachments(detail_path, article_dir)))

    collected_body_by_dir: dict[str, Path] = {}
    for path in files:
        if resolved_by_path[path] in claimed:
            continue
        if path.name.lower() not in {"正文.md", "正文.html", "正文.htm"}:
            continue
        key = _resolved_path(path.parent)
        existing = collected_body_by_dir.get(key)
        if existing is None or _main_source_sort_key(path) < _main_source_sort_key(existing):
            collected_body_by_dir[key] = path

    for body_path in collected_body_by_dir.values():
        if resolved_by_path[body_path] in claimed:
            continue
        article_dir = body_path.parent
        article_files = direct_files_by_parent.get(_resolved_path(article_dir), [])
        claimed.update(resolved_by_path[item] for item in article_files)
        attachments = [
            _file_attachment(item)
            for item in article_files
            if resolved_by_path[item] != resolved_by_path[body_path] and item.suffix.lower() in ATTACHMENT_SUFFIXES
        ]
        articles.append(_article_candidate(main_path=body_path, article_dir=article_dir, attachments=attachments))

    unclaimed_by_stem: dict[tuple[str, str], list[Path]] = {}
    for path in files:
        if resolved_by_path[path] in claimed:
            continue
        key = (_resolved_path(path.parent), path.stem.lower())
        unclaimed_by_stem.setdefault(key, []).append(path)

    for group in unclaimed_by_stem.values():
        if len(group) < 2:
            continue
        candidate = _same_stem_group_candidate(group)
        if not candidate:
            continue
        claimed.update(resolved_by_path[item] for item in group)
        articles.append(candidate)

    for path in files:
        if resolved_by_path[path] in claimed:
            continue
        articles.append(_article_candidate(main_path=path, article_dir=path.parent, attachments=[]))

    articles.sort(key=lambda item: (0 if Path(str(item.get("main_path") or "")).suffix.lower() in TEXT_FIRST_SUFFIXES else 1, str(item.get("main_path") or "").lower()))
    return articles


def _is_excluded_path(path: Path) -> bool:
    return any(part in EXCLUDED_DIRECTORY_NAMES for part in path.parts)


def _is_processed_status(status: str) -> bool:
    return status in {"processed", "processed_with_no_entries"}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _article_candidate(*, main_path: Path, article_dir: Path, attachments: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "source_kind": "source_article",
        "main_path": str(main_path),
        "main_resolved_path": _resolved_path(main_path),
        "article_dir": str(article_dir),
        "article_dir_resolved": _resolved_path(article_dir),
        "resolved_path": _resolved_path(article_dir if main_path.name.lower() == "entry.json" else main_path),
        "attachments": attachments,
    }


def _same_stem_group_candidate(group: list[Path]) -> dict[str, Any] | None:
    ordered = sorted(group, key=_main_source_sort_key)
    main_path = next((path for path in ordered if path.suffix.lower() in INDEXABLE_SUFFIXES), None)
    if not main_path:
        return None
    attachments = [_file_attachment(item) for item in ordered if _resolved_path(item) != _resolved_path(main_path)]
    return _article_candidate(main_path=main_path, article_dir=main_path.parent, attachments=attachments)


def _main_source_sort_key(path: Path) -> tuple[int, str]:
    return (MAIN_SOURCE_SUFFIX_PRIORITY.get(path.suffix.lower(), 100), str(path).lower())


def _file_attachment(path: Path) -> dict[str, str]:
    return {
        "file_name": path.name,
        "title": "",
        "path_or_url": str(path),
        "file_type": path.suffix.lower().lstrip("."),
        "relationship": "same_stem_alternate_format",
        "parse_status": "not_parsed",
    }


def _article_attachments(main_path: Path, article_dir: Path) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    if main_path.suffix.lower() == ".json":
        try:
            data = json.loads(main_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        raw_items = data.get("attachments") if isinstance(data, dict) else None
        if isinstance(raw_items, list):
            attachments.extend(_normalize_attachments(raw_items))
    files_dir = article_dir / "files"
    if files_dir.exists():
        for item in sorted(files_dir.rglob("*")):
            if item.is_file() and item.suffix.lower() in ATTACHMENT_SUFFIXES:
                attachments.append(_file_attachment(item) | {"relationship": ""})
    return _merge_attachments([], attachments)


def _source_record(candidate: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    main_path = Path(str(candidate.get("main_path") or ""))
    stat = main_path.stat()
    content_hash = _hash_file(main_path)
    resolved_path = str(candidate.get("resolved_path") or _resolved_path(main_path))
    source_id = "regsrc_" + hashlib.sha256(f"{resolved_path}|{content_hash}".encode("utf-8")).hexdigest()[:16]
    now = _utc_now()
    existing = existing or {}
    return {
        "source_id": source_id,
        "source_article_id": source_id,
        "source_kind": "source_article",
        "path": str(main_path),
        "main_path": str(main_path),
        "resolved_path": resolved_path,
        "main_resolved_path": str(candidate.get("main_resolved_path") or _resolved_path(main_path)),
        "article_dir": str(candidate.get("article_dir") or main_path.parent),
        "article_dir_resolved": str(candidate.get("article_dir_resolved") or _resolved_path(main_path.parent)),
        "suffix": main_path.suffix.lower(),
        "size": int(stat.st_size),
        "content_hash": content_hash,
        "status": str(existing.get("status") or "unprocessed"),
        "first_seen_at": str(existing.get("first_seen_at") or now),
        "updated_at": now,
        "processed_at": str(existing.get("processed_at") or ""),
        "entry_ids": list(existing.get("entry_ids") or []),
        "regulation_ids": list(existing.get("regulation_ids") or existing.get("entry_ids") or []),
        "attachments": _merge_attachments(existing.get("attachments"), candidate.get("attachments")),
        "message": str(existing.get("message") or ""),
    }


def _normalize_entry(raw: dict[str, Any], *, source: dict[str, Any], collection_id: str, now: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each entry must be an object.")
    number = _single_line(raw.get("regulation_number") or raw.get("number") or "")
    name = _single_line(raw.get("regulation_name") or raw.get("name") or "")
    if not number and not name:
        raise ValueError("each entry requires regulation_number or regulation_name.")
    status = _single_line(raw.get("regulation_status") or raw.get("status") or "未知")
    status = _canonical_status(status)
    notice_date = _single_line(raw.get("notice_date") or raw.get("announcement_date") or raw.get("start_date") or "")
    comment_deadline = _single_line(raw.get("comment_deadline") or raw.get("deadline_date") or raw.get("end_date") or "")
    effective_date = _single_line(raw.get("effective_date") or raw.get("implementation_date") or "")
    event_date = _single_line(raw.get("event_date") or raw.get("date") or notice_date or effective_date or "")
    source_article_id = _single_line(source.get("source_article_id") or source.get("source_id") or "")
    source_ids = sorted({source_article_id})
    source_paths = sorted({_single_line(source.get("main_path") or source.get("path") or "")})
    identity_key = _identity_key(number=number, name=name)
    weak_identity = not bool(_normalize_identifier(number))
    dates = _dates_from_entry(status=status, event_date=event_date, notice_date=notice_date, comment_deadline=comment_deadline, effective_date=effective_date)
    raw_attachments = raw.get("attachments") if isinstance(raw.get("attachments"), list) else None
    inherited_attachments = [] if raw_attachments is not None else source.get("attachments")
    entry_attachments = _merge_attachments(inherited_attachments, raw_attachments or [])
    entry = {
        "regulation_id": "",
        "identity_key": identity_key,
        "number_identity_key": _number_identity_key(number),
        "name_identity_key": _name_identity_key(name),
        "weak_identity": weak_identity,
        "regulation_number": number,
        "regulation_name": name,
        "regulation_status": status,
        "status_date": event_date,
        "date_type": _single_line(raw.get("date_type") or ""),
        "dates": dates,
        "issuing_body": _single_line(raw.get("issuing_body") or raw.get("agency") or ""),
        "source_article_title": _single_line(raw.get("source_article_title") or raw.get("article_title") or ""),
        "source_url": _single_line(raw.get("source_url") or ""),
        "source_article_ids": source_ids,
        "source_articles": [_source_article_ref(source)],
        "source_ids": source_ids,
        "source_paths": source_paths,
        "collection_ids": sorted({_single_line(collection_id)} - {""}),
        "source_evidence_ids": _string_list(raw.get("source_evidence_ids") or raw.get("evidence_ids") or []),
        "attachments": entry_attachments,
        "status_history": [
            _status_history_item(
                status=status,
                date=event_date,
                date_type=_single_line(raw.get("date_type") or ""),
                source_article_id=source_article_id,
                source_evidence_ids=_string_list(raw.get("source_evidence_ids") or raw.get("evidence_ids") or []),
            )
        ],
        "notes": _single_line(raw.get("notes") or ""),
        "confidence": _clean_confidence(raw.get("confidence")),
        "created_at": now,
        "updated_at": now,
        "occurrence_count": 1,
    }
    return entry


def _merge_regulation(existing: dict[str, Any], incoming: dict[str, Any], *, now: str) -> dict[str, Any]:
    merged = dict(existing)
    for key in ("regulation_number", "regulation_name", "issuing_body", "source_article_title", "source_url", "notes"):
        if not str(merged.get(key) or "").strip() and str(incoming.get(key) or "").strip():
            merged[key] = incoming[key]
    if incoming.get("regulation_number") and not str(merged.get("regulation_number") or "").strip():
        merged["regulation_number"] = incoming["regulation_number"]
        merged["weak_identity"] = False
        merged["identity_key"] = _identity_key(number=str(incoming.get("regulation_number") or ""), name=str(merged.get("regulation_name") or incoming.get("regulation_name") or ""))
        merged["number_identity_key"] = _number_identity_key(str(incoming.get("regulation_number") or ""))
    elif incoming.get("regulation_number"):
        merged["weak_identity"] = False
        merged["number_identity_key"] = _number_identity_key(str(merged.get("regulation_number") or incoming.get("regulation_number") or ""))
    current_status, current_date = _choose_current_status(
        str(merged.get("regulation_status") or ""),
        str(merged.get("status_date") or ""),
        str(incoming.get("regulation_status") or ""),
        str(incoming.get("status_date") or ""),
    )
    merged["regulation_status"] = current_status
    merged["status_date"] = current_date
    for key in ("source_article_ids", "source_ids", "source_paths", "collection_ids", "source_evidence_ids"):
        merged[key] = sorted(set(_string_list(merged.get(key))) | set(_string_list(incoming.get(key))))
    merged["source_articles"] = _merge_source_articles(merged.get("source_articles"), incoming.get("source_articles"))
    merged["dates"] = _merge_dates(merged.get("dates"), incoming.get("dates"))
    merged["status_history"] = _merge_status_history(merged.get("status_history"), incoming.get("status_history"))
    merged["attachments"] = _merge_attachments(merged.get("attachments"), incoming.get("attachments"))
    merged["confidence"] = _best_confidence(str(merged.get("confidence") or "unknown"), str(incoming.get("confidence") or "unknown"))
    merged["updated_at"] = now
    merged["occurrence_count"] = int(merged.get("occurrence_count") or 1) + 1
    return merged


def _normalize_attachments(raw_items: list[Any]) -> list[dict[str, str]]:
    normalized_by_key: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = {
            "file_name": _single_line(raw.get("file_name") or raw.get("filename") or raw.get("name") or ""),
            "title": _single_line(raw.get("title") or ""),
            "path_or_url": _single_line(raw.get("path_or_url") or raw.get("local_path") or raw.get("url") or raw.get("path") or ""),
            "file_type": _single_line(raw.get("file_type") or raw.get("format") or raw.get("type") or ""),
            "relationship": _single_line(raw.get("relationship") or raw.get("relation") or raw.get("kind") or ""),
            "parse_status": _single_line(raw.get("parse_status") or "not_parsed"),
        }
        key = _attachment_identity_key(item)
        if not key:
            continue
        if key in normalized_by_key:
            existing = normalized_by_key[key]
            for field, value in item.items():
                if value and not existing.get(field):
                    existing[field] = value
            continue
        normalized_by_key[key] = item
        order.append(key)
    return [normalized_by_key[key] for key in order]


def _attachment_identity_key(item: dict[str, str]) -> str:
    for field in ("path_or_url", "file_name", "title"):
        normalized = _normalize_identifier(item.get(field, ""))
        if normalized:
            return f"{field}:{normalized}"
    return ""


def _merge_attachments(left: Any, right: Any) -> list[dict[str, str]]:
    items = []
    if isinstance(left, list):
        items.extend(left)
    if isinstance(right, list):
        items.extend(right)
    return _normalize_attachments(items)


def _export_snapshot(registry: dict[str, Any]) -> dict[str, Any]:
    regulations = [dict(entry) for entry in registry.get("regulations", {}).values() if isinstance(entry, dict)]
    sources = [dict(source) for source in registry.get("sources", {}).values() if isinstance(source, dict)]
    regulations.sort(key=lambda entry: (str(entry.get("regulation_number") or ""), str(entry.get("regulation_name") or "")))
    sources.sort(key=lambda source: str(source.get("path") or ""))
    return {
        "schema_version": registry.get("schema_version", 1),
        "registry_id": registry.get("registry_id", "regulation_index_default"),
        "exported_at": _utc_now(),
        "regulations": regulations,
        "source_articles": sources,
    }


def _write_csv(path: Path, regulations: list[dict[str, Any]]) -> None:
    fieldnames = [
        "regulation_id",
        "regulation_number",
        "regulation_name",
        "regulation_status",
        "status_date",
        "project_initiation_date",
        "consultation_start_date",
        "comment_deadline",
        "approval_draft_date",
        "release_date",
        "effective_date",
        "issuing_body",
        "attachment_count",
        "source_article_count",
        "confidence",
        "notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in regulations:
            dates = entry.get("dates") if isinstance(entry.get("dates"), dict) else {}
            row = {key: entry.get(key, "") for key in fieldnames}
            for key in ("project_initiation_date", "consultation_start_date", "comment_deadline", "approval_draft_date", "release_date", "effective_date"):
                row[key] = dates.get(key, "")
            row["attachment_count"] = len(entry.get("attachments", [])) if isinstance(entry.get("attachments"), list) else 0
            row["source_article_count"] = len(entry.get("source_article_ids", [])) if isinstance(entry.get("source_article_ids"), list) else 0
            writer.writerow(row)


def _source_by_path(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for source in registry.get("sources", {}).values():
        if not isinstance(source, dict):
            continue
        for key in ("resolved_path", "main_resolved_path", "article_dir_resolved"):
            resolved = str(source.get(key) or "")
            if resolved:
                result[resolved] = source
    return result


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(source.get("source_id") or ""),
        "source_article_id": str(source.get("source_article_id") or source.get("source_id") or ""),
        "source_kind": str(source.get("source_kind") or "source_article"),
        "path": str(source.get("path") or ""),
        "main_path": str(source.get("main_path") or source.get("path") or ""),
        "suffix": str(source.get("suffix") or ""),
        "status": str(source.get("status") or ""),
        "size": int(source.get("size") or 0),
        "content_hash": str(source.get("content_hash") or ""),
        "entry_ids": list(source.get("entry_ids") or []),
        "regulation_ids": list(source.get("regulation_ids") or source.get("entry_ids") or []),
        "attachments": source.get("attachments") if isinstance(source.get("attachments"), list) else [],
        "message": str(source.get("message") or ""),
    }


def _resolve_regulation_id(registry: dict[str, Any], incoming: dict[str, Any]) -> str:
    index = registry.get("identity_index") if isinstance(registry.get("identity_index"), dict) else {}
    number_key = str(incoming.get("number_identity_key") or "")
    name_key = str(incoming.get("name_identity_key") or "")
    if number_key and number_key in index:
        return str(index[number_key])
    if name_key and name_key in index:
        return str(index[name_key])
    return ""


def _index_regulation_identity(registry: dict[str, Any], regulation: dict[str, Any]) -> None:
    index = registry.setdefault("identity_index", {})
    regulation_id = str(regulation.get("regulation_id") or "")
    for key in (regulation.get("number_identity_key"), regulation.get("name_identity_key"), regulation.get("identity_key")):
        clean = str(key or "")
        if clean and regulation_id:
            index[clean] = regulation_id


def _identity_key(*, number: str, name: str) -> str:
    return _number_identity_key(number) or _name_identity_key(name)


def _number_identity_key(number: str) -> str:
    normalized = _normalize_identifier(number)
    return f"number:{normalized}" if normalized else ""


def _name_identity_key(name: str) -> str:
    normalized = _normalize_identifier(name)
    return f"name:{normalized}" if normalized else ""


def _dates_from_entry(*, status: str, event_date: str, notice_date: str, comment_deadline: str, effective_date: str) -> dict[str, str]:
    dates = {
        "project_initiation_date": "",
        "consultation_start_date": "",
        "comment_deadline": comment_deadline,
        "approval_draft_date": "",
        "release_date": "",
        "effective_date": effective_date,
    }
    if status == "立项":
        dates["project_initiation_date"] = event_date
    elif status == "征求意见":
        dates["consultation_start_date"] = notice_date or event_date
    elif status == "报批稿":
        dates["approval_draft_date"] = event_date
    elif status == "发布":
        dates["release_date"] = event_date
    elif status == "实施" and not dates["effective_date"]:
        dates["effective_date"] = event_date
    return dates


def _merge_dates(left: Any, right: Any) -> dict[str, str]:
    result = {key: "" for key in ("project_initiation_date", "consultation_start_date", "comment_deadline", "approval_draft_date", "release_date", "effective_date")}
    for source in (left, right):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            clean = _single_line(value)
            if clean and (not result.get(key) or clean > result[key]):
                result[key] = clean
    return result


def _status_history_item(*, status: str, date: str, date_type: str, source_article_id: str, source_evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "date": date,
        "date_type": date_type,
        "source_article_id": source_article_id,
        "source_evidence_ids": source_evidence_ids,
    }


def _merge_status_history(left: Any, right: Any) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for source in (left, right):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            clean = {
                "status": _canonical_status(item.get("status")),
                "date": _single_line(item.get("date")),
                "date_type": _single_line(item.get("date_type")),
                "source_article_id": _single_line(item.get("source_article_id")),
                "source_evidence_ids": _string_list(item.get("source_evidence_ids")),
            }
            key = (clean["status"], clean["date"], clean["source_article_id"])
            if clean["status"] and key not in seen:
                result.append(clean)
                seen.add(key)
    result.sort(key=lambda item: (_status_rank(item.get("status")), str(item.get("date") or "")))
    return result


def _choose_current_status(left_status: str, left_date: str, right_status: str, right_date: str) -> tuple[str, str]:
    left = (_status_rank(left_status), left_date or "")
    right = (_status_rank(right_status), right_date or "")
    if right > left:
        return _canonical_status(right_status), right_date
    return _canonical_status(left_status), left_date


def _canonical_status(value: Any) -> str:
    text = _single_line(value)
    if not text:
        return "未知"
    if "废止" in text:
        return "废止"
    if "替代" in text:
        return "替代"
    if "实施" in text or "生效" in text:
        return "实施"
    if "发布" in text or "公布" in text:
        return "发布"
    if "报批" in text:
        return "报批稿"
    if "征求" in text or "意见" in text:
        return "征求意见"
    if "立项" in text or "计划" in text:
        return "立项"
    return text if text in STATUS_ORDER else "未知"


def _status_rank(value: Any) -> int:
    return STATUS_ORDER.get(_canonical_status(value), 0)


def _source_article_ref(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_article_id": str(source.get("source_article_id") or source.get("source_id") or ""),
        "title": str(source.get("source_title") or ""),
        "path": str(source.get("main_path") or source.get("path") or ""),
        "url": str(source.get("source_url") or ""),
    }


def _merge_source_articles(left: Any, right: Any) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for source in (left, right):
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, dict):
                continue
            article_id = _single_line(item.get("source_article_id"))
            key = article_id or _single_line(item.get("path"))
            if key and key not in seen:
                result.append(
                    {
                        "source_article_id": article_id,
                        "title": _single_line(item.get("title")),
                        "path": _single_line(item.get("path")),
                        "url": _single_line(item.get("url")),
                    }
                )
                seen.add(key)
    return result


def _normalize_identifier(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("－", "-").replace("—", "-").replace(" ", "")
    text = text.replace("-", "")
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff.-]+", "", text)
    return text


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser())


def _clean_path(value: Any) -> str:
    text = str(value or "").strip().strip("\ufeff").replace("\u200b", "")
    for left, right in (('"', '"'), ("'", "'"), ("`", "`"), ("<", ">"), ("“", "”"), ("‘", "’")):
        if text.startswith(left) and text.endswith(right) and len(text) >= len(left) + len(right):
            text = text[len(left) : -len(right)].strip()
    return text


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({_single_line(item) for item in value if _single_line(item)})


def _clean_confidence(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in CONFIDENCE_VALUES else "unknown"


def _best_confidence(left: str, right: str) -> str:
    order = {"high": 3, "medium": 2, "low": 1, "unknown": 0}
    return left if order.get(left, 0) >= order.get(right, 0) else right


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _clean_formats(formats: list[str] | None) -> list[str]:
    requested = [str(item).lower().strip() for item in formats] if isinstance(formats, list) else ["json", "csv"]
    clean = []
    for fmt in requested:
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"unsupported export format: {fmt}")
        if fmt not in clean:
            clean.append(fmt)
    return clean or ["json", "csv"]


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
    return text[:80] or "regulation_index"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"cannot allocate export path under {path.parent}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
