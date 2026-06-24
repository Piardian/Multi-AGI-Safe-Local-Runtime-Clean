# -*- coding: utf-8 -*-
"""Deterministic, read-only diagnostic playbooks.

Each playbook is a fixed sequence of existing typed capabilities.  It never
creates a shell command, calls a model, or enables a new permission.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticPlaybook:
    scenario: str
    title: str
    tools: tuple[str, ...]
    markers: tuple[str, ...]

    def actions(self) -> list[dict]:
        return [
            {
                "type": tool,
                "limit": 20,
                "reason": f"Deterministic {self.scenario} playbook adimi.",
                "continue_on_failure": True,
            }
            for tool in self.tools
        ]


PLAYBOOKS = (
    DiagnosticPlaybook(
        "unexpected_restart_diagnosis",
        "Beklenmedik yeniden baslatma teshisi",
        (
            "get_system_info",
            "get_last_boot_reason",
            "read_recent_event_logs",
            "read_reliability_history",
            "list_driver_errors",
            "list_windows_update_history",
            "list_recent_crashes",
        ),
        ("unexpected_restart_diagnosis", "beklenmedik yeniden baslat", "beklenmedik yeniden başlat", "ani restart", "unexpected restart", "kernel power", "neden yeniden basladi", "neden yeniden başladı"),
    ),
    DiagnosticPlaybook(
        "blue_screen_or_crash_diagnosis",
        "Mavi ekran veya cokme teshisi",
        (
            "get_system_info",
            "list_recent_crashes",
            "read_recent_event_logs",
            "read_reliability_history",
            "list_driver_errors",
            "list_windows_update_history",
        ),
        ("blue_screen_or_crash_diagnosis", "mavi ekran", "blue screen", "bsod", "cokme", "çökme", "uygulama hatasi", "uygulama hatası"),
    ),
    DiagnosticPlaybook(
        "slow_pc_diagnosis",
        "Yavas bilgisayar teshisi",
        (
            "get_system_info",
            "list_running_processes_summary",
            "list_startup_apps",
            "check_disk_health_readonly",
            "read_recent_event_logs",
            "read_reliability_history",
            "list_windows_update_history",
        ),
        ("slow_pc_diagnosis", "yavas pc", "yavaş pc", "bilgisayar yavas", "bilgisayar yavaş", "slow computer", "slow pc", "kasıyor", "kasiyor", "donuyor"),
    ),
    DiagnosticPlaybook(
        "driver_problem_diagnosis",
        "Surucu problemi teshisi",
        (
            "get_system_info",
            "list_driver_errors",
            "read_recent_event_logs",
            "list_windows_update_history",
        ),
        ("driver_problem_diagnosis", "driver sorunu", "driver problemi", "surucu sorunu", "sürücü sorunu", "surucu hatasi", "sürücü hatası"),
    ),
    DiagnosticPlaybook(
        "disk_space_or_disk_health_diagnosis",
        "Disk alani veya disk sagligi teshisi",
        (
            "get_system_info",
            "check_disk_health_readonly",
            "read_recent_event_logs",
        ),
        ("disk_space_or_disk_health_diagnosis", "disk dolu", "disk alani", "disk alanı", "disk sagligi", "disk sağlığı", "disk health", "smart", "depolama sorunu"),
    ),
)


def match_playbook(goal: str) -> DiagnosticPlaybook | None:
    normalized = (goal or "").lower()
    return next((playbook for playbook in PLAYBOOKS if any(marker in normalized for marker in playbook.markers)), None)
