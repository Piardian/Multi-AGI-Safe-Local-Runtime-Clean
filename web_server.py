# -*- coding: utf-8 -*-
"""Local-only Flask server for the Antigravity Web UI MVP."""

from __future__ import annotations

import io
import json
import os
import queue
import secrets
import sys
import threading
import uuid
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, jsonify, request, render_template, session

import config
import bridge
from data_policy import redact_text, redact_value
import task_memory

app = Flask(__name__, template_folder="templates")
app.secret_key = secrets.token_hex(32)
# Ensure cookies are secure/lax and not exposed to scripts
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Global active execution state
ACTIVE_EXECUTION = {
    "status": "idle",             # idle | running | pending_approval | completed | failed | denied
    "task_id": None,
    "approval_id": None,          # Unique nonce for the current pending approval
    "goal": None,
    "plan": None,                 # plan steps from TaskRuntime
    "decisions": None,            # policy decisions
    "response_queue": None,       # queue.Queue for approval feedback
    "diagnostic_report": None,    # diagnostic report if generated
    "output_logs": [],            # stdout captured during loop (redacted & limited)
    "summary": "",                # final report summary
    "tools_used": [],
    "approval_payload": None      # Full metadata dict prepared by backend for UI rendering
}

from contextlib import contextmanager

class RedactingOutputStream:
    """File-like stream wrapper to intercept stdout/stderr for log capping & redaction."""
    def __init__(self, original_stream, append_log):
        self.original_stream = original_stream
        self.append_log = append_log
        self.encoding = getattr(original_stream, "encoding", "utf-8")
        self.errors = getattr(original_stream, "errors", "strict")

    def write(self, data):
        if data is None:
            return 0
        if not isinstance(data, str):
            data = str(data)
        self.append_log(data)
        return self.original_stream.write(data)

    def flush(self):
        if hasattr(self.original_stream, "flush"):
            return self.original_stream.flush()

    def isatty(self):
        return getattr(self.original_stream, "isatty", lambda: False)()

    def fileno(self):
        if hasattr(self.original_stream, "fileno"):
            return self.original_stream.fileno()
        raise io.UnsupportedOperation("fileno")

def append_redacted_log_line(line):
    redacted = redact_text(line.rstrip('\r\n'))
    if len(redacted) > 500:
        redacted = redacted[:500] + " ... [line truncated]"
    ACTIVE_EXECUTION["output_logs"].append(redacted)
    if len(ACTIVE_EXECUTION["output_logs"]) > 200:
        ACTIVE_EXECUTION["output_logs"].pop(0)

@contextmanager
def capture_execution_output():
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    
    stdout_buffer = []
    stderr_buffer = []
    
    def append_stdout(text):
        stdout_buffer.append(text)
        joined = "".join(stdout_buffer)
        if "\n" in joined:
            lines = joined.split("\n")
            for line in lines[:-1]:
                append_redacted_log_line(line)
            stdout_buffer.clear()
            if lines[-1]:
                stdout_buffer.append(lines[-1])

    def append_stderr(text):
        stderr_buffer.append(text)
        joined = "".join(stderr_buffer)
        if "\n" in joined:
            lines = joined.split("\n")
            for line in lines[:-1]:
                append_redacted_log_line(line)
            stderr_buffer.clear()
            if lines[-1]:
                stderr_buffer.append(lines[-1])

    try:
        sys.stdout = RedactingOutputStream(original_stdout, append_stdout)
        sys.stderr = RedactingOutputStream(original_stderr, append_stderr)
        yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        # Flush remaining buffers
        if stdout_buffer:
            append_redacted_log_line("".join(stdout_buffer))
        if stderr_buffer:
            append_redacted_log_line("".join(stderr_buffer))

@app.before_request
def enforce_same_origin_and_csrf():
    """Enforce Same-Origin and CSRF checks on all mutable state endpoints (all POST requests)."""
    if request.method == "POST":
        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")

        # Validate Origin header if present
        if origin and origin not in {"http://127.0.0.1:8000", "http://localhost:8000"}:
            return jsonify({"error": "Cross-Origin request blocked"}), 403

        # Validate Referer header if present
        if referer:
            parsed = urlparse(referer)
            host = parsed.netloc
            if host not in {"127.0.0.1:8000", "localhost:8000"}:
                return jsonify({"error": "Cross-Origin referer blocked"}), 403

        # Enforce CSRF token match for ALL POST endpoints
        if not validate_csrf():
            return jsonify({"error": "CSRF validation failed"}), 403

