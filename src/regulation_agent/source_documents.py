from __future__ import annotations

import hashlib
import json
import re
import zipfile
from csv import reader as csv_reader
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SUPPORTED_SOURCE_SUFFIXES = {".md", ".txt", ".html", ".htm", ".json", ".jsonl", ".csv", ".docx", ".xlsx", ".pdf"}
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_EVIDENCE_PER_SOURCE = 1200
DEFAULT_SLICE_CHAR_LIMIT = 4000
MAX_SLICE_CHAR_LIMIT = 64000
DEFAULT_BUNDLE_CHAR_LIMIT = 64000
MAX_BUNDLE_CHAR_LIMIT = 256000


@dataclass(frozen=True)
class _ParsedEvidence:
    text: str
    locator: dict[str, Any]


class DocumentSourceStore:
    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir).expanduser() / "source_documents"
        self.collections_dir = self.root / "collections"
        self.collections_dir.mkdir(parents=True, exist_ok=True)

    def ingest_source_paths(
        self,
        source_paths: list[str],
        *,
        collection_name: str = "",
    ) -> dict[str, Any]:
        clean_paths = [_clean_path(item) for item in source_paths if str(item or "").strip()]
        sources: list[dict[str, Any]] = []
        evidence_records: list[dict[str, Any]] = []

        for raw_path in clean_paths:
            source = self._parse_source(raw_path)
            source_evidence = []
            for index, evidence in enumerate(source.pop("_evidence", []), start=1):
                evidence_id = f"ev_{source['source_id']}_{index:04d}"
                record = {
                    "evidence_id": evidence_id,
                    "source_id": source["source_id"],
                    "path": source["path"],
                    "suffix": source["suffix"],
                    "locator": evidence.locator,
                    "text": evidence.text,
                }
                source_evidence.append(record)
                evidence_records.append(record)
            source["evidence_count"] = len(source_evidence)
            sources.append(source)

        collection_id = _collection_id([source.get("source_id", "") for source in sources])
        collection = {
            "collection_id": collection_id,
            "collection_name": _single_line(collection_name),
            "sources": sources,
            "evidence": evidence_records,
        }
        self._write_collection(collection)
        return {
            "ok": True,
            "collection_id": collection_id,
            "collection_name": collection["collection_name"],
            "source_count": len(sources),
            "evidence_count": len(evidence_records),
            "sources": [_public_source(source) for source in sources],
            "message": _ingest_message(sources, evidence_records),
        }

    def search_sources(self, *, collection_id: str, query: str, top_k: int = 5) -> dict[str, Any]:
        collection = self._read_collection(collection_id)
        terms = _query_terms(query)
        if not terms:
            return {"ok": True, "collection_id": collection["collection_id"], "query": str(query or ""), "results": []}
        ranked = []
        for evidence in collection.get("evidence", []):
            text = str(evidence.get("text") or "")
            score = _score_text(text, terms)
            if score <= 0:
                continue
            ranked.append((score, evidence))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("evidence_id") or "")))
        limit = max(1, min(int(top_k or 5), 20))
        return {
            "ok": True,
            "collection_id": collection["collection_id"],
            "query": str(query or ""),
            "results": [_search_result(evidence, terms, score) for score, evidence in ranked[:limit]],
        }

    def load_source_slice(
        self,
        *,
        collection_id: str,
        evidence_id: str,
        char_limit: int = DEFAULT_SLICE_CHAR_LIMIT,
    ) -> dict[str, Any]:
        collection = self._read_collection(collection_id)
        clean_evidence_id = str(evidence_id or "").strip()
        limit = max(200, min(int(char_limit or DEFAULT_SLICE_CHAR_LIMIT), MAX_SLICE_CHAR_LIMIT))
        for evidence in collection.get("evidence", []):
            if str(evidence.get("evidence_id") or "") != clean_evidence_id:
                continue
            text = str(evidence.get("text") or "")
            truncated = len(text) > limit
            return {
                "ok": True,
                "collection_id": collection["collection_id"],
                "evidence_id": clean_evidence_id,
                "source_id": str(evidence.get("source_id") or ""),
                "path": str(evidence.get("path") or ""),
                "locator": evidence.get("locator") if isinstance(evidence.get("locator"), dict) else {},
                "text": text[:limit],
                "truncated": truncated,
            }
        raise ValueError(f"Source evidence does not exist: {clean_evidence_id}")

    def build_evidence_bundle(
        self,
        *,
        collection_id: str,
        query: str = "",
        queries: list[str] | None = None,
        top_k: int = 6,
        total_char_limit: int = DEFAULT_BUNDLE_CHAR_LIMIT,
    ) -> dict[str, Any]:
        collection = self._read_collection(collection_id)
        clean_queries = _clean_queries(query=query, queries=queries)
        if not clean_queries:
            raise ValueError("At least one evidence query is required.")

        limit = max(400, min(int(total_char_limit or DEFAULT_BUNDLE_CHAR_LIMIT), MAX_BUNDLE_CHAR_LIMIT))
        per_query_limit = max(1, min(int(top_k or 6), 20))
        evidence_by_id = {
            str(item.get("evidence_id") or ""): item
            for item in collection.get("evidence", [])
            if str(item.get("evidence_id") or "").strip()
        }
        ordered_ids: list[str] = []
        coverage = []
        for clean_query in clean_queries:
            result = self.search_sources(collection_id=str(collection.get("collection_id") or ""), query=clean_query, top_k=per_query_limit)
            result_ids = [str(item.get("evidence_id") or "") for item in result.get("results", []) if str(item.get("evidence_id") or "").strip()]
            coverage.append({"query": clean_query, "result_count": len(result_ids), "evidence_ids": result_ids})
            for evidence_id in result_ids:
                if evidence_id not in ordered_ids:
                    ordered_ids.append(evidence_id)

        bundle_lines = [
            "Source Evidence Bundle",
            f"collection_id: {collection.get('collection_id')}",
            "queries: " + "; ".join(clean_queries),
        ]
        truncated = False
        for evidence_id in ordered_ids:
            evidence = evidence_by_id.get(evidence_id)
            if not evidence:
                continue
            header = (
                f"\n[Evidence: {evidence_id}] "
                f"{Path(str(evidence.get('path') or '')).name or evidence.get('path')} "
                f"({_locator_label(evidence.get('locator') if isinstance(evidence.get('locator'), dict) else {})})"
            )
            text = str(evidence.get("text") or "").strip()
            next_prefix = header + "\n"
            remaining = limit - len("\n".join(bundle_lines)) - len(next_prefix)
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                text = text[: max(0, remaining - 14)].rstrip() + "\n[truncated]"
                truncated = True
            bundle_lines.append(next_prefix + text)
            if truncated:
                break

        bundle_text = "\n".join(bundle_lines).strip() + "\n"
        if len(bundle_text) > limit:
            bundle_text = bundle_text[: max(0, limit - 14)].rstrip() + "\n[truncated]\n"
            truncated = True
        included_ids = [evidence_id for evidence_id in ordered_ids if f"[Evidence: {evidence_id}]" in bundle_text]
        return {
            "ok": True,
            "collection_id": str(collection.get("collection_id") or ""),
            "queries": clean_queries,
            "top_k": per_query_limit,
            "total_char_limit": limit,
            "total_chars": len(bundle_text),
            "truncated": truncated,
            "evidence_ids": included_ids,
            "coverage": coverage,
            "bundle_text": bundle_text,
        }

    def read_collection(self, collection_id: str) -> dict[str, Any]:
        return self._read_collection(collection_id)

    def _parse_source(self, raw_path: str) -> dict[str, Any]:
        path = Path(raw_path).expanduser()
        suffix = path.suffix.lower()
        exists = path.exists()
        source_id_seed = str(path.resolve() if exists else path)
        if not exists:
            return _source_record(source_id_seed, path, suffix, "missing", "来源路径不存在。", [])
        if not path.is_file():
            return _source_record(source_id_seed, path, suffix, "unsupported", "当前只支持明确的文件路径，不解析文件夹。", [])
        if suffix not in SUPPORTED_SOURCE_SUFFIXES:
            return _source_record(source_id_seed, path, suffix, "unsupported", "不支持的资料格式。", [])
        size = path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            return _source_record(source_id_seed, path, suffix, "unsupported", "文件超过当前解析大小上限。", [], size=size)
        content_hash = _hash_file(path)
        try:
            evidence = _parse_evidence(path, suffix)
        except Exception as exc:
            return _source_record(content_hash, path, suffix, "parse_failed", f"解析失败：{exc}", [], size=size, content_hash=content_hash)
        evidence = [item for item in evidence if item.text.strip()][:MAX_EVIDENCE_PER_SOURCE]
        if not evidence:
            message = "未提取到文本；不会进行 OCR、远程转换或伪造内容。" if suffix == ".pdf" else "未提取到可用文本。"
            return _source_record(content_hash, path, suffix, "no_text", message, [], size=size, content_hash=content_hash)
        return _source_record(content_hash, path, suffix, "parsed", "已解析为 Source Evidence。", evidence, size=size, content_hash=content_hash)

    def _collection_path(self, collection_id: str) -> Path:
        clean = _safe_id(collection_id)
        if not clean:
            raise ValueError("collection_id is required.")
        return self.collections_dir / f"{clean}.json"

    def _write_collection(self, collection: dict[str, Any]) -> None:
        self._collection_path(str(collection.get("collection_id") or "")).write_text(
            json.dumps(collection, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_collection(self, collection_id: str) -> dict[str, Any]:
        path = self._collection_path(collection_id)
        if not path.exists():
            raise ValueError(f"Source collection does not exist: {collection_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Source collection is invalid: {collection_id}")
        return data


def _parse_evidence(path: Path, suffix: str) -> list[_ParsedEvidence]:
    if suffix in {".md", ".txt"}:
        return _parse_text_file(path)
    if suffix in {".html", ".htm"}:
        return _parse_html(path)
    if suffix == ".json":
        return _parse_json(path)
    if suffix == ".jsonl":
        return _parse_jsonl(path)
    if suffix == ".csv":
        return _parse_csv(path)
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix == ".xlsx":
        return _parse_xlsx(path)
    if suffix == ".pdf":
        return _parse_pdf(path)
    return []


def _parse_text_file(path: Path) -> list[_ParsedEvidence]:
    text = _read_text_with_fallback(path)
    return _line_evidence(text)


def _line_evidence(text: str) -> list[_ParsedEvidence]:
    evidence = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        clean = line.strip()
        if clean:
            evidence.append(_ParsedEvidence(clean, {"type": "line", "start_line": line_number, "end_line": line_number}))
    if evidence:
        return evidence
    stripped = text.strip()
    return [_ParsedEvidence(stripped, {"type": "text"})] if stripped else []


def _parse_html(path: Path) -> list[_ParsedEvidence]:
    parser = _HtmlTextParser()
    parser.feed(_read_text_with_fallback(path))
    return _line_evidence("\n".join(parser.text_parts))


def _parse_json(path: Path) -> list[_ParsedEvidence]:
    text = _read_text_with_fallback(path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _line_evidence(text)
    return _json_evidence(data)


def _parse_jsonl(path: Path) -> list[_ParsedEvidence]:
    evidence = []
    for line_number, line in enumerate(_read_text_with_fallback(path).splitlines(), start=1):
        clean = line.strip()
        if not clean:
            continue
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            evidence.append(_ParsedEvidence(clean, {"type": "jsonl_line", "line": line_number}))
            continue
        for item in _json_evidence(data, locator_prefix={"type": "jsonl_line", "line": line_number}):
            evidence.append(item)
    return evidence


def _parse_csv(path: Path) -> list[_ParsedEvidence]:
    text = _read_text_with_fallback(path)
    evidence = []
    rows = list(csv_reader(text.splitlines()))
    header = rows[0] if rows else []
    for row_number, row in enumerate(rows[1:] if header else rows, start=2 if header else 1):
        values = [str(value).strip() for value in row if str(value).strip()]
        if not values:
            continue
        if header and len(header) == len(row):
            text_value = "；".join(f"{header[index]}: {value}" for index, value in enumerate(row) if str(value).strip())
        else:
            text_value = "；".join(values)
        evidence.append(_ParsedEvidence(text_value, {"type": "csv_row", "row": row_number}))
    return evidence


def _parse_docx(path: Path) -> list[_ParsedEvidence]:
    with zipfile.ZipFile(path) as archive:
        xml_text = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_text)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    evidence = []
    for index, paragraph in enumerate(root.findall(".//w:p", namespace), start=1):
        text = _paragraph_text(paragraph, namespace)
        if text:
            evidence.append(_ParsedEvidence(text, {"type": "docx_paragraph", "paragraph": index}))
    return evidence


def _parse_xlsx(path: Path) -> list[_ParsedEvidence]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_map = _xlsx_sheet_map(archive)
        evidence = []
        for sheet_name, sheet_path in sheet_map:
            try:
                xml_bytes = archive.read(sheet_path)
            except KeyError:
                continue
            evidence.extend(_xlsx_sheet_rows(xml_bytes, sheet_name, shared_strings))
    return evidence


def _parse_pdf(path: Path) -> list[_ParsedEvidence]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return _parse_pdf_plaintext_fallback(path)
    reader = PdfReader(str(path))
    evidence = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = " ".join(str(page.extract_text() or "").split())
        if text:
            evidence.append(_ParsedEvidence(text, {"type": "pdf_page", "page": page_index}))
    return evidence


def _parse_pdf_plaintext_fallback(path: Path) -> list[_ParsedEvidence]:
    raw = path.read_bytes()
    if b"BT" not in raw or b"Tj" not in raw:
        return []
    text = raw.decode("latin-1", errors="ignore")
    values = []
    for match in re.finditer(r"\((?P<text>(?:\\.|[^\\)])*)\)\s*Tj", text):
        value = match.group("text")
        value = value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        clean = " ".join(value.split())
        if clean:
            values.append(clean)
    joined = "\n".join(values).strip()
    return [_ParsedEvidence(joined, {"type": "pdf_text_object"})] if joined else []


def _read_text_with_fallback(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码。")


class _HtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = " ".join(str(data or "").split())
        if clean:
            self.text_parts.append(clean)


def _json_evidence(data: Any, *, locator_prefix: dict[str, Any] | None = None) -> list[_ParsedEvidence]:
    evidence = []
    for index, (path, value) in enumerate(_flatten_json_scalars(data), start=1):
        text = f"{path}: {value}" if path else value
        locator = {"type": "json_value", "path": path, "index": index}
        if locator_prefix:
            locator = {**locator_prefix, "json_path": path, "json_index": index}
        evidence.append(_ParsedEvidence(text, locator))
    return evidence


def _flatten_json_scalars(data: Any, prefix: str = "") -> list[tuple[str, str]]:
    if isinstance(data, dict):
        items: list[tuple[str, str]] = []
        for key, value in data.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_flatten_json_scalars(value, child_prefix))
        return items
    if isinstance(data, list):
        items = []
        for index, value in enumerate(data):
            child_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            items.extend(_flatten_json_scalars(value, child_prefix))
        return items
    if data is None:
        return []
    text = " ".join(str(data).split())
    return [(prefix, text)] if text else []


def _paragraph_text(paragraph: ElementTree.Element, namespace: dict[str, str]) -> str:
    parts = []
    for node in paragraph.iter():
        if node.tag == f"{{{namespace['w']}}}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{{{namespace['w']}}}tab":
            parts.append("\t")
        elif node.tag == f"{{{namespace['w']}}}br":
            parts.append("\n")
    return " ".join("".join(parts).split())


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml_bytes = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ElementTree.fromstring(xml_bytes)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    result = []
    for item in root.findall(".//x:si", namespace):
        texts = [node.text or "" for node in item.findall(".//x:t", namespace)]
        result.append("".join(texts))
    return result


def _xlsx_sheet_map(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    namespace = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    rels = _xlsx_relationships(archive)
    sheets = []
    for sheet in workbook.findall(".//x:sheet", namespace):
        name = str(sheet.attrib.get("name") or "Sheet")
        rel_id = str(sheet.attrib.get(f"{{{namespace['r']}}}id") or "")
        target = rels.get(rel_id)
        if target:
            sheets.append((name, "xl/" + target.lstrip("/")))
    return sheets


def _xlsx_relationships(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        rels_xml = archive.read("xl/_rels/workbook.xml.rels")
    except KeyError:
        return {}
    root = ElementTree.fromstring(rels_xml)
    result = {}
    for rel in root:
        rel_id = str(rel.attrib.get("Id") or "")
        target = str(rel.attrib.get("Target") or "")
        if rel_id and target:
            result[rel_id] = target if target.startswith("worksheets/") else target
    return result


def _xlsx_sheet_rows(xml_bytes: bytes, sheet_name: str, shared_strings: list[str]) -> list[_ParsedEvidence]:
    root = ElementTree.fromstring(xml_bytes)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    evidence = []
    for row in root.findall(".//x:row", namespace):
        row_number = int(row.attrib.get("r") or len(evidence) + 1)
        values = []
        for cell in row.findall("x:c", namespace):
            values.append(_xlsx_cell_value(cell, namespace, shared_strings))
        clean_values = [value for value in values if value]
        if clean_values:
            text = f"{sheet_name} R{row_number}: " + " | ".join(clean_values)
            evidence.append(_ParsedEvidence(text, {"type": "xlsx_row", "sheet": sheet_name, "row": row_number}))
    return evidence


def _xlsx_cell_value(cell: ElementTree.Element, namespace: dict[str, str], shared_strings: list[str]) -> str:
    cell_type = str(cell.attrib.get("t") or "")
    if cell_type == "inlineStr":
        return " ".join("".join(node.text or "" for node in cell.findall(".//x:t", namespace)).split())
    value_node = cell.find("x:v", namespace)
    raw = "" if value_node is None or value_node.text is None else value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    return raw.strip()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_record(
    seed: str,
    path: Path,
    suffix: str,
    status: str,
    message: str,
    evidence: list[_ParsedEvidence],
    *,
    size: int = 0,
    content_hash: str = "",
) -> dict[str, Any]:
    source_id = "src_" + hashlib.sha256(f"{path.resolve() if path.exists() else path}|{seed}".encode("utf-8")).hexdigest()[:16]
    return {
        "source_id": source_id,
        "path": str(path),
        "suffix": suffix,
        "status": status,
        "message": message,
        "size": size,
        "content_hash": content_hash,
        "_evidence": evidence,
    }


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(source.get("source_id") or ""),
        "path": str(source.get("path") or ""),
        "suffix": str(source.get("suffix") or ""),
        "status": str(source.get("status") or ""),
        "message": str(source.get("message") or ""),
        "evidence_count": int(source.get("evidence_count") or 0),
        "size": int(source.get("size") or 0),
        "content_hash": str(source.get("content_hash") or ""),
    }


def _collection_id(source_ids: list[str]) -> str:
    seed = "\n".join(source_ids) if source_ids else "empty"
    return "src_col_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _query_terms(query: str) -> list[str]:
    return [item.lower() for item in re.findall(r"[\w\u4e00-\u9fff]+", str(query or "")) if item.strip()]


def _score_text(text: str, terms: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def _search_result(evidence: dict[str, Any], terms: list[str], score: int) -> dict[str, Any]:
    text = str(evidence.get("text") or "")
    return {
        "evidence_id": str(evidence.get("evidence_id") or ""),
        "source_id": str(evidence.get("source_id") or ""),
        "path": str(evidence.get("path") or ""),
        "suffix": str(evidence.get("suffix") or ""),
        "locator": evidence.get("locator") if isinstance(evidence.get("locator"), dict) else {},
        "excerpt": _excerpt(text, terms),
        "score": score,
    }


def _clean_queries(*, query: str = "", queries: list[str] | None = None) -> list[str]:
    result = []
    for item in [query, *(queries or [])]:
        clean = _single_line(str(item or ""))
        if clean and clean not in result:
            result.append(clean)
    return result[:24]


def _locator_label(locator: dict[str, Any]) -> str:
    locator_type = str(locator.get("type") or "")
    if locator_type == "line":
        start = int(locator.get("start_line") or 0)
        end = int(locator.get("end_line") or start)
        return f"lines {start}-{end}" if end and end != start else f"line {start}"
    if locator_type == "docx_paragraph":
        return f"DOCX paragraph {locator.get('paragraph')}"
    if locator_type == "xlsx_row":
        return f"XLSX {locator.get('sheet') or 'Sheet'} R{locator.get('row')}"
    if locator_type == "pdf_page":
        return f"PDF p.{locator.get('page')}"
    if locator_type == "pdf_text_object":
        return "PDF text object"
    if locator_type == "text":
        return "text"
    return locator_type or "source"


def _excerpt(text: str, terms: list[str], limit: int = 360) -> str:
    lowered = text.lower()
    first = min((lowered.find(term) for term in terms if lowered.find(term) >= 0), default=0)
    start = max(0, first - 80)
    excerpt = text[start : start + limit]
    return excerpt.strip()


def _ingest_message(sources: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
    parsed = sum(1 for source in sources if source.get("status") == "parsed")
    blocked = len(sources) - parsed
    return f"已登记 {len(sources)} 个来源，成功解析 {parsed} 个，生成 {len(evidence)} 条 Source Evidence；{blocked} 个来源未解析或部分降级。"


def _clean_path(value: Any) -> str:
    text = str(value or "").strip()
    text = text.strip(" \t\r\n`\"'“”")
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    return text


def _safe_id(value: str) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_.-]+", text) else ""


def _single_line(value: str) -> str:
    return " ".join(str(value or "").split())[:120]
