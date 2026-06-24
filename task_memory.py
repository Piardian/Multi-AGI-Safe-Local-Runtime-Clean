# -*- coding: utf-8 -*-
"""Persistent task memory logging and querying."""

from __future__ import annotations

import datetime
import json
import os
import re

import config
from data_policy import redact_value

MEMORY_FILE = os.path.join(config.PROJECT_ROOT, "logs", "task_memory.jsonl")


def save_run(task_plan: dict, provider: dict, result: str | dict):
    # Ensure directory exists
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)

    goal = task_plan.get("goal") or ""
    intent = task_plan.get("task_type") or task_plan.get("intent") or ""
    provider_used = provider.get("provider_name") or provider.get("provider_type") or "unknown"

    # Parse status
    status = "success"
    try:
        import web_server
        web_status = web_server.ACTIVE_EXECUTION["status"]
        if web_status == "needs_clarification":
            status = "needs_clarification"
        elif web_status == "pending_approval":
            status = "pending_approval"
        elif web_status == "denied":
            status = "denied"
        elif web_status == "failed":
            status = "failed"
    except Exception:
        pass

    if status == "success":
        if isinstance(result, dict):
            res_status = str(result.get("status") or result.get("graph", {}).get("status") or "").lower()
            if res_status in {"needs_approval", "pending_approval"}:
                status = "pending_approval"
            elif res_status in {"failed", "error", "failed_low_relevance"}:
                status = "failed"
            elif res_status == "needs_clarification":
                status = "needs_clarification"
            elif res_status == "denied":
                status = "denied"
            elif res_status == "success":
                status = "success"
        else:
            text = str(result).lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
            if "needs_approval" in text or "onayi gerekiyor" in text or "pending_approval" in text:
                status = "pending_approval"
            elif "denied" in text or "status=denied" in text:
                status = "denied"
            elif "needs_clarification" in text or "bilgi gerekiyor" in text or ("nereye" in text and "istiyorsun" in text) or ("hangi sehir" in text and "istiyorsun" in text):
                status = "needs_clarification"
            elif "failed" in text or "hata" in text or "basarisiz" in text:
                status = "failed"

    # Parse tools_used
    tools_used = []
    if getattr(config, "LAST_RUN_TOOLS", None):
        tools_used.extend(config.LAST_RUN_TOOLS)
    if not tools_used:
        if isinstance(result, dict):
            tools_used = result.get("task_runtime", {}).get("tools") or []
            if not tools_used and "safe_plan" in result:
                tools_used = [act.get("tool") or act.get("type") for act in result["safe_plan"].get("actions", []) if act.get("tool") or act.get("type")]
            if not tools_used and "graph" in result:
                tools_used = list(set([step.get("tool") for step in result["graph"].get("steps", []) if step.get("tool")]))
        else:
            # Regex or string search for tool names
            all_tools = {
                "list_workspace_files", "read_file_limited", "write_file_with_diff", "validate_python_syntax_sandboxed",
                "get_system_info", "read_recent_event_logs", "read_reliability_history", "get_last_boot_reason",
                "list_recent_crashes", "check_disk_health_readonly", "list_driver_errors",
                "list_windows_update_history", "list_startup_apps", "list_running_processes_summary",
                "web_search_public", "create_directory", "open_browser"
            }
            text = str(result)
            for t in all_tools:
                if t in text:
                    tools_used.append(t)

    # Parse files_created
    files_created = []
    if isinstance(result, dict):
        files_created = result.get("created_files") or []
        if not files_created and "safe_plan" in result:
            files_created = result["safe_plan"].get("created_files") or []
    else:
        matches = re.findall(r"(\S+) written after approved diff", str(result))
        for m in matches:
            files_created.append(m)
        for path in ["restaurant_site/index.html", "todo_app/index.html", "todo_app/style.css", "todo_app/script.js", "portfolio_site/index.html"]:
            if path in str(result) and path not in files_created:
                files_created.append(path)

    # Parse errors
    errors = []
    if isinstance(result, dict):
        errors = result.get("errors") or []
        if "validation" in result and result["validation"].get("issues"):
            errors.extend(result["validation"]["issues"])
    else:
        matches = re.findall(r"- HATA:\s*(.*)", str(result))
        errors.extend(matches)

    # Summary
    summary = ""
    if isinstance(result, dict):
        summary = result.get("summary") or result.get("analysis_report", {}).get("summary") or ""
    if not summary:
        lines = [l.strip() for l in str(result).splitlines() if l.strip()]
        summary = " ".join(lines[:3])

    provider_decision = provider.get("decision_log") or {}
    selected_model = provider.get("selected_model") or "unknown"
    selected_provider = provider.get("provider_name") or provider.get("provider_type") or "unknown"
    estimated_cost = provider.get("estimated_cost") or "unknown"

    fallback_used = False
    if isinstance(result, dict):
        fallback_used = bool(result.get("fallback_used", False))
    else:
        text = str(result).lower()
        if "fallback" in text or "basarisiz; api fallback" in text or "primary provider failed" in text:
            fallback_used = True

    response_time = getattr(config, "LAST_RUN_DURATION", 0.0)

    actions_executed_count = int(getattr(config, "LAST_RUN_ACTIONS_EXECUTED_COUNT", 0))
    if status == "denied":
        actions_executed_count = 0

    # Construct run entry
    # Memory schema documentation for approval fields:
    # - approval_required: Bu işlem normal veya plan-only execution’da onay gerektirirdi.
    # - approval_granted: Bu çalıştırmada kullanıcı gerçekten onay verdi mi?
    # - blocked_by_plan_only: İşlem plan-only nedeniyle çalıştırılmadı mı?
    # - approval_would_be_required: İşlemin onay gerektirip gerektirmediği (true/false).
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "goal": goal,
        "intent": intent,
        "provider_used": provider_used,
        "selected_provider": selected_provider,
        "selected_model": selected_model,
        "estimated_cost": estimated_cost,
        "tools_used": sorted(list(set(tools_used))),
        "status": status,
        "summary": summary,
        "files_created": sorted(list(set(files_created))),
        "errors": sorted(list(set(errors))),
        "provider_decision": provider_decision,
        "fallback_used": fallback_used,
        "response_time_seconds": response_time,
        "approval_required": bool(getattr(config, "LAST_RUN_APPROVAL_REQUIRED", False)),
        "approval_granted": bool(getattr(config, "LAST_RUN_APPROVAL_GRANTED", False)),
        "blocked_by_plan_only": bool(getattr(config, "LAST_RUN_BLOCKED_BY_PLAN_ONLY", False)),
        "approval_would_be_required": bool(getattr(config, "LAST_RUN_APPROVAL_WOULD_BE_REQUIRED", False)),
        "file_operation_type": str(getattr(config, "LAST_RUN_FILE_OPERATION_TYPE", "")),
        "source_redacted": str(getattr(config, "LAST_RUN_SOURCE_REDACTED", "")),
        "target_redacted": str(getattr(config, "LAST_RUN_TARGET_REDACTED", "")),
        "application_name": str(getattr(config, "LAST_RUN_APPLICATION_NAME", "")),
        "application_action_type": str(getattr(config, "LAST_RUN_APPLICATION_ACTION_TYPE", "")),
        "registry_match_confidence": float(getattr(config, "LAST_RUN_REGISTRY_MATCH_CONFIDENCE", 0.0)),
        "registry_verified": bool(getattr(config, "LAST_RUN_REGISTRY_VERIFIED", False)),
        "launch_type": str(getattr(config, "LAST_RUN_LAUNCH_TYPE", "")),
        "diagnostic_status": str(getattr(config, "LAST_RUN_DIAGNOSTIC_STATUS", "")),
        "evidence_count": int(getattr(config, "LAST_RUN_EVIDENCE_COUNT", 0)),
        "actions_executed_count": actions_executed_count,
        "remediation_action_type": str(getattr(config, "LAST_RUN_REMEDIATION_ACTION_TYPE", "")),
        "target_process_names_redacted": str(getattr(config, "LAST_RUN_TARGET_PROCESS_NAMES_REDACTED", "")),
        "target_pids_count": int(getattr(config, "LAST_RUN_TARGET_PIDS_COUNT", 0)),
        "target_paths_redacted": str(getattr(config, "LAST_RUN_TARGET_PATHS_REDACTED", "")),
        "diagnostic_report_linked": bool(getattr(config, "LAST_RUN_DIAGNOSTIC_REPORT_LINKED", False))
    }


    # Redact all sensitive info
    redacted_entry = redact_value(entry)

    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(redacted_entry, ensure_ascii=False) + "\n")


