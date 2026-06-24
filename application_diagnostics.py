# -*- coding: utf-8 -*-
"""Read-only Application Diagnostics and Troubleshooting for Sprint 11."""

from __future__ import annotations

import csv
import os
import re
import subprocess
from io import StringIO
from pathlib import Path
import config
from data_policy import redact_text

# Global mock diagnostic data for testing
MOCK_DIAGNOSTICS = {}
IS_MOCK_MODE = False


def run_system_binary_safe(binary: str, args: list[str]) -> tuple[bool, str, str]:
    """Execute a system binary from System32 safely with shell=False."""
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = system_root / "System32" / binary
    if not executable.is_file():
        return False, "", f"Binary unavailable: {binary}"
    try:
        completed = subprocess.run(
            [str(executable), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.DIAGNOSTIC_TIMEOUT_SECONDS,
            shell=False,
            cwd=config.PROJECT_ROOT,
            env={
                "SystemRoot": str(system_root),
                "WINDIR": str(system_root),
                "PATH": str(system_root / "System32")
            },
        )
        if completed.returncode != 0:
            return False, "", (completed.stderr or completed.stdout).strip()[:1000]
        return True, completed.stdout, ""
    except Exception as exc:
        return False, "", str(exc)


def list_running_processes() -> list[dict]:
    """Retrieve running processes via tasklist.exe."""
    if IS_MOCK_MODE and "processes" in MOCK_DIAGNOSTICS:
        return MOCK_DIAGNOSTICS["processes"]
        
    if os.name != "nt":
        return []
        
    ok, raw, err = run_system_binary_safe("tasklist.exe", ["/FO", "CSV", "/NH"])
    if not ok:
        return []
        
    processes = []
    for row in csv.reader(StringIO(raw)):
        if len(row) < 5:
            continue
        processes.append({
            "image_name": row[0],
            "pid": row[1],
            "session": row[2],
            "memory": row[4]
        })
    return processes


def read_crash_events(app_name: str, limit: int = 5) -> list[str]:
    """Retrieve crash event logs using wevtutil.exe."""
    if IS_MOCK_MODE and "crashes" in MOCK_DIAGNOSTICS:
        return MOCK_DIAGNOSTICS["crashes"]
        
    if os.name != "nt":
        return []
        
    # Query Application channel for crashes (Event IDs 1000, 1001, 1002, 1026)
    # Filter by application name if possible, or query last N events and search
    query = "*[System[(EventID=1000 or EventID=1001 or EventID=1002 or EventID=1026)]]"
    ok, raw, err = run_system_binary_safe(
        "wevtutil.exe",
        ["qe", "Application", "/rd:true", f"/c:{limit}", "/f:RenderedXml", f"/q:{query}"]
    )
    if not ok:
        return []
        
    events = []
    parts = re.split(r"(?=Event\[\d+\]:|<Event(?:\s|>))", raw or "")
    for part in parts:
        part_strip = part.strip()
        if not part_strip:
            continue
        # Check if the application name matches the crash details
        if app_name.lower() in part_strip.lower():
            events.append(redact_text(part_strip[:6000]))
    return events


def diagnose_application(app_name: str) -> dict:
    """Diagnose an application status based on processes and crash logs.

    Strictly read-only diagnostics.
    """
    if IS_MOCK_MODE:
        mock_data = MOCK_DIAGNOSTICS.get(app_name.lower())
        if mock_data:
            config.LAST_RUN_APPLICATION_NAME = app_name
            config.LAST_RUN_APPLICATION_ACTION_TYPE = "diagnostics"
            config.LAST_RUN_DIAGNOSTIC_STATUS = mock_data.get("status", "unknown")
            config.LAST_RUN_EVIDENCE_COUNT = len(mock_data.get("evidence", []))
            config.LAST_RUN_ACTIONS_EXECUTED_COUNT = len(mock_data.get("actions_executed", []))
            return mock_data
            
    # Default return structure
    report = {
        "application": app_name,
        "status": "unknown",
        "evidence": [],
        "possible_causes": [],
        "recommended_next_steps": [],
        "safe_actions_available": [],
        "actions_executed": []
    }

    # Find matching app to inspect registered paths
    import application_registry
    app_data = application_registry.match_application(app_name)
    
    app_base = app_name.lower()
    app_exe = app_base + ".exe"
    if app_data and app_data.get("launch_target"):
        target = app_data["launch_target"]
        if target.lower().endswith(".exe"):
            import os
            app_exe = os.path.basename(target).lower()
            app_base = os.path.splitext(app_exe)[0]

    # Reset safe actions list first
    report["safe_actions_available"] = []
    
    # 1. Process Check
    processes = list_running_processes()
    running_pids = []
    for p in processes:
        img = p["image_name"].lower()
        if img == app_exe or app_base in img:
            running_pids.append(p["pid"])
            
    # 2. Crash Logs Check
    crashes = read_crash_events(app_base, limit=10)
    
    # Determine status
    if running_pids:
        report["status"] = "running"
        report["evidence"].append(f"Application process is running with PID(s): {', '.join(running_pids)}.")
        report["possible_causes"].append("Application is currently running. If it is unresponsive, it might be hanging.")
        report["recommended_next_steps"].append("Verify application UI responsiveness manually.")
        report["safe_actions_available"].append("close_application_process")
        report["safe_actions_available"].append("restart_application_resolved")
    else:
        report["status"] = "not_running"
        report["evidence"].append("Application process was not found in the running process list.")
        
        if crashes:
            report["status"] = "crashed_recently"
            report["evidence"].append(f"Detected {len(crashes)} recent crash event(s) matching '{app_name}' in Windows Event Logs.")
            report["possible_causes"].append("The application encountered a critical exception and terminated unexpectedly.")
            report["recommended_next_steps"].append("Check application event logs or reliability history for specific error codes.")
            report["recommended_next_steps"].append("Launch the application again to check if the error is persistent.")
        else:
            report["possible_causes"].append("The application is not started.")
            report["recommended_next_steps"].append("Launch the application via launch_application_resolved.")
            
        report["safe_actions_available"].append("launch_application_resolved")

    # Add log, config, and cache options if defined in registry
    if app_data:
        if app_data.get("log_path"):
            report["safe_actions_available"].append("archive_application_logs")
        if app_data.get("config_path"):
            report["safe_actions_available"].append("backup_application_config")
        if app_data.get("cache_path"):
            report["safe_actions_available"].append("clear_safe_application_cache")

    # Finalize safe actions list
    # Troubleshooting action list executed must be empty
    report["actions_executed"] = []
    
    # Update config memory tracking variables
    config.LAST_RUN_APPLICATION_NAME = app_name
    config.LAST_RUN_APPLICATION_ACTION_TYPE = "diagnostics"
    config.LAST_RUN_DIAGNOSTIC_STATUS = report["status"]
    config.LAST_RUN_EVIDENCE_COUNT = len(report["evidence"])
    config.LAST_RUN_ACTIONS_EXECUTED_COUNT = 0
    config.LAST_RUN_REGISTRY_MATCH_CONFIDENCE = 0.0
    config.LAST_RUN_REGISTRY_VERIFIED = False
    config.LAST_RUN_LAUNCH_TYPE = ""

    return report
