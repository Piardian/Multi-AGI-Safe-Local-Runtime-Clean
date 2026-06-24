# -*- coding: utf-8 -*-
"""The single official runtime: typed plan -> policy -> authorized tool call."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Callable

from audit import AuditLogger
import config
from execution_context import authorized_tool_call
from policy_engine import PolicyDecision, PolicyEngine, TypedToolCall
from tools.registry import ToolResult, execute_tool


LEGACY_TOOL_ALIASES = {
    "read_file": "read_file_limited",
    "write_file": "write_file_with_diff",
    "list_directory": "list_workspace_files",
    "run_test": "validate_python_syntax_sandboxed",
}


@dataclass
class RuntimePlan:
    task_id: str
    goal: str
    calls: list[TypedToolCall]
    rejected_actions: list[str] = field(default_factory=list)


@dataclass
class RuntimeReport:
    task_id: str
    status: str
    decisions: list[PolicyDecision]
    results: list[ToolResult]
    rejected_actions: list[str] = field(default_factory=list)


ApprovalCallback = Callable[[RuntimePlan, list[PolicyDecision]], bool]


class TaskRuntime:
    def __init__(self, policy: PolicyEngine | None = None, audit: AuditLogger | None = None) -> None:
        self.policy = policy or PolicyEngine()
        self.audit = audit or AuditLogger()

    def build_plan(self, goal: str, actions: list[dict]) -> RuntimePlan:
        task_id = uuid.uuid4().hex
        calls: list[TypedToolCall] = []
        rejected: list[str] = []
        for action in actions or []:
            raw_tool = str(action.get("tool") or action.get("type") or "").strip()
            if raw_tool == "complete":
                continue
            tool = LEGACY_TOOL_ALIASES.get(raw_tool, raw_tool)
            payload = {
                key: value
                for key, value in action.items()
                if key not in {"tool", "type", "reason", "requires_approval", "requires_user_approval", "continue_on_failure"}
            }
            if tool not in {
                "list_workspace_files", "read_file_limited", "write_file_with_diff", "validate_python_syntax_sandboxed",
                "get_system_info", "read_recent_event_logs", "read_reliability_history", "get_last_boot_reason",
                "list_recent_crashes", "check_disk_health_readonly", "list_driver_errors",
                "list_windows_update_history", "list_startup_apps", "list_running_processes_summary",
                "web_search_public", "get_weather", "create_directory", "open_browser", "open_application",
                "open_folder", "copy_file", "move_file", "safe_delete_file", "search_files", "get_file_info",
                "discover_applications", "launch_application_resolved", "application_diagnostics",
                "close_application_process", "restart_application_resolved", "archive_application_logs",
                "backup_application_config", "clear_safe_application_cache",
            }:
                rejected.append(f"Unsupported or unsafe action rejected: {raw_tool}")
                continue
            calls.append(
                TypedToolCall(
                    tool=tool,
                    payload=payload,
                    reason=str(action.get("reason", "")),
                    continue_on_failure=bool(action.get("continue_on_failure", False)),
                )
            )
        plan = RuntimePlan(task_id=task_id, goal=goal, calls=calls, rejected_actions=rejected)
        self.audit.record("task_planned", task_id, goal=goal, tools=[call.tool for call in calls], rejected_actions=rejected)
        return plan

    def inspect_plan(self, plan: RuntimePlan) -> list[PolicyDecision]:
        decisions = [self.policy.inspect(call) for call in plan.calls]
        self.audit.record(
            "plan_inspected",
            plan.task_id,
            decisions=[{"tool": call.tool, "risk": decision.risk, "valid": decision.valid, "requires_approval": decision.requires_approval, "reason": decision.reason} for call, decision in zip(plan.calls, decisions)],
        )
        return decisions

    def execute_plan(
        self,
        plan: RuntimePlan,
        approval_callback: ApprovalCallback | None = None,
        dev_auto_approve: bool = False,
    ) -> RuntimeReport:
        inspected = self.inspect_plan(plan)
        approval_needed = any(decision.requires_approval for decision in inspected)
        config.LAST_RUN_APPROVAL_REQUIRED = approval_needed
        config.LAST_RUN_APPROVAL_WOULD_BE_REQUIRED = approval_needed
        
        if not getattr(config, "PLAN_ONLY", False):
            # Check source file existence before prompting approval
            from tools.safe_file_ops import resolve_file_path, resolve_special_folder
            from tools.registry import ToolResult
            for call in plan.calls:
                if call.tool in {"move_file", "copy_file", "safe_delete_file", "get_file_info", "read_file_limited"}:
                    payload = call.payload or {}
                    src_key = "src" if call.tool in {"move_file", "copy_file"} else "path"
                    src_str = str(payload.get(src_key) or payload.get("source") or "").strip()
                    if src_str:
                        resolved = resolve_file_path(src_str)
                        if not resolved.exists():
                            desktop_dir = resolve_special_folder("desktop")
                            documents_dir = resolve_special_folder("documents")
                            downloads_dir = resolve_special_folder("downloads")
                            if resolved.parent.resolve() == desktop_dir.resolve():
                                err_msg = f"Masaüstünde {resolved.name} bulunamadı."
                            elif resolved.parent.resolve() == documents_dir.resolve():
                                err_msg = f"Belgeler klasöründe {resolved.name} bulunamadı."
                            elif resolved.parent.resolve() == downloads_dir.resolve():
                                err_msg = f"İndirilenler klasöründe {resolved.name} bulunamadı."
                            else:
                                err_msg = f"Kaynak dosya mevcut değil: {resolved.as_posix()}"
                            
                            self.audit.record("task_failed_missing_source", plan.task_id, path=src_str)
                            return RuntimeReport(plan.task_id, "failed", inspected, [ToolResult(False, err_msg)], plan.rejected_actions)

        if getattr(config, "PLAN_ONLY", False):
            config.LAST_RUN_BLOCKED_BY_PLAN_ONLY = True
            config.LAST_RUN_APPROVAL_GRANTED = False
            self.audit.record("task_blocked", plan.task_id, reason="plan_only_mode_enabled")
            return RuntimeReport(plan.task_id, "blocked", inspected, [], plan.rejected_actions)
        if any(not decision.valid for decision in inspected):
            self.audit.record("task_blocked", plan.task_id, reason="policy_validation_failed")
            return RuntimeReport(plan.task_id, "blocked", inspected, [], plan.rejected_actions)

        user_approved = False
        if approval_needed and not (dev_auto_approve and config.DEV_MODE):
            if approval_callback is None:
                self.audit.record("approval_missing", plan.task_id)
                return RuntimeReport(plan.task_id, "needs_approval", inspected, [], plan.rejected_actions)
            user_approved = bool(approval_callback(plan, inspected))
            config.LAST_RUN_APPROVAL_GRANTED = user_approved
            self.audit.record("approval_decision", plan.task_id, approved=user_approved, source="user")
            if not user_approved:
                return RuntimeReport(plan.task_id, "denied", inspected, [], plan.rejected_actions)
        elif approval_needed and dev_auto_approve and config.DEV_MODE:
            config.LAST_RUN_APPROVAL_GRANTED = True
        else:
            config.LAST_RUN_APPROVAL_GRANTED = False

        results: list[ToolResult] = []
        authorized: list[PolicyDecision] = []
        for call in plan.calls:
            decision = self.policy.authorize(call, user_approved=user_approved, dev_auto_approve=dev_auto_approve)
            authorized.append(decision)
            if not decision.permitted:
                self.audit.record("tool_blocked", plan.task_id, tool=call.tool, risk=decision.risk, reason=decision.reason)
                results.append(ToolResult(False, decision.reason))
                break
            self.audit.record("tool_started", plan.task_id, tool=call.tool, risk=decision.risk, approval_mode=decision.approval_mode)
            try:
                with authorized_tool_call(plan.task_id, call.tool, decision.approval_mode):
                    result = execute_tool(call.tool, call.payload)
            except Exception as exc:
                result = ToolResult(False, f"Tool execution failed safely: {exc}")
            results.append(result)
            if not hasattr(config, "LAST_RUN_TOOLS"):
                config.LAST_RUN_TOOLS = []
            config.LAST_RUN_TOOLS.append(call.tool)
            self.audit.record(
                "tool_finished",
                plan.task_id,
                tool=call.tool,
                risk=decision.risk,
                ok=result.ok,
                target_path=_result_path(result),
                changed_files=[_result_path(result)] if call.tool == "write_file_with_diff" and result.ok else [],
                content_fingerprint=_content_fingerprint(call.payload) if call.tool == "write_file_with_diff" else "",
                message=result.message[:500],
            )
            if not result.ok and not call.continue_on_failure:
                break

        if plan.rejected_actions:
            status = "blocked"
        elif results and all(result.ok for result in results):
            status = "success"
        elif len(results) == len(plan.calls):
            status = "partial"
        elif not plan.calls:
            status = "completed"
        else:
            status = "failed"
        self.audit.record("task_finished", plan.task_id, status=status, result_count=len(results))
        return RuntimeReport(plan.task_id, status, authorized, results, plan.rejected_actions)


def _result_path(result: ToolResult) -> str:
    if isinstance(result.data, dict):
        return str(result.data.get("path", ""))
    return ""


def _content_fingerprint(payload: dict) -> str:
    content = str(payload.get("content", ""))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