def validate_csrf():
    """Validates presence and match of the CSRF token from headers or JSON body."""
    token_in_session = session.get('csrf_token')
    token_in_request = request.headers.get("X-CSRF-Token") or (request.is_json and request.json.get("csrf_token"))
    if not token_in_session or not token_in_request or token_in_request != token_in_session:
        return False
    return True

def ui_approval_callback(plan, decisions) -> bool:
    """Invoked by the TaskRuntime during execution when an action requires approval."""
    approval_id = uuid.uuid4().hex
    
    # Extract the first call requiring approval
    call_to_approve = None
    decision_to_approve = None
    for call, dec in zip(plan.calls, decisions):
        if dec.requires_approval:
            call_to_approve = call
            decision_to_approve = dec
            break

    if not call_to_approve and plan.calls:
        call_to_approve = plan.calls[0]
        decision_to_approve = decisions[0]

    explanation = "Bu işlem için onay gerekiyor."
    effects = "Belirtilen işlem gerçekleştirilecek."
    source = "-"
    target = "-"
    app_name = "-"
    warnings_list = []

    if call_to_approve:
        tool_details = bridge.TOOL_APPROVAL_DETAILS.get(call_to_approve.tool)
        if not tool_details and call_to_approve.tool in {
            "get_system_info", "read_recent_event_logs", "read_reliability_history", "get_last_boot_reason",
            "list_recent_crashes", "check_disk_health_readonly", "list_driver_errors",
            "list_windows_update_history", "list_startup_apps", "list_running_processes_summary"
        }:
            tool_details = {
                "description": "Bu işlem Windows sistem günlüklerini ve sistem durumunu analiz edecek.",
                "effect": "Sistem bilgileri okunacak. Bilgisayarda hiçbir dosya veya ayar değiştirilmeyecek.",
                "if_approved": "Sistem günlükleri ve tanı bilgileri okunacak.",
                "if_denied": "Hiçbir bilgi okunmayacak."
            }
        if tool_details:
            explanation = tool_details.get("description", explanation)
            effects = tool_details.get("effect", effects)

        # Build application registry launch & remediation payloads
        if call_to_approve.tool == "launch_application_resolved" or call_to_approve.tool in {
            "close_application_process", "restart_application_resolved", "archive_application_logs",
            "backup_application_config", "clear_safe_application_cache"
        }:
            import application_registry
            raw_app_name = call_to_approve.payload.get("app") or call_to_approve.payload.get("application") or call_to_approve.payload.get("application_name") or ""
            app_data = application_registry.match_application(raw_app_name)
            app_name = app_data.get("display_name", raw_app_name) if app_data else raw_app_name

            if call_to_approve.tool == "launch_application_resolved":
                l_type = app_data.get("launch_type", "unknown") if app_data else "unknown"
                src_val = app_data.get("source", "unknown") if app_data else "unknown"
                source_map = {
                    "steam": "Steam manifest / registry match",
                    "start_menu": "Start Menu shortcut / registry match",
                    "desktop": "Desktop shortcut / registry match",
                    "alias": "User defined alias / registry match",
                    "metadata": "Program Files metadata / registry match"
                }
                source = source_map.get(src_val, f"{src_val} / registry match")
                target = app_data.get("launch_target", "") if app_data else ""
            else:
                pids = []
                if app_data:
                    try:
                        from tools.app_launcher_tools import find_running_pids
                        pids = find_running_pids(app_data)
                    except Exception:
                        pass
                pids_str = ", ".join(pids) if pids else "Bulunamadı (Çalışmıyor)"
                source = f"Process check (PIDs: {pids_str})"

                if call_to_approve.tool in {"close_application_process", "restart_application_resolved"}:
                    warnings_list.append("Bu işlem kaydedilmemiş verilerin kaybına neden olabilir!")
                elif call_to_approve.tool == "backup_application_config":
                    config_path = app_data.get("config_path", "") if app_data else ""
                    from tools.app_launcher_tools import check_file_contains_secrets
                    has_secret = False
                    if config_path:
                        try:
                            has_secret = check_file_contains_secrets(config_path)
                        except Exception:
                            pass
                    display_path = config_path if config_path else "-"
                    if has_secret:
                        display_path = "<REDACTED>"
                        warnings_list.append("Bu dosya gizli değerler içerebilir, yalnızca yerel backup klasörüne kopyalanacak")
                    target = display_path
                elif call_to_approve.tool == "archive_application_logs":
                    target = app_data.get("log_path", "") if app_data else "-"
                elif call_to_approve.tool == "clear_safe_application_cache":
                    target = app_data.get("cache_path", "") if app_data else "-"

        elif call_to_approve.tool in {"copy_file", "move_file", "safe_delete_file"}:
            if call_to_approve.tool == "safe_delete_file":
                src_path = call_to_approve.payload.get("path") or ""
                src_formatted = bridge.format_path_friendly(src_path)
                filename = Path(src_path).name if src_path else "file"
                dst_formatted = f"logs/safe_trash / {filename}"
            else:
                src_path = call_to_approve.payload.get("src") or call_to_approve.payload.get("source") or ""
                dst_path = call_to_approve.payload.get("dst") or call_to_approve.payload.get("destination") or call_to_approve.payload.get("target") or ""
                src_formatted = bridge.format_path_friendly(src_path)
                dst_formatted = bridge.format_path_friendly(dst_path)
            source = src_formatted
            target = dst_formatted
        else:
            t_val = call_to_approve.payload.get("target") or call_to_approve.payload.get("path") or call_to_approve.payload.get("app") or call_to_approve.payload.get("query") or ""
            redacted_target = redact_text(str(t_val))
            if call_to_approve.tool == "create_directory":
                loc_str = "Masaüstü" if call_to_approve.payload.get("location") == "desktop" else "Proje Klasörü"
                redacted_target = f"{loc_str} / {redacted_target}"
            target = redacted_target

    risk_level = decision_to_approve.risk if decision_to_approve else "low"
    diff_preview = decision_to_approve.preview.get("diff", "") if decision_to_approve else ""

    payload = {
        "user_goal": redact_text(plan.goal),
        "technical_tool": call_to_approve.tool if call_to_approve else "unknown",
        "explanation": explanation,
        "source": source,
        "target": target,
        "app": app_name,
        "risk": risk_level,
        "warnings": " | ".join(warnings_list) if warnings_list else "",
        "effects": effects,
        "diff_preview": diff_preview
    }

    ACTIVE_EXECUTION.update({
        "status": "pending_approval",
        "task_id": plan.task_id,
        "approval_id": approval_id,
        "plan": [{"tool": call.tool, "payload": call.payload} for call in plan.calls],
        "decisions": [dec.__dict__ for dec in decisions],
        "approval_payload": payload,
        "response_queue": queue.Queue()
    })

    # Block background thread waiting for user input
    approved = ACTIVE_EXECUTION["response_queue"].get()

    # Stale/Reuse invalidation: immediately clear values
    ACTIVE_EXECUTION["approval_id"] = None
    ACTIVE_EXECUTION["response_queue"] = None
    ACTIVE_EXECUTION["approval_payload"] = None

    return approved

