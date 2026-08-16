---
name: automotive-regulation-source-index
description: Build a deduplicated structured index from local regulatory source articles, announcements, consultation notices, approval drafts, release notices, and attachment lists. Use when the user asks to 整理法规公告, 征求意见, 报批稿, 发布稿, 法规动态, 附件清单, or generate a structured regulation source index from local files.
---

# Automotive Regulation Source Index

Use this skill to turn locally collected regulatory source material into a structured, deduplicated regulation index.

Read `reference/index-workflow.md` before processing a folder, recording extracted entries, or exporting the structured result.

## Source And State Boundary

Use RegPilot tools for every local source path. Do not claim to have read or indexed a file until the relevant tool returns a result.

1. Call `regpilot_stage_regulation_sources` with only the file or directory paths the operator explicitly provided.
2. Use `next_sources` as the work queue. Decide a small batch size yourself from the source complexity and context budget; do not ask the backend to hard-code a business batch size.
3. If a collected directory has a body page such as `正文.md` or `正文.html`, treat that body page as the source article and its neighboring files as attachments.
4. If one source article exposes the same item in multiple formats such as `.docx` and `.pdf`, read the `main_path` selected by the tool and keep the alternates as attachments; do not process each format as a separate source.
5. For each source, call `regpilot_ingest_sources` on that specific source path, then use `regpilot_build_evidence_bundle` or `regpilot_search_sources` to retrieve Source Evidence.
6. Call `regpilot_record_regulation_entries` for every processed source article.
7. If a source cannot be parsed, call `regpilot_record_regulation_entries` with `source_status: "unprocessed"`, empty `entries`, and a short `message` explaining the reason.
8. If a source was read but no regulation identity was found, call `regpilot_record_regulation_entries` with `source_status: "processed_with_no_entries"`, empty `entries`, and a short `message`.
9. When the operator asks for the structured file, call `regpilot_export_regulation_index`.

Supported local source formats are `md`, `txt`, `html`, `json`, `jsonl`, `csv`, `docx`, `xlsx`, and text-based `pdf`. Do not request OCR, remote conversion, or free command-line access.

## Extraction Contract

Extract one structured entry per regulation or standard identity mentioned by the source. The backend will merge the same regulation by number first and name second, preserving status history inside one regulation record.

For each entry, capture:

- `regulation_number`: official regulation or standard number when stated.
- `regulation_name`: regulation or standard name. If only a number is available, keep the number and write the best available title from the source; do not invent a formal title.
- `regulation_status`: examples include `立项`, `征求意见`, `报批稿`, `发布`, `实施`, `修订`, `废止`, or `未知`.
- `event_date`: the main date tied to that status, preferably `YYYY-MM-DD`; leave empty if not stated.
- `date_type`: for example `公告日期`, `发布日期`, `实施日期`, or `会议日期`.
- `notice_date`: announcement or consultation start date.
- `comment_deadline`: consultation feedback deadline. Do not create a second duplicate entry only for the deadline.
- `effective_date`: implementation or in-force date.
- `issuing_body`: the issuing or collecting organization when stated.
- `attachments`: files or links explicitly attached to the source, including draft text, compiled comments, annexes, Word/PDF attachments, or standard files.
- `source_evidence_ids`: Source Evidence ids that support the row.
- `confidence`: `high`, `medium`, `low`, or `unknown`.

Do not create separate entries only because one regulation has multiple dates or status mentions. Put the best current status in `regulation_status`; the backend keeps `status_history`.

If an expected field is not mentioned by the source, leave it empty or write `资料中未提及` in `notes`; do not infer from outside memory.

## Output Discipline

The chat answer after a run should be short. Do not paste the full structured index or Markdown tables into chat unless the operator explicitly asks.

After export, report the artifact paths and counts returned by `regpilot_export_regulation_index`.
