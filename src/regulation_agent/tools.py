from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


FillTaskProvider = Callable[[], tuple[Any, ...]]


@dataclass(frozen=True)
class DemoFillTask:
    id: str
    title: str
    description: str


DEMO_FILL_TASKS = (
    DemoFillTask("shanghaiData_fill", "上海数据填报演示", "模拟字段校验、人工介入和提交前停止。"),
    DemoFillTask("ota_fill", "OTA 填报演示", "模拟附件检查、字段校验和提交前停止。"),
    DemoFillTask("landmark_fill", "地标填报演示", "模拟字段校验和提交前停止。"),
)


def default_tool_inventory(fill_task_provider: FillTaskProvider | None = None) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = [
        {
            "name": "human_action.request_operator_choice",
            "title": "人工选择",
            "description": "在模型或编排器判断需要人工点击或选择时，把交互卡片推送到聊天流。",
            "category": "human_action",
            "risk_level": "medium",
            "requires_confirmation": True,
            "status": "available",
        },
        {
            "name": "context.resolve_task_context",
            "title": "任务上下文查询",
            "description": "由后端查询和回填工作空间、总表、映射表、目标列和目标工具。",
            "category": "context",
            "risk_level": "low",
            "requires_confirmation": False,
            "status": "pending_integration",
            "unavailable_reason": "等待后端任务上下文查询接口接入。",
        },
        {
            "name": "source.collect_official_page",
            "title": "官方来源采集",
            "description": "采集白名单官方网页并形成 Source Evidence。",
            "category": "research",
            "risk_level": "low",
            "requires_confirmation": False,
            "status": "pending_integration",
            "unavailable_reason": "等待官方来源采集工具实现。",
        },
        {
            "name": "source.ingest_local_documents",
            "title": "本地资料摄入",
            "description": "读取用户明确提供的 md/txt/docx/xlsx/文本型 pdf，并形成 Source Evidence。",
            "category": "research",
            "risk_level": "low",
            "requires_confirmation": False,
            "status": "available",
        },
        {
            "name": "source.search_evidence",
            "title": "资料证据检索",
            "description": "在已摄入资料中检索证据片段，并按证据 ID 渐进加载。",
            "category": "research",
            "risk_level": "low",
            "requires_confirmation": False,
            "status": "available",
        },
        {
            "name": "artifact.generate_regulatory_report",
            "title": "对照报告生成",
            "description": "把基于 Source Evidence 的法规解读 Markdown 落盘为 .md/.docx Regulatory Artifact。",
            "category": "artifact",
            "risk_level": "medium",
            "requires_confirmation": False,
            "status": "available",
        },
    ]
    tools.extend(_fill_task_tools(fill_task_provider))
    return tools


def _fill_task_tools(fill_task_provider: FillTaskProvider | None) -> list[dict[str, Any]]:
    tasks: tuple[Any, ...] = ()
    error = ""
    if fill_task_provider is None:
        tasks = DEMO_FILL_TASKS
    else:
        try:
            tasks = tuple(fill_task_provider())
        except Exception as exc:
            error = str(exc)
    if not tasks:
        return [
            {
                "name": "fill.pending",
                "title": "受控填报",
                "description": "地标、上海数据平台、OTA 等受控填报能力。",
                "category": "fill",
                "risk_level": "high",
                "requires_confirmation": True,
                "status": "pending_integration",
                "unavailable_reason": error or "等待 Fill Service 任务清单接入。",
            }
        ]
    return [
        {
            "name": f"fill.{getattr(task, 'id', 'unknown')}",
            "title": getattr(task, "title", "受控填报"),
            "description": getattr(task, "description", "受控填报能力。"),
            "category": "fill",
            "risk_level": "high",
            "requires_confirmation": True,
            "status": "available",
            "unavailable_reason": "",
        }
        for task in tasks
    ]
