---
name: automotive-regulation-interpretation
description: Create, review, or revise source-grounded automotive regulation interpretation reports from regulation PDFs, standards, templates, prompts, spreadsheets, comments, or company materials. Use when the user asks for automotive法规解读,法规差异分析,版本对比,认证/试验解读,工程师视角解读,法规条款拆解,专业模块接口匹配, or a reusable agent workflow/skill for generating such reports.
---

# Automotive Regulation Interpretation

Use this skill to produce an independent, audit-ready automotive regulation interpretation report. The report must help three audiences read the same regulation from different angles:

1. **法规视角**: scope, definitions, mandatory clauses, version history, transition rules, legal boundaries.
2. **认证视角**: approval path, test method, evidence package, markings, reporting, COP/production conformity.
3. **工程师视角**: affected systems, design variables, verification actions, module interfaces, risk controls.

Read `reference/report-workflow.md` when drafting or revising a full report, creating a DOCX deliverable, or checking quality.

## Source Tool Boundary

Use RegPilot source tools for every local document input. Do not claim to have read a file until a tool result supplies Source Evidence.

1. Call `regpilot_ingest_sources` with only the file paths the operator explicitly provided.
2. Use `regpilot_search_sources` to find relevant clauses, definitions, tables, or workbook rows.
3. For broad interpretation work, prefer `regpilot_build_evidence_bundle` to collect multiple topic queries into a bounded Source Evidence bundle before drafting.
4. Use `regpilot_load_source_slice` when a search excerpt or bundle excerpt is too small to support a conclusion.
5. Cite or describe conclusions from Source Evidence ids or locators such as PDF page, DOCX paragraph, text line, or XLSX sheet row.

Supported local inputs are `md`, `txt`, `docx`, `xlsx`, and text-based `pdf`. Do not request OCR, remote conversion, or Office beautification as part of this skill. If a PDF has no extractable text, mark it as not readable in this workflow instead of inferring content.

## Report Coverage Discipline

Before drafting the report, create a **报告覆盖清单** from `reference/report-workflow.md` and the operator's request. Use it as the contract for the final report structure.

- Keep every expected report section or required topic from the coverage checklist visible in the final report.
- If Source Evidence supports a section, write the supported interpretation and cite the evidence id or locator near the claim.
- If the provided materials do not mention an expected topic, keep the section or row and write `资料中未提及` instead of deleting it.
- If the topic requires a source that was not provided, such as prior versions for version comparison, write `输入资料不足，当前资料不支持判断`.
- Do not convert a failed search into a negative regulatory conclusion. “No evidence found” means not mentioned in source, not “not required,” unless the regulation text explicitly says so.

## Artifact Output

When the operator asks for an interpretation file, draft the report as Markdown after evidence retrieval, then call `regpilot_generate_interpretation_report`.

- Pass the active `collection_id`, report `title`, full `markdown`, and the `source_evidence_ids` actually used.
- Keep evidence ids or locators visible in the Markdown near the claims they support; the artifact tool rejects reports whose body has no Source Evidence reference.
- Request `formats: ["md", "docx"]` unless the operator asks for only one format.
- Leave `output_dir` empty unless the operator explicitly gives a target directory.
- Do not say a file was generated until the tool returns artifact paths.

## Source Hierarchy

Use the regulation or standard original text as the factual baseline. Treat commentary documents, prompt files, templates, company interpretations, Excel breakdowns, and prior drafts as secondary inputs.

Do not promote secondary material into final-report facts unless confirmed by the original regulation or another official source. If sources conflict, list the conflict, use the original regulation as the conservative baseline, and mark unresolved items as needing verification.

For current or changeable facts such as latest versions, in-force dates, official adoption, EU/GB implementation, agency practice, or certification acceptance, verify against official sources or mark as pending. Use absolute dates only.

## Workflow

1. **Inventory inputs**
   - Identify regulation originals, prior versions, templates/prompts, commentary files, company materials, Excel module lists, comments, and images.
   - Extract exact regulation number, series/amendment/supplement status, in-force dates, and source file names.

2. **Build the fact base**
   - Read the regulation original before reading interpretations.
   - Extract scope, exemptions, definitions, technical clauses, test methods, approval/marking clauses, transitional provisions, annexes, and figures.
   - For version comparison, obtain each version text or official amendment summary. If only one version is available, do not invent older-version changes.

3. **Create the report coverage checklist**
   - Build a coverage row for every expected section and topic in `reference/report-workflow.md`.
   - Record each row as `supported by Source Evidence`, `资料中未提及`, or `输入资料不足，当前资料不支持判断`.
   - Carry every row into the final report so expected topics do not drift, merge, or disappear.

4. **Separate report-facing content from process notes**
   - The final interpretation report must stand alone as a regulation document.
   - Do not include “与公司材料的关系”, prompt/template evaluation, generation process, or critique of prior drafts unless the user explicitly asks for an audit appendix.

5. **Write the three perspectives**
   - 法规视角: what the rule requires and where its boundaries are.
   - 认证视角: how compliance is demonstrated and what evidence is needed.
   - 工程师视角: what engineering teams must design, test, freeze, or control.

6. **Version Comparison**
   - Compare `current vs previous`, then `previous vs earlier` where sources exist, e.g. `R127.04 vs R127.03`, `R127.03 vs R127.02`.
   - Use version-level rows: scope, definitions, technical requirements, test area/method, certification/marking, transition rules, annexes, engineering impact.
   - Do not fragment this section into unrelated term-by-term comparisons.

7. **Core technical requirements**
   - Expand clauses that affect pass/fail, test areas, impactor selection, thresholds, test point selection, transition paths, or certification evidence.
   - Each expanded item should include: clause/topic, applicable object, regulatory requirement, test/verification method, pass/fail judgment, engineering action.
   - If a module-interface workbook is provided, add `法规模块 / 法规项 / 专业模块` only inside the core technical requirements section. Do not add module labels throughout the whole report.

8. **Figures**
   - Use regulation-original figures when they clarify test areas, reference lines, boundaries, measurement methods, or test-point allocation.
   - Place figures next to the related concept or clause and cite the original figure number.

9. **Deliverable discipline**
   - If producing DOCX, prefer A4 portrait unless the user requests otherwise.
   - Avoid wide tables that clip in Word. Use compact comparison tables for overview and “requirement cards” or narrow tables for detailed clauses.
   - Use the Documents skill for DOCX generation and render/Word-export QA when available.

## Red Lines

Do not deliver a report that:

- Invents limits, test conditions, dates, applicability, penalties, or certification consequences.
- Treats drafts, proposals, public articles, or company commentary as official regulation facts.
- Uses relative dates like “current/latest/soon” without absolute dates and source status.
- Hides exemptions, transition periods, or old-certificate acceptance rules.
- Places module-interface tags outside the core technical requirement analysis.
- Replaces complete engineering interpretation with only a change log, unless the user explicitly requests a change-only report.