def ui_diagnostic_callback(report):
    ACTIVE_EXECUTION["diagnostic_report"] = report

def run_execution_thread(goal):
    try:
        from tools.registry import load_default_tools
        load_default_tools()

        # Run orchestrator autonomous loop with context output stream capture
        with capture_execution_output():
            bridge.run_bridge_autonomous_loop(
                goal,
                plan_only=False,
                approval_callback=ui_approval_callback,
                diagnostic_report_callback=ui_diagnostic_callback
            )

        if ACTIVE_EXECUTION["status"] == "running":
            ACTIVE_EXECUTION["status"] = "completed"
    except Exception as exc:
        ACTIVE_EXECUTION["status"] = "failed"
        ACTIVE_EXECUTION["summary"] = f"Execution failed: {exc}"
    finally:
        ACTIVE_EXECUTION["tools_used"] = getattr(config, "LAST_RUN_TOOLS", [])

@app.route("/")
def index():
    """Serves the single-page application."""
    return render_template("index.html")

@app.route("/api/session", methods=["GET"])
def api_session():
    """Generates and returns a CSRF token for the session."""
    # Ensure this GET endpoint is read-only / has no execution side effects
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return jsonify({"csrf_token": session['csrf_token']})

@app.route("/api/route", methods=["POST"])
def api_route():
    """Classifies the input goal and returns category and risk. Same-origin checked."""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    goal = request.json.get("goal")
    if not goal:
        return jsonify({"error": "Goal is required"}), 400

    try:
        res = bridge.resolve_goal_route(goal, preview_only=True)
        return jsonify({
            "category": res["category"],
            "display_category": bridge.translate_category_for_display(res["category"]),
            "risk_level": res["risk"],
            "direct_mapping": res["direct_map"],
            "target_tools": res["tools"],
            "reason": res["reason"]
        })
    except Exception as exc:
        return jsonify({"error": f"Route classification failed: {exc}"}), 500

