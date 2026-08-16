from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.service import ApplicationService
from regulation_agent.source_documents import DocumentSourceStore


class SourceDocumentTests(unittest.TestCase):
    def test_ingest_reads_common_local_documents_and_supports_search_and_slice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            md_path = root / "法规说明.md"
            txt_path = root / "术语.txt"
            docx_path = root / "解读.docx"
            xlsx_path = root / "模块.xlsx"
            html_path = root / "公告.html"
            json_path = root / "公告.json"
            csv_path = root / "公告.csv"
            md_path.write_text("# 标准说明\n制动系统应满足 GB 21670。", encoding="utf-8")
            txt_path.write_text("术语：整车整备质量。", encoding="utf-8")
            _write_minimal_docx(docx_path, ["法规条款：车辆应配置行人保护。"])
            _write_minimal_xlsx(xlsx_path, "模块清单", [["法规项", "标准要求"], ["制动系统", "GB 21670"]])
            html_path.write_text("<html><body><h1>公告</h1><p>征求 GB 9999 意见。</p></body></html>", encoding="utf-8")
            json_path.write_text('{"title":"公告","content":"发布 GB 8888 标准。"}', encoding="utf-8")
            csv_path.write_text("法规,状态\nGB 7777,立项\n", encoding="utf-8")

            store = DocumentSourceStore(root / "state")
            ingest = store.ingest_source_paths(
                [str(md_path), str(txt_path), str(docx_path), str(xlsx_path), str(html_path), str(json_path), str(csv_path)]
            )

            self.assertTrue(ingest["ok"])
            self.assertEqual(ingest["source_count"], 7)
            self.assertGreaterEqual(ingest["evidence_count"], 8)
            self.assertEqual({source["status"] for source in ingest["sources"]}, {"parsed"})

            search = store.search_sources(collection_id=ingest["collection_id"], query="制动 GB 21670", top_k=3)

            self.assertTrue(search["ok"])
            self.assertGreaterEqual(len(search["results"]), 1)
            self.assertIn("制动", search["results"][0]["excerpt"])
            self.assertIn("source_id", search["results"][0])
            self.assertIn("locator", search["results"][0])

            loaded = store.load_source_slice(
                collection_id=ingest["collection_id"],
                evidence_id=search["results"][0]["evidence_id"],
            )

            self.assertTrue(loaded["ok"])
            self.assertIn(search["results"][0]["source_id"], loaded["source_id"])
            self.assertIn("text", loaded)
            self.assertLessEqual(len(loaded["text"]), 4000)

    def test_pdf_without_extractable_text_is_recorded_without_invented_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            pdf_path = root / "扫描件.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%%EOF\n")

            store = DocumentSourceStore(root / "state")
            ingest = store.ingest_source_paths([str(pdf_path)])

            self.assertTrue(ingest["ok"])
            self.assertEqual(ingest["source_count"], 1)
            self.assertEqual(ingest["evidence_count"], 0)
            self.assertIn(ingest["sources"][0]["status"], {"no_text", "parse_failed"})
            self.assertEqual(store.search_sources(collection_id=ingest["collection_id"], query="任意")["results"], [])

    def test_service_exposes_source_tools_without_requiring_skill_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "法规.md"
            source.write_text("车辆制动要求来自 GB 21670。", encoding="utf-8")
            service = ApplicationService(state_dir=root / "state")

            tool_names = {tool["function"]["name"] for tool in service._regpilot_model_tools()}
            self.assertIn("regpilot_ingest_sources", tool_names)
            self.assertIn("regpilot_search_sources", tool_names)
            self.assertIn("regpilot_build_evidence_bundle", tool_names)
            self.assertIn("regpilot_load_source_slice", tool_names)

            ingest = service._handle_regpilot_management_tool_call(
                "regpilot_ingest_sources",
                {"source_paths": [str(source)]},
            )
            self.assertTrue(ingest["ok"])

            search = service._handle_regpilot_management_tool_call(
                "regpilot_search_sources",
                {"collection_id": ingest["collection_id"], "query": "制动", "top_k": 2},
            )
            self.assertTrue(search["ok"])
            self.assertEqual(len(search["results"]), 1)

            loaded = service._handle_regpilot_management_tool_call(
                "regpilot_load_source_slice",
                {"collection_id": ingest["collection_id"], "evidence_id": search["results"][0]["evidence_id"]},
            )
            self.assertTrue(loaded["ok"])
            self.assertIn("GB 21670", loaded["text"])

    def test_service_builds_bounded_evidence_bundle_with_locators(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "UN-R127.md"
            source.write_text(
                "\n".join(
                    [
                        "Scope: UN R127 applies to M1 and N1 vehicles for pedestrian protection.",
                        "Definitions: bonnet top and bumper reference lines define the test areas.",
                        "Approval: type approval requires evidence from headform and legform tests.",
                        "Transition: certificates must be checked against the applicable amendment.",
                    ]
                ),
                encoding="utf-8",
            )
            service = ApplicationService(state_dir=root / "state")
            ingest = service._handle_regpilot_management_tool_call(
                "regpilot_ingest_sources",
                {"source_paths": [str(source)], "collection_name": "UN R127"},
            )

            bundle = service._handle_regpilot_management_tool_call(
                "regpilot_build_evidence_bundle",
                {
                    "collection_id": ingest["collection_id"],
                    "queries": ["scope pedestrian", "approval headform"],
                    "top_k": 2,
                    "total_char_limit": 800,
                },
            )

            self.assertTrue(bundle["ok"])
            self.assertEqual(bundle["collection_id"], ingest["collection_id"])
            self.assertGreaterEqual(len(bundle["evidence_ids"]), 2)
            self.assertLessEqual(bundle["total_chars"], 800)
            self.assertIn("Source Evidence Bundle", bundle["bundle_text"])
            self.assertIn("Evidence:", bundle["bundle_text"])
            self.assertIn("line", bundle["bundle_text"])
            self.assertEqual([item["query"] for item in bundle["coverage"]], ["scope pedestrian", "approval headform"])

    def test_service_generates_markdown_and_docx_interpretation_artifacts_from_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "法规.md"
            source.write_text("UN R127 行人保护要求：车辆前部结构应降低行人伤害风险。", encoding="utf-8")
            service = ApplicationService(state_dir=root / "state")
            ingest = service._handle_regpilot_management_tool_call(
                "regpilot_ingest_sources",
                {"source_paths": [str(source)], "collection_name": "UN R127"},
            )
            search = service._handle_regpilot_management_tool_call(
                "regpilot_search_sources",
                {"collection_id": ingest["collection_id"], "query": "行人保护", "top_k": 1},
            )
            evidence_id = search["results"][0]["evidence_id"]

            result = service._handle_regpilot_management_tool_call(
                "regpilot_generate_interpretation_report",
                {
                    "collection_id": ingest["collection_id"],
                    "title": "UN R127 法规解读",
                    "markdown": (
                        "# UN R127 法规解读\n\n"
                        "## 一页速览\n\n"
                        f"行人保护要求需要工程团队关注车辆前部结构。[Evidence: {evidence_id}]\n\n"
                        "| 条款 | 要求 | 证据 |\n"
                        "| --- | --- | --- |\n"
                        f"| 车辆前部结构 | 降低行人伤害风险 | {evidence_id} |\n"
                    ),
                    "source_evidence_ids": [evidence_id],
                    "formats": ["md", "docx"],
                    "output_dir": str(root / "outputs"),
                },
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["collection_id"], ingest["collection_id"])
            paths = {artifact["format"]: Path(artifact["path"]) for artifact in result["artifacts"]}
            self.assertEqual(set(paths), {"md", "docx"})
            self.assertIn("行人保护", paths["md"].read_text(encoding="utf-8"))
            self.assertTrue(paths["docx"].exists())
            with zipfile.ZipFile(paths["docx"]) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("UN R127 法规解读", document_xml)
            self.assertIn("行人保护", document_xml)
            self.assertIn("<w:tbl", document_xml)
            self.assertNotIn("| 条款 | 要求 | 证据 |", document_xml)
            with zipfile.ZipFile(paths["docx"]) as archive:
                self.assertIn("word/footer1.xml", archive.namelist())
            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertGreaterEqual(manifest["quality"]["locator_reference_count"], 1)
            self.assertGreaterEqual(manifest["quality"]["docx"]["table_count"], 1)

            duplicate = service._handle_regpilot_management_tool_call(
                "regpilot_generate_interpretation_report",
                {
                    "collection_id": ingest["collection_id"],
                    "title": "UN R127 法规解读",
                    "markdown": f"重复生成。[Evidence: {evidence_id}]",
                    "source_evidence_ids": [evidence_id],
                    "formats": ["md"],
                    "output_dir": str(root / "outputs"),
                },
            )
            self.assertFalse(duplicate["ok"])
            self.assertEqual(duplicate["code"], "tool_call_invalid")

    def test_report_generation_requires_source_evidence_reference_in_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "法规.md"
            source.write_text("UN R127 行人保护要求：车辆前部结构应降低行人伤害风险。", encoding="utf-8")
            service = ApplicationService(state_dir=root / "state")
            ingest = service._handle_regpilot_management_tool_call(
                "regpilot_ingest_sources",
                {"source_paths": [str(source)], "collection_name": "UN R127"},
            )
            search = service._handle_regpilot_management_tool_call(
                "regpilot_search_sources",
                {"collection_id": ingest["collection_id"], "query": "行人保护", "top_k": 1},
            )
            evidence_id = search["results"][0]["evidence_id"]

            missing_reference = service._handle_regpilot_management_tool_call(
                "regpilot_generate_interpretation_report",
                {
                    "collection_id": ingest["collection_id"],
                    "title": "无引用报告",
                    "markdown": "# 无引用报告\n\n行人保护要求需要工程团队关注车辆前部结构。\n",
                    "source_evidence_ids": [evidence_id],
                    "formats": ["md"],
                    "output_dir": str(root / "outputs"),
                },
            )

            self.assertFalse(missing_reference["ok"])
            self.assertEqual(missing_reference["code"], "tool_call_invalid")
            self.assertIn("Source Evidence", missing_reference["message"])

    def test_service_exposes_interpretation_report_generation_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            service = ApplicationService(state_dir=Path(temp_name))

            tool_names = {tool["function"]["name"] for tool in service._regpilot_model_tools()}
            inventory = {tool["name"]: tool for tool in service.bootstrap()["tools"]}

            self.assertIn("regpilot_generate_interpretation_report", tool_names)
            self.assertEqual(inventory["artifact.generate_regulatory_report"]["status"], "available")


def _write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr("word/document.xml", document_xml)


def _write_minimal_xlsx(path: Path, sheet_name: str, rows: list[list[str]]) -> None:
    strings: list[str] = []
    string_index: dict[str, int] = {}

    def index(value: str) -> int:
        if value not in string_index:
            string_index[value] = len(strings)
            strings.append(value)
        return string_index[value]

    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for col_number, value in enumerate(row, start=1):
            cell_ref = f"{chr(64 + col_number)}{row_number}"
            cells.append(f'<c r="{cell_ref}" t="s"><v>{index(value)}</v></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    shared_strings = "".join(f"<si><t>{value}</t></si>" for value in strings)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "")
        archive.writestr(
            "xl/workbook.xml",
            (
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
            ),
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{"".join([shared_strings])}</sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
            ),
        )


if __name__ == "__main__":
    unittest.main()