def print_last_runs(limit: int):
    if not os.path.exists(MEMORY_FILE):
        print("\nHenüz kaydedilmiş bir görev bulunmamaktadır.")
        return

    runs = []
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    runs.append(json.loads(line))
                except Exception:
                    pass

    selected = runs[-limit:] if len(runs) > limit else runs
    selected.reverse()

    print(f"\n=== SON {len(selected)} GÖREV KAYDI ===")
    for index, run in enumerate(selected, start=1):
        _print_run_entry(run, index)


def search_runs(query: str):
    if not os.path.exists(MEMORY_FILE):
        print("\nHenüz kaydedilmiş bir görev bulunmamaktadır.")
        return

    query = query.lower()
    matches = []
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    run = json.loads(line)
                    in_goal = query in run.get("goal", "").lower()
                    in_summary = query in run.get("summary", "").lower()
                    in_files = any(query in str(file).lower() for file in run.get("files_created", []))
                    if in_goal or in_summary or in_files:
                        matches.append(run)
                except Exception:
                    pass

    print(f"\n=== ARAMA SONUÇLARI: '{query}' ({len(matches)} eşleşme) ===")
    matches.reverse()
    for index, run in enumerate(matches, start=1):
        _print_run_entry(run, index)


def _print_run_entry(run: dict, index: int):
    print(f"\n[{index}] Zaman: {run.get('timestamp')}")
    print(f"    Hedef: {run.get('goal')}")
    print(f"    İntent: {run.get('intent')} | Sağlayıcı: {run.get('selected_provider') or run.get('provider_used')} | Model: {run.get('selected_model', 'unknown')}")
    print(f"    Durum: {run.get('status')} | Kullanılan Araçlar: {', '.join(run.get('tools_used', []))}")
    print(f"    Süre: {run.get('response_time_seconds', 0.0)}s | Fallback kullanıldı mı?: {run.get('fallback_used', False)}")
    if run.get("files_created"):
        print(f"    Oluşturulan Dosyalar: {', '.join(run.get('files_created', []))}")
    if run.get("errors"):
        print(f"    Hatalar: {', '.join(run.get('errors', []))}")
    print(f"    Özet: {run.get('summary')}")
    print("-" * 50)

