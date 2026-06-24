# -*- coding: utf-8 -*-
"""Single policy decision point for official local tool calls."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import config
import security
from data_policy import is_sensitive_path, validate_public_query


TOOL_RISKS = {
    "list_workspace_files": "low",
    "read_file_limited": "low",
    "write_file_with_diff": "medium",
    "validate_python_syntax_sandboxed": "medium",
    "get_system_info": "low",
    "read_recent_event_logs": "low",
    "read_reliability_history": "low",
    "get_last_boot_reason": "low",
    "list_recent_crashes": "low",
    "check_disk_health_readonly": "low",
    "list_driver_errors": "low",
    "list_windows_update_history": "low",
    "list_startup_apps": "low",
    "list_running_processes_summary": "low",
    "web_search_public": "low",
    "get_weather": "low",
    "create_directory": "medium",
    "open_browser": "medium",
    "open_application": "medium",
    "open_folder": "medium",
    "copy_file": "medium",
    "move_file": "high",
    "safe_delete_file": "high",
    "search_files": "low",
    "get_file_info": "low",
    "discover_applications": "low",
    "launch_application_resolved": "high",
    "application_diagnostics": "low",
    "close_application_process": "high",
    "restart_application_resolved": "high",
    "archive_application_logs": "medium",
    "backup_application_config": "medium",
    "clear_safe_application_cache": "high",
}

DIAGNOSTIC_TOOLS = {
    "get_system_info",
    "read_recent_event_logs",
    "read_reliability_history",
    "get_last_boot_reason",
    "list_recent_crashes",
    "check_disk_health_readonly",
    "list_driver_errors",
    "list_windows_update_history",
    "list_startup_apps",
    "list_running_processes_summary",
}


@dataclass(frozen=True)
class TypedToolCall:
    tool: str
    payload: dict
    reason: str = ""
    continue_on_failure: bool = False


@dataclass
class PolicyDecision:
    valid: bool
    permitted: bool
    requires_approval: bool
    risk: str
    reason: str
    preview: dict = field(default_factory=dict)
    approval_mode: str = "none"


class PolicyEngine:
    def inspect(self, call: TypedToolCall) -> PolicyDecision:
        risk = TOOL_RISKS.get(call.tool, "high")
        if call.tool not in TOOL_RISKS:
            return PolicyDecision(False, False, True, risk, f"Tool is not allowlisted: {call.tool}")

        payload = call.payload or {}
        path = str(payload.get("path") or payload.get("target") or "")
        force_approval = False

        if call.tool in {"open_folder", "copy_file", "move_file", "safe_delete_file", "search_files", "get_file_info"}:
            from tools.safe_file_ops import resolve_file_path, resolve_special_folder
            paths_to_check = []
            for key in ["path", "target", "src", "source", "dst", "destination"]:
                val = str(payload.get(key) or "").strip()
                if val:
                    paths_to_check.append(val)
            
            force_approval = False
            for raw_p in paths_to_check:
                if ".." in raw_p:
                    return PolicyDecision(False, False, True, risk, "Path traversal attempt detected (.. is not allowed).")
                
                # Resolve the path to absolute and normalized version
                try:
                    resolved_p = resolve_file_path(raw_p)
                except Exception:
                    resolved_p = Path(raw_p)
                
                p_str = resolved_p.as_posix().lower()
                
                if any(sec in p_str for sec in [".env", "token", "api key", "api_key", "secret"]):
                    return PolicyDecision(False, False, True, risk, "Access denied: Korumalı dosya/klasör.")
                
                protected_dirs = [
                    "c:/windows", "c:/program files", "c:/program files (x86)", 
                    "system32", ".venv", ".git", "node_modules", "logs/task_memory.jsonl"
                ]
                for d in protected_dirs:
                    if d in p_str or p_str.startswith(d):
                        return PolicyDecision(False, False, True, risk, "Access denied: Korumalı dosya/klasör.")
                
                # 2. Path resolution check
                safe_bases = [
                    resolve_special_folder("desktop"),
                    resolve_special_folder("documents"),
                    resolve_special_folder("downloads"),
                    Path(config.PROJECT_ROOT).resolve()
                ]
                
                is_under_safe = False
                for base in safe_bases:
                    try:
                        resolved_p.relative_to(base)
                        is_under_safe = True
                        break
                    except ValueError:
                        pass
                
                if not is_under_safe:
                    force_approval = True

        if call.tool in {"read_file_limited", "write_file_with_diff", "validate_python_syntax_sandboxed", "create_directory"}:
            if not path:
                return PolicyDecision(False, False, risk != "low", risk, f"{call.tool} requires a path.")
            if is_sensitive_path(path):
                return PolicyDecision(False, False, True, risk, "Sensitive paths are blocked by the data policy.")
            if ".." in path:
                return PolicyDecision(False, False, True, risk, "Path traversal attempt detected (.. is not allowed).")
            
            if call.tool in {"read_file_limited", "write_file_with_diff", "validate_python_syntax_sandboxed"}:
                try:
                    resolved = Path(security.validate_path(path))
                except Exception as exc:
                    return PolicyDecision(False, False, True, risk, str(exc))
                if call.tool == "read_file_limited" and not resolved.is_file():
                    return PolicyDecision(False, False, False, risk, "Read target does not exist.")
                if call.tool == "write_file_with_diff":
                    relative = resolved.relative_to(Path(config.PROJECT_ROOT).resolve()).as_posix()
                    if relative in security.PROTECTED_FILES:
                        return PolicyDecision(False, False, True, risk, f"Protected runtime file cannot be changed: {relative}")
                    try:
                        security._validate_extension(resolved)
                    except Exception as exc:
                        return PolicyDecision(False, False, True, risk, str(exc))

        if call.tool == "open_browser":
            target = str(payload.get("target") or "").strip()
            if target and is_sensitive_path(target):
                return PolicyDecision(False, False, True, risk, "Sensitive paths/URLs are blocked by the data policy.")

        if call.tool == "open_application":
            app = str(payload.get("app") or "").strip().lower()
            if app not in {"notepad", "notepad.exe", "calc", "calc.exe"}:
                return PolicyDecision(False, False, True, risk, f"Application is not allowlisted: {app}")

        if call.tool in DIAGNOSTIC_TOOLS:
            forbidden = {"command", "script", "arguments", "powershell", "shell"} & set(payload)
            if forbidden:
                return PolicyDecision(False, False, False, risk, f"Diagnostic tools do not accept command-like fields: {', '.join(sorted(forbidden))}")
            if "limit" in payload:
                try:
                    limit = int(payload["limit"])
                except (TypeError, ValueError):
                    return PolicyDecision(False, False, False, risk, "Diagnostic limit must be an integer.")
                if not 1 <= limit <= 50:
                    return PolicyDecision(False, False, False, risk, "Diagnostic limit must be between 1 and 50.")

        if call.tool == "web_search_public":
            forbidden = {"path", "content", "command", "script", "url", "headers", "body"} & set(payload)
            if forbidden:
                return PolicyDecision(False, False, False, risk, f"Public web search does not accept non-public fields: {', '.join(sorted(forbidden))}")
            valid_query, reason = validate_public_query(str(payload.get("query", "")))
            if not valid_query:
                return PolicyDecision(False, False, False, risk, reason)
            if "limit" in payload:
                try:
                    limit = int(payload["limit"])
                except (TypeError, ValueError):
                    return PolicyDecision(False, False, False, risk, "Public web result limit must be an integer.")
                if not 1 <= limit <= 10:
                    return PolicyDecision(False, False, False, risk, "Public web result limit must be between 1 and 10.")

        preview: dict = {"tool": call.tool, "path": path, "reason": call.reason}
        if call.tool == "write_file_with_diff":
            content = payload.get("content")
            if not isinstance(content, str):
                return PolicyDecision(False, False, True, risk, "write_file_with_diff requires string content.")
            if len(content) > config.MAX_WRITE_FILE_CHARS:
                return PolicyDecision(False, False, True, risk, "Proposed file content exceeds the configured limit.")
            try:
                old = security.safe_read_file(path)
            except FileNotFoundError:
                old = ""
            preview["diff"] = "\n".join(
                list(
                    difflib.unified_diff(
                        old.splitlines(),
                        content.splitlines(),
                        fromfile=f"{path} (old)",
                        tofile=f"{path} (new)",
                        lineterm="",
                    )
                )[: config.MAX_DIFF_LINES]
            )

        permitted = (risk == "low" and not force_approval)
        requires_app = (risk != "low" or force_approval)
        return PolicyDecision(True, permitted, requires_app, risk, "Policy validation passed.", preview)

    def authorize(self, call: TypedToolCall, user_approved: bool, dev_auto_approve: bool = False) -> PolicyDecision:
        decision = self.inspect(call)
        if not decision.valid:
            return decision
        if not decision.requires_approval:
            decision.permitted = True
            return decision
        if user_approved:
            decision.permitted = True
            decision.approval_mode = "user"
            return decision
        if dev_auto_approve and config.DEV_MODE:
            decision.permitted = True
            decision.approval_mode = "dev_override"
            return decision
        decision.permitted = False
        decision.reason = "Explicit user approval is required before this tool can run."
        return decision
