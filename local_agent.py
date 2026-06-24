# -*- coding: utf-8 -*-
"""Local agent wrapper with human approval gates for risky actions."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from hybrid_orchestrator import (
    HybridReport,
    format_report,
    is_placeholder_path,
    is_risky_path,
    normalize_workspace_path,
    run_hybrid_goal,
)


RISKY_COMMAND_PATTERNS = [
    r"\bdel\b",
    r"\berase\b",
    r"\brmdir\b",
    r"\bremove-item\b",
    r"\brm\b",
    r"\bpip install\b",
    r"\bnpm install\b",
    r"\bwinget\b",
    r"\bchoco\b",
    r"\breg\b",
    r"\bnetsh\b",
    r"\bschtasks\b",
    r"\bshutdown\b",
]

RISKY_PATH_PATTERNS = [
    r"(^|/|\\)\.env$",
    r"api[_-]?key",
    r"secret",
    r"token",
    r"password",
]


def action_requires_approval(action: dict) -> tuple[bool, str]:
    tool = str(action.get("tool", "")).lower()
    path = str(action.get("path", ""))
    command = str(action.get("command", ""))

    if action.get("requires_approval"):
        return True, "Aksiyon model tarafindan onay gerektiriyor olarak isaretlenmis."

    if tool == "run_command":
        lowered = command.lower()
        for pattern in RISKY_COMMAND_PATTERNS:
            if re.search(pattern, lowered):
                return True, f"Riskli terminal komutu: {command}"

    if tool == "write_file":
        lowered_path = path.lower()
        for pattern in RISKY_PATH_PATTERNS:
            if re.search(pattern, lowered_path):
                return True, f"Gizli/duyarli dosya yazimi: {path}"

    return False, ""


def collect_risky_actions(actions: list[dict]) -> list[tuple[dict, str]]:
    risky: list[tuple[dict, str]] = []
    for action in actions:
        needs_approval, reason = action_requires_approval(action)
        if needs_approval:
            risky.append((action, reason))
    return risky


def build_plan_only_report(user_goal: str, workspace_files: list[str], route: dict) -> dict:
    workspace_set = set(workspace_files)
    mentioned_paths = _extract_mentioned_paths(user_goal)
    errors: list[str] = []
    needed_files: list[str] = []
    actions: list[dict] = []
    normalized_goal = _normalize(user_goal)

    for raw_path in mentioned_paths:
        normalized = normalize_workspace_path(raw_path)
        if is_placeholder_path(normalized):
            errors.append(f"Placeholder path reddedildi; dosya workspace_files icinde yok: {normalized}")
            continue
        if is_risky_path(normalized):
            errors.append(f"Riskli path reddedildi: {raw_path}")
            continue
        if normalized not in workspace_set:
            errors.append(f"Dosya workspace_files icinde yok: {normalized}")
            continue
        if normalized not in needed_files:
            needed_files.append(normalized)

    if not mentioned_paths:
        if any(token in normalized_goal for token in ["proje", "incele", "analiz"]):
            preferred = [
                "bridge.py",
                "hybrid_orchestrator.py",
                "agents.py",
                "config.py",
                "security.py",
                "router.py",
                "prompt_architect.py",
                "local_agent.py",
                "browser_gpt.py",
                "requirements.txt",
            ]
            needed_files = [path for path in preferred if path in workspace_set]

    actions.extend(
        {
            "type": "read_file",
            "path": path,
            "reason": "Projeyi guvenli sekilde incelemek icin gercek workspace dosyasi.",
        }
        for path in needed_files
    )

    if "chrome" in normalized_goal and "ac" in normalized_goal:
        actions.append(
            {
                "type": "open_browser",
                "target": "chrome",
                "reason": "Kullanici Chrome uygulamasini acma eylemi istiyor.",
            }
        )

    if "alarm" in normalized_goal and "kur" in normalized_goal:
        actions.append(
            {
                "type": "create_alarm",
                "title": "Alarm",
                "datetime": _extract_datetime_hint(user_goal),
                "reason": "Kullanici alarm/hatirlatici kurma eylemi istiyor.",
                "requires_approval": True,
            }
        )

    return {
        "summary": "Plan-only raporu: hicbir dosya yazilmadi, hicbir komut calistirilmadi.",
        "workspace_files": workspace_files,
        "needed_files": needed_files,
        "actions": actions,
        "risk_level": route.get("risk", "low") if isinstance(route, dict) else "low",
        "requires_user_approval": False,
        "errors": errors,
    }


def _extract_mentioned_paths(text: str) -> list[str]:
    candidates = re.findall(
        r"(?P<path>(?:[A-Za-z]:)?(?:[~.]?[A-Za-z0-9_\- .]+[\\/])*[A-Za-z0-9_\- .]+\.[A-Za-z0-9]{1,8})",
        text or "",
    )
    cleaned: list[str] = []
    for candidate in candidates:
        normalized = candidate.strip().strip('"').strip("'").rstrip(".,;:")
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _normalize(text: str) -> str:
    replacements = {
        "\u0131": "i",
        "\u011f": "g",
        "\u00fc": "u",
        "\u015f": "s",
        "\u00f6": "o",
        "\u00e7": "c",
        "\u0130": "i",
        "\u011e": "g",
        "\u00dc": "u",
        "\u015e": "s",
        "\u00d6": "o",
        "\u00c7": "c",
        "ı": "i",
        "ğ": "g",
        "ü": "u",
        "ş": "s",
        "ö": "o",
        "ç": "c",
        "İ": "i",
        "Ğ": "g",
        "Ü": "u",
        "Ş": "s",
        "Ö": "o",
        "Ç": "c",
    }
    lowered = (text or "").lower()
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    return lowered


def _extract_datetime_hint(text: str) -> str:
    normalized = _normalize(text)
    day_offset = 1 if "yarin" in normalized else 0
    match = re.search(r"(\d{1,2})[:.](\d{2})", normalized)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if day_offset:
            return _iso_for_day_offset(day_offset, hour, minute)
        return f"next_occurrenceT{hour:02d}:{minute:02d}:00"
    match = re.search(r"\bgece\s+(\d{1,2})\b", normalized)
    if match:
        hour = int(match.group(1))
        if hour == 12:
            hour = 0
        if day_offset:
            return _iso_for_day_offset(day_offset, hour, 0)
        return f"next_occurrenceT{hour:02d}:00:00"
    match = re.search(r"\boglen\s+(\d{1,2})\b", normalized)
    if match:
        hour = int(match.group(1))
        if 1 <= hour <= 11:
            hour += 12
        if day_offset:
            return _iso_for_day_offset(day_offset, hour, 0)
        return f"next_noon_referenceT{int(match.group(1)):02d}:00:00"
    match = re.search(r"\bsaat\s+(\d{1,2})\b", normalized)
    if match:
        hour = int(match.group(1))
        if day_offset:
            return _iso_for_day_offset(day_offset, hour, 0)
        return f"next_occurrenceT{hour:02d}:00:00"
    return "needs_user_datetime_confirmation"


def _iso_for_day_offset(day_offset: int, hour: int, minute: int) -> str:
    target = datetime.now() + timedelta(days=day_offset)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat(timespec="seconds")


def run_local_agent(
    agent_prompt: str,
    original_goal: str = "",
    auto_approve: bool = False,
    execute: bool = True,
) -> HybridReport:
    """Plan/draft/critic wrapper. TaskRuntime is the only execution owner."""
    report = run_hybrid_goal(agent_prompt, execute=False)
    if report.critic.get("decision") != "approve":
        return report

    report.critic = {
        **report.critic,
        "execution": "deferred_to_task_runtime",
        "reason": "Plan was not executed by local_agent. TaskRuntime will validate policy and request approval.",
    }
    return report


def format_local_agent_report(report: HybridReport) -> str:
    return format_report(report)