@app.route("/api/plan", methods=["POST"])
def api_plan():
    """Simulates goal with plan_only=True. Same-origin checked. Side-effect free."""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    goal = request.json.get("goal")
    if not goal:
        return jsonify({"error": "Goal is required"}), 400

    # Reset config tracking variables
    config.LAST_RUN_TOOLS = []
    config.LAST_RUN_APPROVAL_REQUIRED = False
    
    try:
        from tools.registry import load_default_tools
        load_default_tools()

        res = bridge.resolve_goal_route(goal, preview_only=True)
        route_data = {
            "category": res["category"],
            "display_category": bridge.translate_category_for_display(res["category"]),
            "risk": res["risk"],
            "direct_map": res["direct_map"],
            "tools": res["tools"],
            "reason": res["reason"]
        }

        # Run bridge loop plan-only inside a stdout redirect sandbox
        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f):
            bridge.run_bridge_autonomous_loop(goal, plan_only=True)

        plan_preview = [{"tool": t} for t in config.LAST_RUN_TOOLS]
        return jsonify({
            "route": route_data,
            "plan": plan_preview,
            "risk": {
                "approval_required": config.LAST_RUN_APPROVAL_REQUIRED,
                "risk_level": res["risk"]
            }
        })
    except Exception as exc:
        return jsonify({"error": f"Plan simulation failed: {exc}"}), 500

@app.route("/api/run", methods=["POST"])
def api_run():
    """Triggers normal orchestrator execution in a background thread."""
    if not validate_csrf():
        return jsonify({"error": "CSRF validation failed"}), 403

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    goal = request.json.get("goal")
    if not goal:
        return jsonify({"error": "Goal is required"}), 400

    # Concurrency check
    if ACTIVE_EXECUTION["status"] in {"running", "pending_approval"}:
        return jsonify({"error": "A task is already active", "status": ACTIVE_EXECUTION["status"]}), 409

    ACTIVE_EXECUTION.update({
        "status": "running",
        "task_id": None,
        "approval_id": None,
        "goal": goal,
        "plan": None,
        "decisions": None,
        "response_queue": None,
        "diagnostic_report": None,
        "output_logs": [],
        "summary": "",
        "tools_used": [],
        "approval_payload": None
    })

    t = threading.Thread(target=run_execution_thread, args=(goal,))
    t.daemon = True
    t.start()

    return jsonify({"message": "Task started", "status": "running"})

@app.route("/api/status", methods=["GET"])
def api_status():
    """Returns the current state of execution. Read-only and side-effect free."""
    status_data = {
        "status": ACTIVE_EXECUTION["status"],
        "task_id": ACTIVE_EXECUTION["task_id"],
        "approval_id": ACTIVE_EXECUTION["approval_id"],
        "goal": redact_text(ACTIVE_EXECUTION["goal"]) if ACTIVE_EXECUTION["goal"] else None,
        "plan": ACTIVE_EXECUTION["plan"],
        "decisions": ACTIVE_EXECUTION["decisions"],
        "diagnostic_report": redact_value(ACTIVE_EXECUTION["diagnostic_report"]) if ACTIVE_EXECUTION["diagnostic_report"] else None,
        "output_logs": ACTIVE_EXECUTION["output_logs"],
        "summary": redact_text(ACTIVE_EXECUTION["summary"]) if ACTIVE_EXECUTION["summary"] else "",
        "tools_used": ACTIVE_EXECUTION["tools_used"],
        "approval_payload": ACTIVE_EXECUTION["approval_payload"]
    }
    return jsonify(status_data)

