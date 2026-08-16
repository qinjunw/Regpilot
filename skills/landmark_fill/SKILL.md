---
name: landmark-fill
description: Portfolio-only simulation of a guarded Landmark fill contract; it never opens Chrome or writes a workbook.
regpilot_skill: true
---

# 地标填报

> 公开作品版只执行确定性模拟，返回带 `demo_mode=true` 的校验结果，不连接真实平台。

## Overview

This is one operator-facing Agent Skill for guarded Landmark filling. RegPilot may expose it as a business capability, but internal operation nodes remain backend orchestration details.

Read `reference/workflow.md` when deciding how to use this skill, how to handle manual correction, or how to explain a blocked fill workflow to the operator.

## RegPilot Manifest

```json regpilot_manifest
{
  "id": "formfill.landmark",
  "title": "地标填报",
  "description": "根据工作簿、工作表和值所在列，在受控 Chrome 中执行地标平台受控填报，并停在保存/提交前。",
  "category": "fill",
  "status": "available",
  "task_id": "landmark_fill",
  "risk_level": "high",
  "requires_confirmation": true,
  "triggers": [
    "地标",
    "地标填报",
    "地标平台",
    "上海地标"
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
    "value_column"
  ],
  "default_inputs": {
    "sheet": "SHGL备案参数"
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
      "title": "准备填报工作流",
      "boundary": "缺少工作簿、工作表、值所在列、受控页面或受控 Chrome 时停止"
    },
    {
      "id": "fill_current_page",
      "title": "填报当前页",
      "boundary": "当前页字段写入、黄灯/红灯验证或页面提示可能阻塞"
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
- Emit a Skill Command only when the operator clearly intends Landmark filling.
- Use backend defaults such as `SHGL备案参数` when the manifest supplies them.
- Do not infer Landmark intent from a workbook filename alone; require the operator message or selected skill to mention Landmark filling.
- Do not invent workbook paths, value columns, controlled Chrome state, or workflow progress.
- Do not request or imply final save/submit. The default boundary is to stop before submission.
