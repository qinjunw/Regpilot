from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regulation_agent.agent_skills import (
    build_skill_command,
    create_skill_draft,
    enable_skill,
    ingest_sources_placeholder,
    inspect_skill_source,
    install_skill,
    load_agent_skill,
    load_available_agent_skills,
    load_builtin_agent_skills,
    operator_skill_list,
    rename_skill,
    select_skill_candidates,
    validate_skill_source,
    validate_agent_turn_decision,
)


class AgentSkillCatalogTests(unittest.TestCase):
    def test_builtin_catalog_loads_shanghai_fill_skill_from_standard_skill_file(self) -> None:
        skills = load_builtin_agent_skills()
        skill = _skill_by_id(skills, "formfill.shanghai_data")

        self.assertEqual(skill["title"], "上海数据平台填报")
        self.assertEqual(skill["task_id"], "shanghaiData_fill")
        self.assertEqual(skill["run_policy_default"], "until_before_final_submit")
        self.assertTrue(skill["submission_safety_boundary"])
        self.assertTrue(str(skill["skill_path"]).endswith("shanghai_data_fill\\SKILL.md") or str(skill["skill_path"]).endswith("shanghai_data_fill/SKILL.md"))
        self.assertIn("reference/workflow.md", skill["reference_paths"])
        self.assertIn("manual_correction_review", skill["reference_text"])
        self.assertIn("formfill_run_until_stop", skill["allowed_tools"])
        node_ids = {node["id"] for node in skill["operation_nodes"]}
        self.assertEqual(
            {"prepare_fill_run", "fill_current_page", "advance_after_current_page", "manual_correction_review"},
            node_ids,
        )

    def test_builtin_catalog_loads_guarded_fill_skill_family(self) -> None:
        skills = load_builtin_agent_skills()
        fill_skills = {skill["id"]: skill for skill in skills if skill.get("category") == "fill"}

        self.assertEqual(
            {skill_id: skill["task_id"] for skill_id, skill in fill_skills.items()},
            {
                "formfill.landmark": "landmark_fill",
                "formfill.ota": "ota_fill",
                "formfill.shanghai_data": "shanghaiData_fill",
            },
        )
        landmark = fill_skills["formfill.landmark"]
        ota = fill_skills["formfill.ota"]
        self.assertEqual(landmark["default_inputs"]["sheet"], "SHGL备案参数")
        self.assertEqual(ota["default_inputs"]["sheet"], "REEV车型及功能备案细分")
        self.assertIn("attachment_folder", ota["required_inputs"])
        self.assertIn("handle_page_attachments", {node["id"] for node in ota["operation_nodes"]})
        self.assertIn("隐藏 OTA 标签页不视为当前步骤", ota["reference_text"])
        self.assertIn("formfill_resume_after_manual_fix", landmark["allowed_tools"])
        self.assertIn("formfill_resume_after_manual_fix", ota["allowed_tools"])

    def test_builtin_catalog_ignores_unenabled_skill_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_action_skill(root / "builtin" / "shanghai_data_fill")
            _write_ai_workflow_skill(root / "builtin" / "reg_read", enabled=False)

            skills = load_builtin_agent_skills(root)

        self.assertNotIn("automotive-regulation-interpretation", {skill["id"] for skill in skills})
        self.assertNotIn("Regulation Interpretation", {skill["title"] for skill in skills})

    def test_builtin_catalog_can_load_from_release_skill_root_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_action_skill(root / "builtin" / "shanghai_data_fill")

            with patch.dict(os.environ, {"REGULATION_AGENT_SKILLS_ROOT": str(root)}, clear=False):
                skills = load_builtin_agent_skills()

        self.assertEqual([skill["id"] for skill in skills], ["formfill.shanghai_data"])

    def test_builtin_catalog_loads_regpilot_skill_creator_workflow(self) -> None:
        skills = load_builtin_agent_skills()
        skill = _skill_by_id(skills, "regpilot.skill_creator")

        self.assertEqual(skill["title"], "RegPilot Skill Creator")
        self.assertEqual(skill["skill_type"], "ai_workflow")
        self.assertEqual(skill["location"], "builtin")
        self.assertIn("workflow", skill["category"])
        self.assertIn("reference/local-package-install.md", skill["reference_paths"])

    def test_builtin_regulation_interpretation_skill_points_to_existing_reference_path(self) -> None:
        skills = load_builtin_agent_skills()
        skill = _skill_by_id(skills, "automotive-regulation-interpretation")
        skill_text = Path(skill["skill_path"]).read_text(encoding="utf-8")

        self.assertIn("reference/report-workflow.md", skill["reference_paths"])
        self.assertNotIn("references/report-workflow.md", skill["reference_paths"])
        self.assertIn("Read `reference/report-workflow.md`", skill_text)
        self.assertNotIn("Read `references/report-workflow.md`", skill_text)

    def test_builtin_regulation_source_index_skill_is_available(self) -> None:
        skills = load_builtin_agent_skills()
        skill = _skill_by_id(skills, "automotive-regulation-source-index")
        skill_text = Path(skill["skill_path"]).read_text(encoding="utf-8")

        self.assertEqual(skill["title"], "Automotive Regulation Source Index")
        self.assertEqual(skill["skill_type"], "ai_workflow")
        self.assertEqual(skill["location"], "builtin")
        self.assertIn("reference/index-workflow.md", skill["reference_paths"])
        self.assertIn("regpilot_stage_regulation_sources", skill_text)

    def test_regulation_interpretation_skill_requires_report_coverage_checklist(self) -> None:
        skill_roots = [
            Path(__file__).resolve().parents[1] / "skills" / "reg_read",
            Path(__file__).resolve().parents[1] / "skills" / "installed" / "reg_read",
        ]

        for skill_root in skill_roots:
            with self.subTest(skill_root=skill_root):
                skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
                reference_text = (skill_root / "reference" / "report-workflow.md").read_text(encoding="utf-8")

                self.assertIn("报告覆盖清单", skill_text)
                self.assertIn("资料中未提及", skill_text)
                self.assertIn("报告覆盖清单", reference_text)
                self.assertIn("not mentioned in source", reference_text)
                self.assertIn("not supported by inputs", reference_text)

    def test_operator_skill_list_hides_internal_nodes_and_tools(self) -> None:
        skills = load_builtin_agent_skills()
        items = operator_skill_list(skills)
        item = _skill_by_id(items, "formfill.shanghai_data")

        self.assertEqual(item["title"], "上海数据平台填报")
        self.assertEqual(item["status"], "available")
        self.assertEqual(item["category"], "fill")
        self.assertNotIn("operation_nodes", item)
        self.assertNotIn("allowed_tools", item)
        self.assertNotIn("required_inputs", item)
        self.assertNotIn("task_id", item)

    def test_rename_skill_sets_custom_display_name_without_editing_skill_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            skill_dir = root / "builtin" / "shanghai_data_fill"
            _write_action_skill(skill_dir)
            original_skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

            renamed = rename_skill(root=root, skill_id="formfill.shanghai_data", display_name="我的上海填报")

            self.assertTrue(renamed["ok"])
            self.assertEqual(renamed["skill_id"], "formfill.shanghai_data")
            self.assertEqual(renamed["title"], "上海数据平台填报")
            self.assertEqual(renamed["display_name"], "我的上海填报")
            self.assertEqual((skill_dir / "SKILL.md").read_text(encoding="utf-8"), original_skill_text)
            state = json.loads((skill_dir / ".regpilot.json").read_text(encoding="utf-8"))
            self.assertEqual(state["display_name"], "我的上海填报")
            self.assertEqual(state["title"], "上海数据平台填报")

            skills = load_available_agent_skills(root)
            skill = _skill_by_id(skills, "formfill.shanghai_data")
            self.assertEqual(skill["title"], "上海数据平台填报")
            self.assertEqual(skill["display_name"], "我的上海填报")
            self.assertEqual(_skill_by_id(operator_skill_list(skills), "formfill.shanghai_data")["title"], "我的上海填报")
            self.assertEqual([item["id"] for item in select_skill_candidates("帮我用我的上海填报填报", skills=skills)], ["formfill.shanghai_data"])

    def test_candidate_selection_maps_operator_fill_intent_to_one_business_skill(self) -> None:
        candidates = select_skill_candidates(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

        self.assertEqual([item["id"] for item in candidates], ["formfill.shanghai_data"])
        self.assertEqual(
            [item["id"] for item in select_skill_candidates(r"帮我用<D:\case\地标.xlsx>的 E 列填地标平台")],
            ["formfill.landmark"],
        )
        self.assertEqual(
            [item["id"] for item in select_skill_candidates(r"用<D:\ota\总表.xlsx>的 E 列做 OTA平台填报")],
            ["formfill.ota"],
        )
        self.assertEqual(
            select_skill_candidates(r"用<D:\case\E0Y 上海地标 整合版本.xlsx>的 R 列跑一下"),
            [],
        )
        self.assertEqual(select_skill_candidates("帮我解读一下这条法规"), [])
        self.assertEqual(select_skill_candidates("继续"), [])

    def test_skill_command_is_high_level_and_keeps_nodes_internal(self) -> None:
        command = build_skill_command(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

        self.assertEqual(command["type"], "skill_command")
        self.assertEqual(command["skill_id"], "formfill.shanghai_data")
        self.assertEqual(command["goal"], "run_until_stop")
        self.assertEqual(command["run_policy"], "until_before_final_submit")
        self.assertEqual(command["regulatory_tool"], "formfill_run_until_stop")
        self.assertEqual(command["inputs"]["task_id"], "shanghaiData_fill")
        self.assertNotIn("node_id", command)

    def test_ota_skill_command_preserves_attachment_folder_and_defaults(self) -> None:
        candidates = select_skill_candidates(r"用<D:\ota\总表.xlsx>的 E 列做 OTA平台填报")

        command = validate_agent_turn_decision(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.ota",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_run_until_stop",
                "inputs": {
                    "workbook_path": r"D:\ota\总表.xlsx",
                    "value_column": "E",
                    "attachment_folder": r"D:\ota\attachments",
                },
            },
            candidates,
        )

        self.assertEqual(command["skill_id"], "formfill.ota")
        self.assertEqual(command["inputs"]["task_id"], "ota_fill")
        self.assertEqual(command["inputs"]["sheet"], "REEV车型及功能备案细分")
        self.assertEqual(command["inputs"]["value_column"], "E")
        self.assertEqual(command["inputs"]["attachment_folder"], r"D:\ota\attachments")
        self.assertNotIn("node_id", command)

    def test_model_turn_decision_is_validated_against_candidate_skill_manifest(self) -> None:
        candidates = select_skill_candidates(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

        command = validate_agent_turn_decision(
            {
                "decision_type": "skill_command",
                "skill_id": "formfill.shanghai_data",
                "goal": "run_until_stop",
                "run_policy": "until_before_final_submit",
                "regulatory_tool": "formfill_run_until_stop",
                "inputs": {"value_column": "E"},
            },
            candidates,
        )

        self.assertEqual(command["type"], "skill_command")
        self.assertEqual(command["skill_id"], "formfill.shanghai_data")
        self.assertEqual(command["inputs"]["task_id"], "shanghaiData_fill")
        self.assertEqual(command["inputs"]["value_column"], "E")
        self.assertEqual(command["inputs"]["sheet"], "SHGL备案参数")
        self.assertNotIn("node_id", command)

    def test_model_turn_decision_rejects_unknown_skill_or_explicit_node(self) -> None:
        candidates = select_skill_candidates(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

        with self.assertRaises(ValueError):
            validate_agent_turn_decision(
                {
                    "decision_type": "skill_command",
                    "skill_id": "formfill.unknown",
                    "goal": "run_until_stop",
                    "run_policy": "until_before_final_submit",
                    "regulatory_tool": "formfill_run_until_stop",
                    "inputs": {},
                },
                candidates,
            )

    def test_model_turn_decision_normalizes_nested_deepseek_style_command(self) -> None:
        candidates = select_skill_candidates(r"帮我用<D:\case\上海总表.xlsx>的 E 列填报上海数据平台")

        command = validate_agent_turn_decision(
            {
                "decision_type": "skill_command",
                "skill_command": {
                    "skill_id": "formfill.shanghai_data",
                    "goal": "使用表格填报上海数据平台并停在提交前",
                    "run_policy": "until_before_final_submit",
                    "regulatory_tool": None,
                    "inputs": {
                        "workbook_path": r"D:\case\上海总表.xlsx",
                        "sheet": "SHGL备案参数",
                        "column": "E",
                    },
                },
            },
            candidates,
        )

        self.assertEqual(command["goal"], "run_until_stop")
        self.assertEqual(command["regulatory_tool"], "formfill_run_until_stop")
        self.assertEqual(command["inputs"]["workbook_path"], r"D:\case\上海总表.xlsx")
        self.assertEqual(command["inputs"]["value_column"], "E")
        with self.assertRaises(ValueError):
            validate_agent_turn_decision(
                {
                    "decision_type": "skill_command",
                    "skill_id": "formfill.shanghai_data",
                    "goal": "run_until_stop",
                    "run_policy": "until_before_final_submit",
                    "regulatory_tool": "formfill_run_until_stop",
                    "node_id": "fill_current_page",
                    "inputs": {},
                },
                candidates,
            )

    def test_catalog_supports_builtin_installed_and_hides_drafts_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_action_skill(root / "builtin" / "shanghai_data_fill")
            _write_ai_workflow_skill(root / "installed" / "reg_read", enabled=True)
            _write_ai_workflow_skill(root / "drafts" / "draft_reg_read", enabled=False)

            skills = load_available_agent_skills(root)

            self.assertEqual(
                {skill["id"]: skill["skill_type"] for skill in skills},
                {
                    "formfill.shanghai_data": "action_skill",
                    "automotive-regulation-interpretation": "ai_workflow",
                },
            )
            self.assertNotIn("draft_reg_read", {Path(skill["skill_dir"]).name for skill in skills})

    def test_skill_management_flow_creates_validates_installs_enables_and_loads_ai_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"

            draft = create_skill_draft(
                root=root,
                slug="reg-read",
                name="automotive-regulation-interpretation",
                title="法规解读",
                description="Create source-grounded automotive regulation interpretation reports.",
                skill_type="ai_workflow",
            )
            self.assertEqual(draft["location"], "drafts")
            self.assertEqual(load_available_agent_skills(root), [])

            inspected = inspect_skill_source(root=root, path=draft["skill_dir"])
            self.assertEqual(inspected["skill_type"], "ai_workflow")
            self.assertFalse(inspected["enabled"])

            validation = validate_skill_source(root=root, path=draft["skill_dir"])
            self.assertTrue(validation["ok"])
            self.assertEqual(validation["skill_type"], "ai_workflow")

            installed = install_skill(root=root, path=draft["skill_dir"])
            self.assertEqual(installed["location"], "installed")
            self.assertEqual(load_available_agent_skills(root), [])

            enabled = enable_skill(root=root, skill_id="automotive-regulation-interpretation")
            self.assertTrue(enabled["enabled"])
            skills = load_available_agent_skills(root)
            self.assertEqual([skill["id"] for skill in skills], ["automotive-regulation-interpretation"])

            loaded = load_agent_skill(root=root, skill_id="automotive-regulation-interpretation")
            self.assertEqual(loaded["skill_type"], "ai_workflow")
            self.assertIn("# 法规解读", loaded["instructions"])
            self.assertNotIn("operation_nodes", loaded)

    def test_ingest_sources_reads_supported_text_and_does_not_invent_pdf_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_ai_workflow_skill(root / "installed" / "reg_read", enabled=True)
            source_text = Path(temp_name) / "R127.md"
            source_text.write_text("制动系统应满足 GB 21670。", encoding="utf-8")
            source = Path(temp_name) / "R127.pdf"
            source.write_bytes(b"%PDF-1.7 placeholder")

            result = ingest_sources_placeholder(
                root=root,
                skill_id="automotive-regulation-interpretation",
                source_paths=[str(source_text), str(source)],
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["skill_id"], "automotive-regulation-interpretation")
            self.assertEqual(result["source_count"], 2)
            self.assertEqual(result["evidence_count"], 1)
            self.assertEqual(result["sources"][0]["status"], "parsed")
            self.assertIn(result["sources"][1]["status"], {"no_text", "parse_failed"})

    def test_local_external_skill_package_install_preserves_resources_without_trusting_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            package_dir = Path(temp_name) / "incoming" / "external-review"
            _write_external_skill_package(package_dir)

            inspected = inspect_skill_source(root=root, path=package_dir)

            self.assertEqual(inspected["location"], "external")
            self.assertEqual(inspected["skill_type"], "ai_workflow")
            self.assertEqual(inspected["resources"]["references"], ["references/review-rules.md"])
            self.assertEqual(inspected["resources"]["scripts"], ["scripts/check_review.py"])
            self.assertEqual(inspected["resources"]["assets"], ["assets/template.txt"])
            self.assertEqual(inspected["resources"]["agents"], ["agents/openai.yaml"])
            self.assertIn("scripts_require_trust", inspected["validation"]["warnings"])

            installed = install_skill(root=root, path=package_dir)
            self.assertEqual(installed["location"], "installed")
            self.assertFalse(installed["enabled"])
            installed_dir = Path(installed["skill_dir"])
            self.assertTrue((installed_dir / "references" / "review-rules.md").exists())
            self.assertTrue((installed_dir / "scripts" / "check_review.py").exists())
            self.assertTrue((installed_dir / "assets" / "template.txt").exists())
            self.assertTrue((installed_dir / "agents" / "openai.yaml").exists())
            self.assertEqual(load_available_agent_skills(root), [])

            enable_skill(root=root, skill_id="external-review")
            loaded = load_agent_skill(root=root, skill_id="external-review")

            self.assertIn("# External Review", loaded["instructions"])
            self.assertEqual(loaded["resources"]["scripts"], ["scripts/check_review.py"])
            self.assertFalse(loaded["trust"]["scripts_trusted"])
            self.assertIn("review rules", loaded["references"][0]["content"])

    def test_local_external_skill_path_accepts_common_chat_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            package_dir = Path(temp_name) / "incoming" / "external-review"
            _write_external_skill_package(package_dir)
            wrapped_path = f'  "`{package_dir}`"  \n'

            installed = install_skill(root=root, path=wrapped_path)

            self.assertEqual(installed["skill_id"], "external-review")
            self.assertTrue((root / "installed" / "external-review" / "SKILL.md").exists())

    def test_skill_collection_directory_returns_install_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_ai_workflow_skill(root / "reg_read", enabled=False)
            _write_action_skill(root / "builtin" / "shanghai_data_fill")

            inspected = inspect_skill_source(root=root, path=root)
            validation = validate_skill_source(root=root, path=root)
            installed = install_skill(root=root, path=root)

            self.assertEqual(inspected["location"], "collection")
            self.assertEqual(inspected["status"], "selection_required")
            self.assertIn("skill_collection_requires_selection", inspected["validation"]["errors"])
            self.assertEqual(
                {candidate["skill_id"] for candidate in inspected["candidates"]},
                {"automotive-regulation-interpretation", "formfill.shanghai_data"},
            )
            self.assertFalse(validation["ok"])
            self.assertEqual(validation["code"], "skill_collection_requires_selection")
            self.assertFalse(installed["ok"])
            self.assertEqual(installed["code"], "skill_collection_requires_selection")

    def test_legacy_ai_workflow_is_local_source_until_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name) / "skills"
            _write_ai_workflow_skill(root / "reg_read", enabled=False)

            inspected = inspect_skill_source(root=root, path=root / "reg_read")
            blocked_enable = enable_skill(root=root, skill_id="automotive-regulation-interpretation")
            installed = install_skill(root=root, path=root / "reg_read")
            enabled = enable_skill(root=root, skill_id="automotive-regulation-interpretation")

            self.assertEqual(inspected["location"], "legacy")
            self.assertEqual(inspected["status"], "local_source")
            self.assertFalse(inspected["enabled"])
            self.assertFalse(blocked_enable["ok"])
            self.assertEqual(blocked_enable["code"], "skill_requires_install")
            self.assertEqual(installed["location"], "installed")
            self.assertTrue(enabled["enabled"])


def _skill_by_id(items: list[dict], skill_id: str) -> dict:
    for item in items:
        if item.get("id") == skill_id:
            return item
    raise AssertionError(f"Missing skill: {skill_id}")


def _write_ai_workflow_skill(skill_dir: Path, *, enabled: bool) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: automotive-regulation-interpretation",
                "description: Create source-grounded automotive regulation interpretation reports.",
                "---",
                "",
                "# 法规解读",
                "",
                "Read `reference/report-workflow.md` before drafting.",
            ]
        ),
        encoding="utf-8",
    )
    reference_dir = skill_dir / "reference"
    reference_dir.mkdir(exist_ok=True)
    (reference_dir / "report-workflow.md").write_text("manual_correction_review is unrelated here.", encoding="utf-8")
    if enabled:
        (skill_dir / ".regpilot.json").write_text(
            '{"enabled": true, "skill_type": "ai_workflow", "status": "available"}',
            encoding="utf-8",
        )