@app.route("/api/approve", methods=["POST"])
def api_approve():
    """Approves the currently pending action."""
    if not validate_csrf():
        return jsonify({"error": "CSRF validation failed"}), 403

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    task_id = request.json.get("task_id")
    approval_id = request.json.get("approval_id")

    if not task_id or not approval_id:
        return jsonify({"error": "task_id and approval_id are required"}), 400

    if ACTIVE_EXECUTION["status"] != "pending_approval":
        return jsonify({"error": "No pending approval action"}), 400

    if ACTIVE_EXECUTION["task_id"] != task_id:
        return jsonify({"error": "task_id mismatch"}), 400

    if ACTIVE_EXECUTION["approval_id"] != approval_id:
        return jsonify({"error": "stale or invalid approval_id"}), 400

    ACTIVE_EXECUTION["status"] = "running"
    ACTIVE_EXECUTION["response_queue"].put(True)

    return jsonify({"message": "Action approved", "status": "running"})

@app.route("/api/deny", methods=["POST"])
def api_deny():
    """Denies the currently pending action."""
    if not validate_csrf():
        return jsonify({"error": "CSRF validation failed"}), 403

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    task_id = request.json.get("task_id")
    approval_id = request.json.get("approval_id")

    if not task_id or not approval_id:
        return jsonify({"error": "task_id and approval_id are required"}), 400

    if ACTIVE_EXECUTION["status"] != "pending_approval":
        return jsonify({"error": "No pending approval action"}), 400

    if ACTIVE_EXECUTION["task_id"] != task_id:
        return jsonify({"error": "task_id mismatch"}), 400

    if ACTIVE_EXECUTION["approval_id"] != approval_id:
        return jsonify({"error": "stale or invalid approval_id"}), 400

    ACTIVE_EXECUTION["status"] = "denied"
    ACTIVE_EXECUTION["response_queue"].put(False)

    return jsonify({"message": "Action denied", "status": "denied"})

@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    """Cancels the currently pending action."""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    task_id = request.json.get("task_id")
    approval_id = request.json.get("approval_id")

    if not task_id or not approval_id:
        return jsonify({"error": "task_id and approval_id are required"}), 400

    if ACTIVE_EXECUTION["status"] != "pending_approval":
        return jsonify({"error": "No pending approval action"}), 400

    if ACTIVE_EXECUTION["task_id"] != task_id:
        return jsonify({"error": "task_id mismatch"}), 400

    if ACTIVE_EXECUTION["approval_id"] != approval_id:
        return jsonify({"error": "stale or invalid approval_id"}), 400

    ACTIVE_EXECUTION["status"] = "denied"
    ACTIVE_EXECUTION["response_queue"].put(False)

    return jsonify({"message": "Action cancelled", "status": "denied"})

