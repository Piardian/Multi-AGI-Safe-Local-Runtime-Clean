# -*- coding: utf-8 -*-
"""General security policy for orchestrator actions."""

from __future__ import annotations

import re


LOW_RISK = {"conversation", "content_generation", "read_file", "open_browser", "browser_model_task"}
MEDIUM_RISK = {"write_file", "run_command", "coding", "file_workspace_task", "create_scheduled_task"}
HIGH_RISK_PATTERNS = [
    r"\bsil\b",
    r"\bdelete\b",
    r"\bformat\b",
    r"\bprogram kur\b",
    r"\binstall\b",
    r"\bmail gonder\b",
    r"\bodeme\b",
    r"\bhesap\b",
    r"\bapi key\b",
    r"\bsecret\b",
    r"\btoken\b",
    r"\bsifre\b",
    r"\bpassword\b",
    r"\bsistem ayari\b",
]


def classify_risk(goal: str, action_type: str = "") -> str:
    normalized = (goal or "").lower()
    if any(re.search(pattern, normalized) for pattern in HIGH_RISK_PATTERNS):
        return "high"
    if "alarm" in normalized or "hatirlatici" in normalized or action_type == "create_scheduled_task":
        return "medium"
    if action_type in MEDIUM_RISK:
        return "medium"
    return "low"


def requires_approval(risk: str) -> bool:
    return risk in {"medium", "high"}


APPROVAL_TOOLS = {
    "write_file",
    "run_command",
    "create_scheduled_task",
    "create_alarm",
    "delete_file",
    "remove_file",
    "system_setting",
}


def action_requires_approval(action: dict, risk: str = "low") -> tuple[bool, str]:
    tool = str(action.get("tool") or action.get("type") or "").lower()
    command = str(action.get("command", "")).lower()
    path = str(action.get("path", "")).lower()
    if risk in {"medium", "high"}:
        return True, f"{risk} risk gorev kullanici onayi ister."
    if tool in APPROVAL_TOOLS:
        return True, f"{tool} araci kullanici onayi ister."
    if any(token in command for token in ["del ", "remove-item", "rmdir", "reg ", "shutdown", "format "]):
        return True, "Riskli terminal komutu kullanici onayi ister."
    if any(token in path for token in [".env", "secret", "token", "password", "api_key"]):
        return True, "Duyarli dosya islemi kullanici onayi ister."
    return False, ""
