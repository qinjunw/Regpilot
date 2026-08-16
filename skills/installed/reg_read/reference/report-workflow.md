# Automotive Regulation Interpretation Report Workflow

Use this reference when producing or revising a full automotive regulation interpretation report.

## 1. Input Review

Create a working inventory before drafting:

| Input type | How to use it |
|---|---|
| Regulation/standard original text | Primary fact source for scope, definitions, limits, dates, tests, approval, annexes, and figures. |
| Prior regulation versions | Required for version-to-version comparison. If absent, mark comparison as not supported by inputs. |
| Interpretation articles/company materials | Secondary reading aids; use to identify issues, not as final authority. |
| Prompt/template/checklist files | Structural and quality constraints; do not mention them in the final report unless asked. |
| Commented drafts | User review signals; incorporate accepted comments into the next formal version. |
| Module/responsibility spreadsheets | Use only for module labels in the core technical requirements section, unless user asks for a separate responsibility matrix. |

Always note the source status internally:

- `法规原文事实`
- `官方外部事实`
- `输入材料解读`
- `专业归纳`
- `待核验`

The final report may be polished, but its claims must remain traceable to these categories.

When using RegPilot source tools:

- Use `regpilot_build_evidence_bundle` for broad multi-topic retrieval, such as scope, definitions, technical requirements, tests, approval, marking, COP, and transition rules.
- Use `regpilot_load_source_slice` only when the bundle or search excerpt is not enough for a specific claim.
- Keep `Evidence: <evidence_id>` or a concrete locator near report claims while drafting. The artifact generator requires at least one visible Source Evidence reference in the Markdown body.

## 2. Report Coverage Checklist

Before drafting prose, build a **报告覆盖清单**. This is a drafting guardrail, not a final-report appendix unless the user asks for it.

Minimum checklist rows:

| Coverage row | Required status |
|---|---|
| Regulation identity and source status | supported / not mentioned in source |
| Scope and exemptions | supported / not mentioned in source |
| Version history and transition rules | supported / not mentioned in source / not supported by inputs |
| Core terms and judgment concepts | supported / not mentioned in source |
| Core technical requirements | supported / not mentioned in source |
| Test procedure and evidence package | supported / not mentioned in source |
| Approval documents, markings, COP/IUC | supported / not mentioned in source |
| Project actions and deliverables | supported / not supported by inputs |
| Compliance risks and common mistakes | supported / not supported by inputs |
| Source list | supported |

Rules:

- Every checklist row must map to a visible final-report section, subsection, table row, or explicit placeholder.
- Use `资料中未提及` (`not mentioned in source`) when the provided Source Evidence does not mention an expected regulation topic.
- Use `输入资料不足，当前资料不支持判断` (`not supported by inputs`) when the topic requires missing materials, such as prior regulation versions, official adoption notices, module workbooks, figures, or company-specific data.
- Do not delete rows with zero search results.
- Do not treat zero search results as a negative rule. Write “资料中未提及” unless source text positively states that an item is exempt, not applicable, or not required.
- If a broad evidence bundle returns sparse coverage, run targeted searches for the missing checklist rows before drafting. If targeted searches still find nothing, keep the row and mark the status.

## 3. Recommended Final Report Structure

Use this structure for a complete independent report. Do not include process critique or company-material comparison unless explicitly requested.

1. **一页速览**
   - Regulation number/name, authority/system, type, scope, exemptions, key dates, core conclusion, priority actions.

2. **法规定位、适用范围与认证边界**
   - Explain whether it is whole-vehicle, component, installation, market-access, or test-method regulation.
   - Separate scope from exemptions.
   - Distinguish legal applicability from engineering impact.

3. **版本沿革与法规横向对比**
   - Use whole-version comparison: current vs previous, previous vs earlier.
   - Include a timeline only after the version relationship is clear.
   - If older versions are unavailable, state that the version comparison requires source text.

4. **核心术语与区域/判定概念**
   - Explain only terms needed for later clauses.
   - Include original regulation figures where useful.
   - Do not attach professional module labels in this chapter.

5. **核心技术要求逐条解读**
   - Expand all clauses that affect design, testing, pass/fail, certification, or transition use.
   - Add module interface labels only here when a module workbook is provided.

6. **测试程序、认证试验方式与资料要求**
   - Explain how to prove compliance: tests, samples, fixtures, conditions, reports, markings, approval documents, COP/IUC.

7. **认证资料、批准标志与生产一致性**
   - Use when approval, marking, extension, COP, software/version, or user manual requirements are important.

8. **项目应用清单**
   - Translate requirements into phased actions and deliverables.

9. **主要合规风险与易错点**
   - Include legal, certification, testing, engineering, timing, document, and production-consistency risks.

10. **资料来源**
   - List final sources used. Keep it concise.

Avoid adding a generic conclusion section if it repeats the速览 and action checklist.

## 4. Three-Perspective Writing

Each major topic should be readable from three views.

### 法规视角

Use for:

- Scope, exemptions, definitions.
- Mandatory limits and pass/fail criteria.
- Version status and transition provisions.
- Textual boundaries: what is required, allowed, exempted, or only monitored.

Style:

- Cite clause/topic.
- Use exact values and absolute dates.
- Do not infer beyond source text.

### 认证视角

Use for:

- Approval path and approval markings.
- Test method, sample state, test-point logic, reporting.
- Evidence package, information document, user manual, test report, simulation acceptance.
- COP/production conformity and approval extension.

Style:

- Express “how compliance is demonstrated”.
- Separate formal regulatory requirements from recommended project practice.

### 工程师视角

Use for:

- Affected systems, parts, software, calibration, packaging, materials, geometry.
- CAE/pre-test strategy, design variables, hard points, change control.
- Module interfaces.

Style:

- Translate each requirement into engineering actions and design controls.
- Keep module labels local to technical requirements.

## 5. Version Comparison Pattern

The user expects version-level comparison, not isolated term comparison.

Use this table form:

| Version step | Overall change | Scope/definitions | Technical requirements | Test/annex impact | Approval/transition impact | Engineering impact |
|---|---|---|---|---|---|---|
| Rxxx.04 vs Rxxx.03 |  |  |  |  |  |  |
| Rxxx.03 vs Rxxx.02 |  |  |  |  |  |  |

Rules:

- Compare adjacent versions in chronological order.
- If amendments/supplements are the real change unit, show them under the relevant series.
- Do not claim a change unless both source texts or an official amendment summary supports it.
- If the current PDF contains only current consolidated text, avoid reconstructing earlier-version differences from memory.

For UN regulations, inspect:

- Cover page in-force notes.
- Transitional provisions.
- Footnotes/editorial notes in consolidated texts.
- Annex revisions and approval-marking changes.
- Official amendment proposals only as secondary context unless adopted.

## 6. Core Technical Requirement Selection

Expand a clause when any answer is “yes”:

- Does it determine pass/fail?
- Does it define an impact area, measuring boundary, test point, or impactor?
- Does it contain a limit, threshold, speed, angle, force, moment, injury metric, timing, or geometry?
- Does it define when an alternative path or transition provision can be used?
- Does it require evidence in an information document, report, marking, user manual, or COP record?
- Does it trigger cross-functional engineering work?

Do not expand purely administrative text line by line. Summarize administrative clauses by effect.

Preferred detailed item format:

| Field | Content |
|---|---|
| Clause/topic | Exact clause or descriptive topic. |
| Applicable object | Vehicle, system, component, area, test condition. |
| Regulatory requirement | Exact requirement from source. |
| Test/verification | How compliance is demonstrated. |
| Pass/fail judgment | What must be true to pass. |
| Engineering action | What teams should design, verify, freeze, or control. |
| Module interface | Only if module workbook is provided. |

## 7. Module Interface Rules

Use module labels as reading aids, not as full responsibility assignment.

Allowed:

- Add `法规模块 / 法规项 / 专业模块` to core technical requirement items.
- Use multiple labels where one requirement crosses systems.
- Use labels to help engineers find relevant clauses.

Avoid:

- Adding module tags in速览, source sections, version comparison, risk summary, or every paragraph.
- Assigning specific departments or people unless the user provides a responsibility matrix and requests it.
- Creating a separate opening responsibility table when the user asked for embedded labels.

Example:

`模块接口：碰撞 / 行人保护 / 工程中心；玻璃、视野、刮水器洗涤器 / 安全玻璃 / 外饰。`

## 8. Figures and Visuals

Use original regulation figures when they explain:

- Reference lines and boundaries.
- Test areas and monitoring areas.
- Measuring methods.
- Test point allocation.
- Impactor setup or calibration.

Rules:

- Place figure near the related clause.
- Caption with original figure number.
- Do not use figures as decoration.
- If extracting from PDF, visually inspect crops before insertion.

## 9. DOCX/Deliverable Rules

When creating Word output:

- Prefer A4 portrait for formal reports unless user asks otherwise.
- Avoid landscape unless content truly requires a wide matrix.
- Avoid wide 5-6 column tables for detailed prose; use narrow tables or requirement cards.
- Use real Markdown tables only for genuinely tabular data; the artifact generator converts them into real Word tables.
- Set explicit table widths, grid columns, cell widths, and table layout to fixed.
- Use readable Chinese font such as Microsoft YaHei when appropriate.
- Include page footer with report title/version/date if useful.
- Render or export with Word/LibreOffice to inspect pages.

Minimum QA:

- No clipped table content.
- No table extends beyond page margins.
- Headings stay with first following table/card.
- Figures fit within margins and have captions.
- Comments/tracked changes removed unless requested.
- Accessibility audit has no high/medium findings where tooling exists.
- Artifact manifest quality records Source Evidence reference count and DOCX structure summary; treat missing references or missing expected tables as a failed handoff.

## 10. Quality Checklist

Before delivery, verify:

- Regulation number, version, amendment/supplement status are accurate.
- The report coverage checklist has no silently dropped row.
- Rows unsupported by the provided materials are explicitly marked `资料中未提及` or `输入资料不足，当前资料不支持判断`.
- Scope and exemptions are clear and separate.
- All dates are absolute.
- Version comparison is version-to-version, not a scattered term comparison.
- Technical clauses include test/verification and pass/fail judgment.
- Certification perspective is present: approval, test evidence, marking, reporting, COP.
- Engineering perspective is present: affected systems, design variables, module interfaces, change control.
- Module labels appear only in the core technical section.
- Secondary materials are not treated as official regulation facts.
- No unsupported claims of equivalence, mandatory status, penalties, or deadlines.
- Final report does not contain process notes such as “company material relationship” unless requested.

## 11. Common Failure Modes

Avoid these patterns:

- Dropping a required structure row because Source Evidence did not mention it.
- Only writing a revision/change-log when the user wants a complete interpretation.
- Turning a complete report into a critique of input materials.
- Treating company targets or internal 80% margins as regulation limits.
- Saying “04 continues WAD 2,500” without explaining WAD 2,100/BRRL transition exceptions.
- Spreading module labels throughout the document.
- Using landscape Word with clipped tables.
- Including generic “conclusion and suggested wording” sections that do not help the final report.