@app.route("/api/tools", methods=["GET"])
def api_tools():
    """Returns the categorized tool catalog. Read-only and side-effect free."""
    from tools.registry import registered_tools
    reg_tools = set(registered_tools())
    if "run_command" in reg_tools:
        reg_tools.discard("run_command")

    catalog = {
        "Local Computer": [
            {"name": "open_browser", "description": "Varsayılan tarayıcıyı açar.", "risk": "low"},
            {"name": "create_directory", "description": "Belirtilen konumda yeni klasör oluşturur.", "risk": "low/medium"},
            {"name": "open_application", "description": "Güvenli listedeki uygulamayı çalıştırır.", "risk": "medium"}
        ],
        "File Operations": [
            {"name": "open_folder", "description": "Belirtilen klasörü Windows Explorer ile açar.", "risk": "low/medium"},
            {"name": "copy_file", "description": "Dosyayı başka bir konuma kopyalar.", "risk": "medium"},
            {"name": "move_file", "description": "Dosyayı başka bir konuma taşır.", "risk": "high"},
            {"name": "safe_delete_file", "description": "Dosyayı güvenli çöpe taşır.", "risk": "high"},
            {"name": "search_files", "description": "Uzantıya veya ada göre arama yapar.", "risk": "low"},
            {"name": "get_file_info", "description": "Dosyanın boyut, tarih gibi bilgilerini döner.", "risk": "low"}
        ],
        "Workspace": [
            {"name": "list_workspace_files", "description": "Workspace dosyalarını listeler.", "risk": "low"},
            {"name": "read_file_limited", "description": "Dosya içeriğini görüntüler.", "risk": "low"},
            {"name": "write_file_with_diff", "description": "Dosya içeriğini günceller veya oluşturur.", "risk": "high"},
            {"name": "validate_python_syntax_sandboxed", "description": "Python syntax doğrulaması yapar.", "risk": "low"}
        ],
        "Research": [
            {"name": "web_search_public", "description": "Halka açık kaynaklarda arama yapar.", "risk": "low"}
        ],
        "Applications": [
            {"name": "discover_applications", "description": "Yüklü uygulamaları listeler.", "risk": "low"},
            {"name": "launch_application_resolved", "description": "Registry tarafından onaylanmış uygulamayı başlatır.", "risk": "high"}
        ],
        "Diagnostics": [
            {"name": "application_diagnostics", "description": "Uygulama sorun giderme analizi yapar.", "risk": "low"},
            {"name": "list_running_processes_summary", "description": "Çalışan işlemleri listeler.", "risk": "low"},
            {"name": "list_recent_crashes", "description": "Sistem çökme loglarını okur.", "risk": "low"},
            {"name": "read_reliability_history", "description": "Reliability history okuması yapar.", "risk": "low"},
            {"name": "read_recent_event_logs", "description": "Event loglarını inceler.", "risk": "low"},
            {"name": "get_system_info", "description": "Sistem donanım ve OS bilgilerini alır.", "risk": "low"},
            {"name": "get_last_boot_reason", "description": "Son başlatılma sebebini okur.", "risk": "low"},
            {"name": "list_driver_errors", "description": "Sürücü hatalarını raporlar.", "risk": "low"},
            {"name": "check_disk_health_readonly", "description": "Disk sağlığını okur.", "risk": "low"}
        ],
        "Remediation": [
            {"name": "close_application_process", "description": "Çalışan tüm prosesleri sonlandırır.", "risk": "high"},
            {"name": "restart_application_resolved", "description": "Uygulamayı kapatıp yeniden başlatır.", "risk": "high"},
            {"name": "archive_application_logs", "description": "Log dosyalarını yerel yedeğe arşivler.", "risk": "medium"},
            {"name": "backup_application_config", "description": "Konfigürasyon ayarlarını yedekler.", "risk": "medium"},
            {"name": "clear_safe_application_cache", "description": "Cache dosyalarını yedekleyerek temizler.", "risk": "high"}
        ]
    }

    # Filter catalog dynamically based on registered_tools
    filtered_catalog = {}
    for cat, tools_list in catalog.items():
        matched = [t for t in tools_list if t["name"] in reg_tools]
        if matched:
            filtered_catalog[cat] = matched

    return jsonify(filtered_catalog)

@app.route("/api/memory", methods=["GET"])
def api_memory():
    """Returns redacted task history. Read-only and side-effect free."""
    runs = []
    if os.path.exists(task_memory.MEMORY_FILE):
        with open(task_memory.MEMORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        run = json.loads(line)
                        runs.append(redact_value(run))
                    except Exception:
                        pass
    runs.reverse()
    # Limit returned history and ensure redaction is completed
    return jsonify(runs[:100])

if __name__ == "__main__":
    # Local-only server binding exclusively to 127.0.0.1 on port 8000
    assert hasattr(sys.stdout, "write")
    assert hasattr(sys.stderr, "write")
    app.run(
        host="127.0.0.1",
        port=8000,
        debug=False,
        use_reloader=False,
        threaded=True
    )