def _write_action_skill(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: shanghai-data-fill",
                "description: Guarded Shanghai Data Platform fill.",
                "regpilot_skill: true",
                "---",
                "",
                "# 上海数据平台填报",
                "",
                "```json regpilot_manifest",
                "{",
                '  "id": "formfill.shanghai_data",',
                '  "title": "上海数据平台填报",',
                '  "description": "受控填报并停在提交前。",',
                '  "category": "fill",',
                '  "status": "available",',
                '  "task_id": "shanghaiData_fill",',
                '  "triggers": ["上海数据平台", "上海数据"],',
                '  "intent_keywords": ["填报", "备案"],',
                '  "allowed_tools": ["formfill_run_until_stop"],',
                '  "allowed_run_policies": ["until_before_final_submit"],',
                '  "run_policy_default": "until_before_final_submit",',
                '  "operation_nodes": []',
                "}",
                "```",
            ]
        ),
        encoding="utf-8",
    )


def _write_external_skill_package(skill_dir: Path) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: external-review",
                "description: Use when reviewing a local code change with project-specific review rules.",
                "---",
                "",
                "# External Review",
                "",
                "Read `references/review-rules.md` when reviewing changes.",
            ]
        ),
        encoding="utf-8",
    )
    for dirname in ("references", "scripts", "assets", "agents"):
        (skill_dir / dirname).mkdir(exist_ok=True)
    (skill_dir / "references" / "review-rules.md").write_text("review rules", encoding="utf-8")
    (skill_dir / "scripts" / "check_review.py").write_text("print('not executed during install')\n", encoding="utf-8")
    (skill_dir / "assets" / "template.txt").write_text("template", encoding="utf-8")
    (skill_dir / "agents" / "openai.yaml").write_text("display_name: External Review\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
