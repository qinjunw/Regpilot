# Regulation Source Index Workflow

This workflow creates a structured regulation-source index from locally collected public materials. It is not a regulation interpretation report.

## Processing Steps

1. Stage sources with `regpilot_stage_regulation_sources`.
2. Process `next_sources` in small batches chosen by the Agent from the source complexity and context budget. The backend supplies state and safety limits, not a fixed business batch size.
3. When a collected directory has `正文.md` or `正文.html`, treat that body page as the source article and use neighboring files as attachments.
4. When one item is available in multiple same-stem formats such as `.docx` and `.pdf`, read the staged `main_path` and keep the alternate files as attachments instead of re-processing them as separate sources.
5. Search or bundle evidence around these topics:
   - regulation or standard numbers
   - regulation or standard names
   - announcement title
   - status terms such as 立项, 征求意见, 报批稿, 发布, 实施, 修订, 废止
   - dates, deadlines, issuing body, and attachments
6. Record entries with `regpilot_record_regulation_entries`.
7. Export with `regpilot_export_regulation_index` when the operator asks for a structured file.

## Normalization Rules

- Treat one regulation number/name as one regulation index identity.
- If the same regulation appears in multiple sources, record it again through the tool; the backend will merge status history, source evidence, dates, and attachments.
- If one source mentions multiple regulations, create multiple entries.
- If one regulation has multiple status events, keep one entry for that regulation identity and use the most advanced explicit status for `regulation_status`.
- Do not create a second entry only because the source has an opinion deadline. Store that deadline in `comment_deadline`.
- If the source only mentions a package or batch, capture each regulation explicitly named in that package.
- Do not convert "not found in this source" into a regulatory conclusion.

## Source Status Rules

- `processed`: the source was read and all extractable regulation identities from that source were recorded.
- `processed_with_no_entries`: the source was read but no regulation identity was found.
- `unprocessed`: the source could not be read, was unsupported, had no extractable text, or requires human/manual handling before extraction.

## Attachment Rules

Record attachments only when the source text or collected package clearly identifies them. Good attachment fields include:

- file name or title
- local path or URL when available
- file type such as pdf, docx, xlsx
- relationship such as draft text, official release, annex, comment form, compilation table

Do not invent missing file paths or URLs.
