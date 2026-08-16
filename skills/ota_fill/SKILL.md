---
name: ota-fill
description: Portfolio-only simulation of a guarded OTA fill contract; it never opens Chrome, reads attachments, or writes a workbook.
regpilot_skill: true
---

# OTA平台填报

> 公开作品版只执行确定性模拟，返回带 `demo_mode=true` 的校验结果，不连接真实平台。

## Overview

This is one operator-facing Agent Skill for guarded OTA Platform filling. RegPilot may expose it as a business capability, but internal operation nodes remain backend orchestration details.

Read `reference/workflow.md` when deciding how to use this skill, how to handle OTA attachments, how to handle manual correction, or how to explain a blocked OTA workflow to the operator.

## RegPilot Manifest

```json regpilot_manifest
{
  "id": "formfill.ota",
  "title": "OTA平台填报",
  "description": "根据 OTA 总表、工作表、值所在列和附件文件夹，在受控 Chrome 中执行 OTA 平台受控填报，并停在保存/提交前。",
  "category": "fill",
  "status": "available",
  "task_id": "ota_fill",
  "risk_level": "high",
  "requires_confirmation": true,
  "triggers": [
    "OTA",
    "OTA平台",
    "在线升级",
    "软件升级",
    "ota_fill"
  ],
  "intent_keywords": [
    "填",
    "填报",
    "填写",
    "备案",
    "自动填",
    "跑一下",
    "处理"
  ],
  "required_inputs": [
    "workbook_path",
    "sheet",
    "value_column",
    "attachment_folder"
  ],
  "default_inputs": {
    "sheet": "REEV车型及功能备案细分"
  },
  "input_resolvers": [
    "operator_message",
    "session_task_context",
    "formfill_harness_validation"
  ],
  "allowed_tools": [
    "formfill_run_until_stop",
    "formfill_resume_after_manual_fix"
  ],
  "run_policy_default": "until_before_final_submit",
  "allowed_run_policies": [
    "disabled",
    "until_blocked",
    "until_before_final_submit"
  ],
  "submission_safety_boundary": true,
  "command": {
    "default_goal": "run_until_stop",
    "regulatory_tool": "formfill_run_until_stop"
  },
  "references": [
    "reference/workflow.md"
  ],
  "operation_nodes": [
    {
      "id": "prepare_fill_run",
      "title": "准备 OTA 填报工作流",
      "boundary": "缺少 OTA 总表、工作表、值所在列、附件文件夹、受控页面或受控 Chrome 时停止"
    },
    {
      "id": "fill_current_page",
      "title": "填报当前 OTA 页面",
      "boundary": "只处理当前打开的 OTA 页面或标签页；隐藏标签页不填"
    },
    {
      "id": "handle_page_attachments",
      "title": "处理当前页附件",
      "boundary": "只使用操作员确认的本地附件文件夹，不远程下载或猜测附件"
    },
    {
      "id": "advance_after_current_page",
      "title": "点击下一步并确认",
      "boundary": "网页确认、页面提示或阻塞可能要求人工修正"
    },
    {
      "id": "manual_correction_review",
      "title": "人工修正后复核并继续",
      "boundary": "只复核人工修正后的当前页并推进，不重新填写当前页"
    }
  ]
}
```

## Model Guidance

- Treat this as a high-level business skill. Do not expose operation nodes to the operator as separate skills.
- Emit a Skill Command only when the operator clearly intends OTA Platform filling.
- Use backend defaults such as `REEV车型及功能备案细分` when the manifest supplies them.
- Require an attachment folder for OTA; do not treat the workbook folder as the attachment folder unless the backend resolver confirms it.
- Do not invent workbook paths, attachment folders, value columns, controlled Chrome state, or workflow progress.
- Do not request or imply final save/submit. The default boundary is to stop before submission.
