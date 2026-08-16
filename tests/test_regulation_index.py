from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent import regulation_index
from regulation_agent.regulation_index import RegulationIndexStore
from regulation_agent.service import ApplicationService


class RegulationIndexTests(unittest.TestCase):
    def test_source_article_directory_is_staged_as_one_unit_with_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            corpus = root / "evidence"
            article = corpus / "catarc-zqyj" / "notices" / "631" / "standards" / "01_20256138-T-339"
            files_dir = article / "files"
            files_dir.mkdir(parents=True)
            entry_json = article / "entry.json"
            draft_pdf = files_dir / "draft_pdf_货运挂车系列型谱.pdf"
            feedback_doc = files_dir / "feedback_form_征求意见反馈单.doc"
            cleanup_dir = corpus / "_cleanup-manifests"
            cleanup_dir.mkdir(parents=True)
            entry_json.write_text(
                json.dumps(
                    {
                        "notice_title": "公开征求《货运挂车系列型谱》推荐性国家标准的意见",
                        "plan_code": "20256138-T-339",
                        "standard_name": "货运挂车系列型谱",
                        "attachments": [
                            {"filename": draft_pdf.name, "local_path": str(draft_pdf), "kind": "draft_pdf"},
                            {"filename": feedback_doc.name, "local_path": str(feedback_doc), "kind": "feedback_form"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            draft_pdf.write_bytes(b"%PDF-1.7\n%%EOF\n")
            feedback_doc.write_bytes(b"legacy doc")
            (cleanup_dir / "cleanup.json").write_text('{"kind":"cleanup"}', encoding="utf-8")

            store = RegulationIndexStore(root / "state")
            staged = store.stage_sources([str(corpus)], max_sources=10)

            self.assertTrue(staged["ok"])
            self.assertEqual(staged["staged_count"], 1)
            self.assertEqual(staged["unsupported_count"], 0)
            self.assertEqual(len(staged["next_sources"]), 1)
            source = staged["next_sources"][0]
            self.assertEqual(source["source_kind"], "source_article")
            self.assertEqual(source["main_path"], str(entry_json))
            self.assertEqual({item["file_name"] for item in source["attachments"]}, {draft_pdf.name, feedback_doc.name})
            self.assertEqual(len(source["attachments"]), 2)

    def test_same_stem_multi_format_files_are_staged_as_one_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            corpus = root / "evidence"
            docs = corpus / "unece" / "working-documents"
            docs.mkdir(parents=True)
            docx_path = docs / "ECE-TRANS-WP29-GRVA-2026-12e.docx"
            pdf_path = docs / "ECE-TRANS-WP29-GRVA-2026-12e.pdf"
            docx_path.write_bytes(b"fake docx")
            pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
            store = RegulationIndexStore(root / "state")

            staged = store.stage_sources([str(corpus)], max_sources=10)

            self.assertEqual(staged["staged_count"], 1)
            source = staged["next_sources"][0]
            self.assertEqual(source["main_path"], str(docx_path))
            self.assertEqual({item["file_name"] for item in source["attachments"]}, {pdf_path.name})

    def test_collected_body_markdown_claims_direct_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            corpus = root / "evidence"
            section = corpus / "unece" / "02-Working-documents"
            section.mkdir(parents=True)
            body_path = section / "正文.md"
            body_html = section / "正文.html"
            docx_path = section / "ECE-TRANS-WP29-GRVA-2026-12e.docx"
            pdf_path = section / "ECE-TRANS-WP29-GRVA-2026-12e.pdf"
            body_path.write_text("ECE/TRANS/WP.29/GRVA/2026/12 - Proposal for amendments to UN Regulation No. 157", encoding="utf-8")
            body_html.write_text("<p>same page</p>", encoding="utf-8")
            docx_path.write_bytes(b"fake docx")
            pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
            store = RegulationIndexStore(root / "state")

            staged = store.stage_sources([str(corpus)], max_sources=10)

            self.assertEqual(staged["staged_count"], 1)
            source = staged["next_sources"][0]
            self.assertEqual(source["main_path"], str(body_path))
            self.assertEqual({item["file_name"] for item in source["attachments"]}, {body_html.name, docx_path.name, pdf_path.name})

    def test_record_and_export_uses_regulation_identity_with_status_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            corpus = root / "evidence"
            corpus.mkdir()
            first = corpus / "立项公告.md"
            second = corpus / "征求意见.html"
            first.write_text("GB 12345 汽车示例标准 立项，日期 2025-01-01。", encoding="utf-8")
            second.write_text("<p>GB-12345 汽车示例标准 征求意见，发布日期 2026-07-09，截止 2026-08-28。</p>", encoding="utf-8")

            store = RegulationIndexStore(root / "state")
            staged = store.stage_sources([str(corpus)], max_sources=10)
            first_source = staged["next_sources"][0]
            second_source = staged["next_sources"][1]
            first_record = store.record_entries(
                source_id=first_source["source_id"],
                source_status="processed",
                entries=[
                    {
                        "regulation_number": "GB 12345",
                        "regulation_name": "汽车示例标准",
                        "regulation_status": "立项",
                        "event_date": "2025-01-01",
                        "confidence": "high",
                    }
                ],
            )

            self.assertTrue(first_record["ok"])
            self.assertEqual(first_record["created_count"], 1)
            self.assertEqual(first_record["source_status"], "processed")
            self.assertTrue((root / "state" / "regulation_index" / "processed" / f"{first_source['source_id']}.json").exists())
            self.assertFalse((root / "state" / "regulation_index" / "unprocessed" / f"{first_source['source_id']}.json").exists())

            updated_record = store.record_entries(
                source_id=second_source["source_id"],
                source_status="processed",
                entries=[
                    {
                        "regulation_number": "GB-12345",
                        "regulation_name": "汽车示例标准",
                        "regulation_status": "征求意见",
                        "event_date": "2026-07-09",
                        "comment_deadline": "2026-08-28",
                        "attachments": [{"file_name": "征求意见稿.pdf", "relationship": "draft"}],
                        "confidence": "medium",
                    }
                ],
            )

            self.assertTrue(updated_record["ok"])
            self.assertEqual(updated_record["created_count"], 0)
            self.assertEqual(updated_record["updated_count"], 1)
            self.assertEqual(updated_record["duplicate_count"], 1)

            exported = store.export_index(output_dir=root / "exports", formats=["json", "csv"], overwrite=True)

            self.assertTrue(exported["ok"])
            paths = {artifact["format"]: Path(artifact["path"]) for artifact in exported["artifacts"]}
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(len(data["regulations"]), 1)
            regulation = data["regulations"][0]
            self.assertEqual(regulation["regulation_number"], "GB 12345")
            self.assertEqual(regulation["regulation_status"], "征求意见")
            self.assertEqual(regulation["status_date"], "2026-07-09")
            self.assertEqual(regulation["dates"]["comment_deadline"], "2026-08-28")
            self.assertEqual([item["status"] for item in regulation["status_history"]], ["立项", "征求意见"])
            self.assertEqual(len(regulation["source_article_ids"]), 2)
            self.assertIn("GB 12345", paths["csv"].read_text(encoding="utf-8-sig"))

    def test_entry_level_attachments_do_not_inherit_unrelated_source_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            article = root / "list"
            article.mkdir()
            entry_json = article / "entry.json"
            first = article / "first.pdf"
            second = article / "second.pdf"
            first.write_bytes(b"%PDF-1.7\n%%EOF\n")
            second.write_bytes(b"%PDF-1.7\n%%EOF\n")
            entry_json.write_text(
                json.dumps(
                    {
                        "notice_title": "附件列表",
                        "attachments": [
                            {"filename": first.name, "local_path": str(first), "kind": "draft_pdf"},
                            {"filename": second.name, "local_path": str(second), "kind": "draft_pdf"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            store = RegulationIndexStore(root / "state")
            staged = store.stage_sources([str(entry_json)], max_sources=10)

            store.record_entries(
                source_id=staged["next_sources"][0]["source_id"],
                source_status="processed",
                entries=[
                    {
                        "regulation_number": "UN Regulation No. 157",
                        "regulation_name": "ALKS",
                        "regulation_status": "报批稿",
                        "attachments": [{"file_name": first.name, "path_or_url": str(first), "relationship": "proposal"}],
                    }
                ],
            )
            exported = store.export_index(output_dir=root / "exports", formats=["json"], overwrite=True)
            data = json.loads(Path(exported["artifacts"][0]["path"]).read_text(encoding="utf-8"))

            self.assertEqual([item["file_name"] for item in data["regulations"][0]["attachments"]], [first.name])

    def test_weak_name_identity_merges_into_numbered_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            first = root / "名称公告.md"
            second = root / "编号公告.md"
            first.write_text("《汽车示例标准》已经立项。", encoding="utf-8")
            second.write_text("GB 12345《汽车示例标准》已经发布。", encoding="utf-8")
            store = RegulationIndexStore(root / "state")
            staged = store.stage_sources([str(first), str(second)], max_sources=10)

            store.record_entries(
                source_id=staged["next_sources"][0]["source_id"],
                source_status="processed",
                entries=[{"regulation_name": "汽车示例标准", "regulation_status": "立项", "event_date": "2025-01-01"}],
            )
            store.record_entries(
                source_id=staged["next_sources"][1]["source_id"],
                source_status="processed",
                entries=[{"regulation_number": "GB 12345", "regulation_name": "汽车示例标准", "regulation_status": "发布", "event_date": "2026-12-01"}],
            )
            data = json.loads(Path(store.export_index(output_dir=root / "exports", formats=["json"], overwrite=True)["artifacts"][0]["path"]).read_text(encoding="utf-8"))

            self.assertEqual(len(data["regulations"]), 1)
            self.assertEqual(data["regulations"][0]["regulation_number"], "GB 12345")
            self.assertFalse(data["regulations"][0]["weak_identity"])
            self.assertEqual(data["regulations"][0]["regulation_status"], "发布")

    def test_processed_with_no_entries_does_not_remain_in_unprocessed_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "普通新闻.md"
            source.write_text("这是一条不含法规编号和法规名称的普通新闻。", encoding="utf-8")
            store = RegulationIndexStore(root / "state")
            staged = store.stage_sources([str(source)], max_sources=10)
            source_id = staged["next_sources"][0]["source_id"]

            result = store.record_entries(source_id=source_id, source_status="processed_with_no_entries", entries=[], message="未发现法规身份。")

            self.assertTrue(result["ok"])
            self.assertEqual(result["source_status"], "processed_with_no_entries")
            restaged = store.stage_sources([str(source)], max_sources=10)
            self.assertEqual(restaged["already_processed_count"], 1)
            self.assertEqual(restaged["next_sources"], [])

    def test_stage_sources_safety_limit_allows_large_explicit_local_corpora(self) -> None:
        self.assertGreaterEqual(regulation_index.MAX_STAGE_SOURCES, 10000)

    def test_service_exposes_regulation_index_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "公告.md"
            source.write_text("GB 54321 发布公告。", encoding="utf-8")
            service = ApplicationService(state_dir=root / "state")

            tool_names = {tool["function"]["name"] for tool in service._regpilot_model_tools()}
            self.assertIn("regpilot_stage_regulation_sources", tool_names)
            self.assertIn("regpilot_record_regulation_entries", tool_names)
            self.assertIn("regpilot_export_regulation_index", tool_names)

            staged = service._handle_regpilot_management_tool_call(
                "regpilot_stage_regulation_sources",
                {"source_paths": [str(source)], "max_sources": 5},
            )
            self.assertTrue(staged["ok"])
            source_id = staged["next_sources"][0]["source_id"]
            ingest = service._handle_regpilot_management_tool_call(
                "regpilot_ingest_sources",
                {"source_paths": [str(source)], "collection_name": "公告"},
            )
            search = service._handle_regpilot_management_tool_call(
                "regpilot_search_sources",
                {"collection_id": ingest["collection_id"], "query": "GB 54321", "top_k": 1},
            )
            evidence_id = search["results"][0]["evidence_id"]

            recorded = service._handle_regpilot_management_tool_call(
                "regpilot_record_regulation_entries",
                {
                    "source_id": source_id,
                    "source_status": "processed",
                    "collection_id": ingest["collection_id"],
                    "entries": [
                        {
                            "regulation_number": "GB 54321",
                            "regulation_name": "示例发布标准",
                            "regulation_status": "发布",
                            "event_date": "2026-07-09",
                            "source_evidence_ids": [evidence_id],
                        }
                    ],
                },
            )
            self.assertTrue(recorded["ok"])

            invalid_evidence = service._handle_regpilot_management_tool_call(
                "regpilot_record_regulation_entries",
                {
                    "source_id": source_id,
                    "source_status": "processed",
                    "collection_id": ingest["collection_id"],
                    "entries": [
                        {
                            "regulation_number": "GB 54321",
                            "regulation_name": "示例发布标准",
                            "regulation_status": "发布",
                            "source_evidence_ids": ["ev_fake"],
                        }
                    ],
                },
            )
            self.assertFalse(invalid_evidence["ok"])
            self.assertEqual(invalid_evidence["code"], "tool_call_invalid")

            exported = service._handle_regpilot_management_tool_call(
                "regpilot_export_regulation_index",
                {"output_dir": str(root / "exports"), "formats": ["json"]},
            )
            self.assertTrue(exported["ok"])
            self.assertEqual(exported["artifacts"][0]["format"], "json")
            exported_data = json.loads(Path(exported["artifacts"][0]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(exported_data["regulations"]), 1)
