from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_AUTO_ADVANCE_POLICY = "until_before_final_submit"


@dataclass(frozen=True)
class UserIntent:
    task_id: str | None = None
    value_column: str | None = None
    auto_advance_policy: str = DEFAULT_AUTO_ADVANCE_POLICY


@dataclass(frozen=True)
class HarnessRequest:
    user_message: str
    workspace_dir: str | None = None
    task_id: str | None = None
    workbook_path: str | None = None
    sheet: str | None = None
    value_column: str | None = None
    attachment_folder: str | None = None
    auto_advance_policy: str | None = None
    include_values: bool = False
    max_steps: int = 20


class FillHarness(Protocol):
    def run_until_stop(self, request: HarnessRequest) -> dict[str, Any]: ...

    def resume_after_manual_fix(
        self,
        session_id: str,
        *,
        policy: str,
        include_values: bool,
        max_steps: int,
    ) -> dict[str, Any]: ...


def build_default_formfill_harness() -> FillHarness:
    return DemoFillHarness()


class DemoFillHarness:
    """Deterministic portfolio adapter; it never opens a browser or writes a workbook."""

    def run_until_stop(self, request: HarnessRequest) -> dict[str, Any]:
        missing = [
            name
            for name, value in (
                ("task_id", request.task_id),
                ("workbook_path", request.workbook_path),
                ("sheet", request.sheet),
                ("value_column", request.value_column),
            )
            if not value
        ]
        if request.task_id == "ota_fill" and not request.attachment_folder:
            missing.append("attachment_folder")
        if missing:
            return {
                "ok": False,
                "status": "needs_input",
                "code": "demo_missing_inputs",
                "missing": missing,
                "message": "公开演示适配器缺少必要输入；未访问浏览器或工作簿。",
                "demo_mode": True,
            }
        session_id = f"demo-{uuid.uuid4().hex[:12]}"
        return self._final_review(session_id, request)

    def resume_after_manual_fix(
        self,
        session_id: str,
        *,
        policy: str = DEFAULT_AUTO_ADVANCE_POLICY,
        include_values: bool = False,
        max_steps: int = 20,
    ) -> dict[str, Any]:
        del include_values, max_steps
        return {
            "ok": True,
            "status": "final_review",
            "session_id": session_id,
            "recommended_next_action": "final_review",
            "human_handoff_required": False,
            "demo_mode": True,
            "inputs": {"session_id": session_id, "auto_advance_policy": policy},
            "summary": self._summary(),
            "message": "模拟人工修正复核完成；没有访问真实页面或提交数据。",
        }

    def _final_review(self, session_id: str, request: HarnessRequest) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "final_review",
            "session_id": session_id,
            "task_id": request.task_id,
            "recommended_next_action": "final_review",
            "human_handoff_required": False,
            "demo_mode": True,
            "inputs": {
                "task_id": request.task_id,
                "excel_path": request.workbook_path,
                "sheet": request.sheet,
                "value_column": request.value_column,
                "attachment_folder": request.attachment_folder,
                "auto_advance_policy": request.auto_advance_policy or DEFAULT_AUTO_ADVANCE_POLICY,
            },
            "summary": self._summary(),
            "message": "公开演示已生成校验结果并停在提交前；没有读取或写入真实工作簿。",
        }

    @staticmethod
    def _summary() -> dict[str, Any]:
        return {
            "status": "final_review",
            "step_id": "demo_final_review",
            "step_title": "公开演示复核",
            "recommended_next_action": "final_review",
            "stopped_reason": "demo_boundary",
            "traffic_light": {"green_count": 3, "yellow_count": 1, "red_count": 0},
            "blocking_items": [],
            "manual_intervention_count": 0,
            "event_log": [
                {
                    "event_id": 1,
                    "type": "demo_boundary_reached",
                    "message": "模拟执行已停在提交前。",
                }
            ],
        }


def parse_user_intent(message: str) -> UserIntent:
    text = str(message or "")
    upper_text = text.upper()
    task_id: str | None = None
    if "上海数据" in text or "数据平台" in text or "数据中心" in text:
        task_id = "shanghaiData_fill"
    elif "OTA" in upper_text or "在线升级" in text:
        task_id = "ota_fill"
    elif "地标" in text:
        task_id = "landmark_fill"

    value_column: str | None = None
    for pattern in (
        r"\b([A-Za-z]{1,3})\s*列\b",
        r"([A-Za-z]{1,3})\s*列",
        r"值所在列\s*[:：]?\s*([A-Za-z]{1,3})",
        r"第\s*([A-Za-z]{1,3})\s*列",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value_column = match.group(1).upper()
            break

    if any(token in text for token in ("只填当前页", "手动", "不要自动下一步")):
        policy = "disabled"
    elif any(token in text for token in ("直到阻塞", "遇到阻塞")):
        policy = "until_blocked"
    else:
        policy = DEFAULT_AUTO_ADVANCE_POLICY
    return UserIntent(task_id=task_id, value_column=value_column, auto_advance_policy=policy)
