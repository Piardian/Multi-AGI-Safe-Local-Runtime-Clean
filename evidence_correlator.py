# -*- coding: utf-8 -*-
"""Deterministic evidence extraction and correlation for diagnostic playbooks."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter

from diagnostic_report import DiagnosticEvidence, DiagnosticReport


HIGH_EVENT_IDS = {"41", "6008", "1000", "1001", "1026", "219", "7000", "7026"}
MEDIUM_EVENT_IDS = {"1002", "20", "21", "22", "43", "10110", "10111"}


def build_diagnostic_report(scenario: str, runtime_report) -> DiagnosticReport:
    evidence: list[DiagnosticEvidence] = []
    unavailable: list[dict] = []
    tool_results = _tool_results(runtime_report)

    for tool, result in tool_results:
        if not result.ok:
            unavailable.append({"source": tool, "reason": result.message, "details": result.data or {}})
            continue
        evidence.extend(_extract_evidence(tool, result.data or {}))
        unavailable.extend(_unavailable_from_data(tool, result.data or {}))

    timeline = sorted((item for item in evidence if item.timestamp), key=lambda item: item.timestamp, reverse=True)
    severity = _severity_for(scenario, evidence)
    confidence = _confidence(tool_results, unavailable, evidence)
    causes = _possible_causes(scenario, evidence, unavailable)
    next_steps = _next_steps(scenario, evidence, unavailable)
    summary = _summary(scenario, severity, evidence, unavailable)
    return DiagnosticReport(
        summary=summary,
        severity=severity,
        confidence=confidence,
        evidence=evidence[:80],
        timeline=timeline[:80],
        possible_causes=causes,
        recommended_next_steps=next_steps,
        blocked_or_unavailable_sources=unavailable,
        scenario=scenario,
    )


def _tool_results(runtime_report) -> list[tuple[str, object]]:
    pairs = []
    for index, result in enumerate(runtime_report.results):
        tool = "unknown"
        if index < len(runtime_report.decisions):
            tool = str(runtime_report.decisions[index].preview.get("tool", "unknown"))
        pairs.append((tool, result))
    return pairs


def _extract_evidence(tool: str, data: dict) -> list[DiagnosticEvidence]:
    if tool in {"read_recent_event_logs", "list_recent_crashes", "list_driver_errors", "list_windows_update_history"}:
        return _event_evidence(tool, data.get("entries", []))
    if tool == "get_last_boot_reason":
        event_id = str(data.get("event_id", ""))
        details = data.get("details", [])
        timestamp = _event_timestamp(details[0]) if details else ""
        return [DiagnosticEvidence("last_boot_reason", _boot_summary(event_id), timestamp, _event_severity(event_id), {"event_id": event_id})]
    if tool == "check_disk_health_readonly":
        return _disk_evidence(data)
    if tool == "get_system_info":
        memory = data.get("memory", {}) if isinstance(data, dict) else {}
        load = memory.get("load_percent")
        summary = f"System memory load: {load}%" if load is not None else "System information collected."
        severity = "medium" if isinstance(load, int) and load >= 85 else "info"
        return [DiagnosticEvidence("system_info", summary, severity=severity, details={"memory": memory, "cpu_count": data.get("cpu_count")})]
    if tool == "list_running_processes_summary":
        total = int(data.get("total", 0) or 0)
        severity = "medium" if total >= 250 else "info"
        return [DiagnosticEvidence("running_processes", f"Running process count: {total}", severity=severity, details={"total": total})]
    if tool == "list_startup_apps":
        total = int(data.get("total", 0) or 0)
        severity = "medium" if total >= 20 else "info"
        return [DiagnosticEvidence("startup_apps", f"Startup entry count: {total}", severity=severity, details={"total": total})]
    if tool == "read_reliability_history":
        records = data.get("records", [])
        return [DiagnosticEvidence("reliability_history", f"Reliability records read: {len(records)}", severity="info")]
    return []


def _unavailable_from_data(tool: str, data: dict) -> list[dict]:
    if tool == "check_disk_health_readonly" and data.get("physical_status_error"):
        return [{
            "source": "physical_disk_smart_wmi",
            "reason": "Windows physical disk health source is unavailable.",
            "details": {"error": data.get("physical_status_error")},
        }]
    return []


def _event_evidence(source: str, entries: list[str]) -> list[DiagnosticEvidence]:
    evidence = []
    for entry in entries[:40]:
        event_id, provider, timestamp = _parse_event(entry)
        label = f"{source}: event {event_id or 'unknown'}"
        if provider:
            label += f" from {provider}"
        evidence.append(DiagnosticEvidence(source, label, timestamp, _event_severity(event_id), {"event_id": event_id, "provider": provider}))
    return evidence


def _parse_event(raw: str) -> tuple[str, str, str]:
    event_id = ""
    provider = ""
    timestamp = ""
    try:
        root = ET.fromstring(raw)
        event_id = _find_xml(root, "EventID")
        provider = _find_xml_attr(root, "Provider", "Name")
        timestamp = _find_xml_attr(root, "TimeCreated", "SystemTime")
    except Exception:
        match = re.search(r"<EventID[^>]*>(\d+)</EventID>|Event ID:\s*(\d+)", raw or "", re.IGNORECASE)
        event_id = next((group for group in match.groups() if group), "") if match else ""
        provider_match = re.search(r"Provider[^>]*Name=[\"']([^\"']+)", raw or "", re.IGNORECASE)
        provider = provider_match.group(1) if provider_match else ""
        time_match = re.search(r"SystemTime=[\"']([^\"']+)", raw or "", re.IGNORECASE)
        timestamp = time_match.group(1) if time_match else ""
    return event_id, provider, timestamp


def _find_xml(root, name: str) -> str:
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] == name:
            return (item.text or "").strip()
    return ""


def _find_xml_attr(root, name: str, attribute: str) -> str:
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] == name:
            return str(item.attrib.get(attribute, ""))
    return ""


def _event_timestamp(raw: str) -> str:
    return _parse_event(raw)[2]


def _event_severity(event_id: str) -> str:
    if event_id in HIGH_EVENT_IDS:
        return "high"
    if event_id in MEDIUM_EVENT_IDS:
        return "medium"
    return "info"


def _boot_summary(event_id: str) -> str:
    return {
        "41": "Kernel-Power 41 indicates an unexpected restart or power loss.",
        "6008": "Windows recorded an unexpected shutdown.",
        "6006": "Windows recorded a clean Event Log shutdown.",
        "6005": "Windows recorded Event Log service startup.",
    }.get(event_id, "A recent boot-related event was recorded.")


def _disk_evidence(data: dict) -> list[DiagnosticEvidence]:
    evidence = []
    for volume in data.get("logical_volumes", []):
        total = int(volume.get("total_bytes", 0) or 0)
        free = int(volume.get("free_bytes", 0) or 0)
        percent = round((free / total) * 100, 1) if total else 0.0
        severity = "high" if percent < 10 else "medium" if percent < 20 else "info"
        evidence.append(DiagnosticEvidence("disk_health", f"{volume.get('drive', '?')} free space: {percent}%", severity=severity, details={"free_percent": percent}))
    statuses = " ".join(data.get("physical_disk_status", [])).lower()
    if statuses:
        severity = "high" if any(token in statuses for token in ("pred fail", "bad", "error")) else "info"
        evidence.append(DiagnosticEvidence("disk_health", "Windows physical-disk status was read.", severity=severity))
    return evidence


def _severity_for(scenario: str, evidence: list[DiagnosticEvidence]) -> str:
    levels = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    highest = max((levels.get(item.severity, 0) for item in evidence), default=0)
    if scenario == "unexpected_restart_diagnosis" and any(item.details.get("event_id") in {"41", "6008"} for item in evidence):
        highest = max(highest, 3)
    return next(level for level, value in levels.items() if value == highest)


def _confidence(tool_results: list[tuple[str, object]], unavailable: list[dict], evidence: list[DiagnosticEvidence]) -> float:
    attempted = max(len(tool_results), 1)
    available = attempted - len(unavailable)
    score = 0.35 + (available / attempted) * 0.45 + min(len(evidence), 12) * 0.015
    return round(max(0.2, min(score, 0.95)), 2)


def _possible_causes(scenario: str, evidence: list[DiagnosticEvidence], unavailable: list[dict]) -> list[str]:
    event_ids = {item.details.get("event_id") for item in evidence}
    causes: list[str] = []
    if {"41", "6008"} & event_ids:
        causes.append("Unexpected power loss, forced reset, or a system-level restart is indicated by the boot evidence.")
    if {"1000", "1001", "1002", "1026"} & event_ids:
        causes.append("Recent application or .NET crash records indicate software instability.")
    if {"219", "7000", "7026", "10110", "10111"} & event_ids:
        causes.append("Driver initialization or driver framework events may be contributing.")
    if any(item.source == "disk_health" and item.severity in {"medium", "high"} for item in evidence):
        causes.append("Low disk space or a Windows-reported storage warning may affect stability or performance.")
    if any(item.source == "running_processes" and item.severity == "medium" for item in evidence):
        causes.append("A high running-process count may contribute to slow responsiveness.")
    if not causes:
        causes.append("The available read-only evidence does not yet identify a single conclusive cause.")
    if unavailable:
        causes.append("Some confidence is limited because one or more read-only sources were unavailable.")
    return causes


def _next_steps(scenario: str, evidence: list[DiagnosticEvidence], unavailable: list[dict]) -> list[str]:
    steps = ["Review the evidence timeline and compare timestamps with the moment the problem was observed."]
    if scenario == "unexpected_restart_diagnosis":
        steps.append("If Kernel-Power 41 repeats, check power delivery, overheating indicators, and any recent driver or Windows Update changes.")
    elif scenario == "blue_screen_or_crash_diagnosis":
        steps.append("Match recent crash records to the affected application or stop-code before changing drivers or software.")
    elif scenario == "slow_pc_diagnosis":
        steps.append("Review startup entries, process count, memory load, and free disk space before making changes.")
    elif scenario == "driver_problem_diagnosis":
        steps.append("Identify the device or provider named in driver events before considering a vendor-supported driver update.")
    elif scenario == "disk_space_or_disk_health_diagnosis":
        steps.append("Preserve free disk space and obtain a vendor-supported disk health check if Windows physical-disk status is unavailable.")
    if unavailable:
        steps.append("Unavailable sources are informational gaps, not confirmed failures; retry after the relevant Windows component is available.")
    return steps


def _summary(scenario: str, severity: str, evidence: list[DiagnosticEvidence], unavailable: list[dict]) -> str:
    high_count = sum(item.severity == "high" for item in evidence)
    message = f"{scenario}: {len(evidence)} evidence items were correlated; overall severity is {severity}."
    if high_count:
        message += f" {high_count} high-severity evidence item(s) were found."
    if unavailable:
        message += f" {len(unavailable)} source(s) were unavailable and are listed separately."
    return message
