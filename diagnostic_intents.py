# -*- coding: utf-8 -*-
"""Deterministic routing for the small read-only Windows diagnostic surface."""

from __future__ import annotations


DIAGNOSTIC_RULES = [
    ("get_system_info", ("sistem bilgisi", "system info", "bilgisayar bilgisi"), "Temel Windows ve donanim bilgisini salt-okunur almak icin."),
    ("read_recent_event_logs", ("event log", "event viewer", "olay gunlugu", "olay günlüğü", "sistem log"), "Son Windows hata/uyari kayitlarini salt-okunur okumak icin."),
    ("read_reliability_history", ("reliability", "guvenilirlik gecmisi", "güvenilirlik geçmişi", "reliability monitor"), "Windows Reliability gecmisini salt-okunur okumak icin."),
    ("get_last_boot_reason", ("son acilis", "son açılış", "boot reason", "restart reason", "yeniden baslama nedeni", "yeniden başlama nedeni"), "Son boot veya restart nedenini salt-okunur incelemek icin."),
    ("list_recent_crashes", ("crash", "cokme", "çökme", "uygulama hatasi", "uygulama hatası"), "Son uygulama cokme kayitlarini salt-okunur incelemek icin."),
    ("check_disk_health_readonly", ("disk sagligi", "disk sağlığı", "smart", "disk health", "depolama sagligi", "depolama sağlığı"), "Disk kapasitesi ve Windows tarafindan bildirilen disk durumunu salt-okunur incelemek icin."),
    ("list_driver_errors", ("driver hatasi", "driver hatası", "surucu hatasi", "sürücü hatası"), "Driver ile ilgili Windows hata kayitlarini salt-okunur incelemek icin."),
    ("list_windows_update_history", ("windows update", "guncelleme gecmisi", "güncelleme geçmişi", "update history"), "Windows Update olay gecmisini salt-okunur incelemek icin."),
    ("list_startup_apps", ("baslangic uygulama", "başlangıç uygulama", "startup app", "startup program"), "Baslangic girdilerini salt-okunur listelemek icin."),
    ("list_running_processes_summary", ("calisan surec", "çalışan süreç", "running process", "process list", "islem listesi", "işlem listesi"), "Calisan surecleri salt-okunur ozetlemek icin."),
]


def build_readonly_diagnostic_actions(goal: str) -> list[dict]:
    normalized = (goal or "").lower()
    actions: list[dict] = []
    if "sistem teshis" in normalized or "sistem teşhis" in normalized or "bilgisayar sorunu" in normalized:
        actions.extend(
            {
                "type": tool,
                "limit": 20,
                "reason": reason,
            }
            for tool, _, reason in DIAGNOSTIC_RULES
        )
        return actions

    for tool, markers, reason in DIAGNOSTIC_RULES:
        if any(marker in normalized for marker in markers):
            action = {"type": tool, "reason": reason}
            if tool not in {"get_system_info", "check_disk_health_readonly"}:
                action["limit"] = 20
            actions.append(action)
    return actions
