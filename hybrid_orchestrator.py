# -*- coding: utf-8 -*-
"""Cost-aware hybrid orchestration for local agent tasks.

Flow:
User prompt -> planner -> local action drafter -> Python validator
-> critic -> optional execution/report.
"""

from __future__ import annotations

import difflib
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

import config
import security
from agents import coder_chat, critic_chat, planner_chat
from data_policy import protect_workspace_context


# This module drafts plans only.  Execution is delegated exclusively to
# TaskRuntime, whose short allowlist intentionally excludes generic commands,
# browser control and system mutation.
ALLOWED_TOOLS = {
    "list_workspace_files",
    "read_file_limited",
    "write_file_with_diff",
    "validate_python_syntax_sandboxed",
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
    "complete",
}
MAX_FILE_CHARS = 6000


@dataclass
class ActionResult:
    tool: str
    ok: bool
    message: str
    path: str = ""


@dataclass
class HybridReport:
    goal: str
    plan: dict
    agent_plan: dict = field(default_factory=dict)
    draft_actions: list[dict] = field(default_factory=list)
    critic: dict = field(default_factory=dict)
    results: list[ActionResult] = field(default_factory=list)


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = text.replace("```json", "").replace("```", "").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(cleaned[index:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("Model gecerli JSON dondurmedi.")


def workspace_files() -> list[str]:
    root = Path(config.PROJECT_ROOT)
    ignored = {
        "logs",
        "backups",
        "__pycache__",
        ".git",
        ".env",
        ".venv",
        ".browser-profile",
        "node_modules",
    }
    files: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts):
            files.append(path.relative_to(root).as_posix())
    return sorted(files)


PLACEHOLDER_PATH_PATTERNS = [
    r"relative/path/to",
    r"path/to/",
    r"your[_-]?file",
    r"example\.",
    r"sample\.",
    r"placeholder",
    r"dosya_listesi\.txt",
]

RISKY_PATH_PATTERNS = [
    r"(^|/|\\)\.\.($|/|\\)",
    r"^[a-zA-Z]:",
    r"^~",
    r"appdata",
    r"windows[/\\]system32",
    r"program files",
    r"users[/\\][^/\\]+[/\\]\.ssh",
    r"(^|/|\\)\.env($|/|\\)",
]


def is_placeholder_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/").strip().lower()
    return any(re.search(pattern, normalized) for pattern in PLACEHOLDER_PATH_PATTERNS)


def is_risky_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/").strip().lower()
    return any(re.search(pattern, normalized) for pattern in RISKY_PATH_PATTERNS)


def normalize_workspace_path(path: str) -> str:
    normalized = (path or "").replace("\\", "/").strip().strip('"').strip("'")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def validate_workspace_read_path(path: str, workspace_set: set[str]) -> tuple[bool, str, str]:
    normalized = normalize_workspace_path(path)
    if not normalized:
        return False, normalized, "Bos dosya yolu."
    if is_placeholder_path(normalized):
        return False, normalized, f"Placeholder dosya yolu reddedildi; dosya workspace_files icinde yok: {normalized}"
    if is_risky_path(normalized):
        return False, normalized, f"Riskli dosya yolu reddedildi: {path}"
    if normalized not in workspace_set:
        return False, normalized, f"Dosya workspace_files icinde yok: {normalized}"
    return True, normalized, ""


def validate_workspace_write_path(path: str) -> tuple[bool, str, str]:
    normalized = normalize_workspace_path(path)
    if not normalized:
        return False, normalized, "Bos dosya yolu."
    if is_placeholder_path(normalized):
        return False, normalized, f"Placeholder dosya yolu reddedildi: {path}"
    if is_risky_path(normalized):
        return False, normalized, f"Riskli dosya yolu reddedildi: {path}"
    try:
        security.validate_path(normalized)
    except Exception as exc:
        return False, normalized, str(exc)
    return True, normalized, ""


def command_has_placeholder_or_risky_path(command: str) -> tuple[bool, str]:
    candidates = re.findall(r"(?P<path>(?:[A-Za-z]:)?[~\\./\\w -]+\\.[A-Za-z0-9]{1,8})", command or "")
    for candidate in candidates:
        candidate = candidate.strip()
        if is_placeholder_path(candidate):
            return True, f"Komutta placeholder path var: {candidate}"
        if is_risky_path(candidate):
            return True, f"Komutta riskli path var: {candidate}"
    return False, ""


def _read_context(paths: list[str]) -> dict[str, str]:
    context: dict[str, str] = {}
    workspace_set = set(workspace_files())
    for path in paths[:8]:
        ok, normalized, reason = validate_workspace_read_path(path, workspace_set)
        if not ok:
            context[normalized or path] = f"[READ_BLOCKED] {reason}"
            continue
        try:
            content = security.safe_read_file(normalized)
        except Exception as exc:
            context[normalized] = f"[READ_ERROR] {exc}"
            continue
        context[normalized] = content[:MAX_FILE_CHARS]
    return protect_workspace_context(context, config.LOCAL_AGENT_PROVIDER)


def build_planner_messages(goal: str, files: list[str]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Sen guclu planlayici modelsin. Kullanici hedefini local agent icin "
                "guvenli ve uygulanabilir plana cevir. Kod yazma. Sadece strict JSON ver. "
                "Tool isimleri: list_workspace_files, read_file_limited, "
                "write_file_with_diff, validate_python_syntax_sandboxed, get_system_info, "
                "read_recent_event_logs, read_reliability_history, get_last_boot_reason, "
                "list_recent_crashes, check_disk_health_readonly, list_driver_errors, "
                "list_windows_update_history, list_startup_apps, list_running_processes_summary, complete. "
                "Riskli islerde requires_approval=true yap."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "workspace_files": files[:80],
                    "hard_rules": [
                        "Sadece workspace_files icindeki dosyalari referans goster.",
                        "workspace_files icinde olmayan dosya yolu uydurma.",
                        "relative/path/to, path/to/file, example.py, your_file.py gibi placeholder path kullanma.",
                        "Gerekli dosya listede yoksa bunu acikca belirt.",
                    ],
                    "schema": {
                        "task_type": "SIMPLE|CODING|LOCAL_AGENT|FILE|PLANNING",
                        "risk": "low|medium|high",
                        "requires_approval": False,
                        "context_files": ["relative/path"],
                        "coder_prompt": "Local agent modeline verilecek kisa action talimati",
                        "validation_commands": ["optional safe commands"],
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


def build_coder_messages(
    goal: str,
    plan: dict,
    context: dict[str, str],
    revision_prompt: str = "",
    previous_actions: list[dict] | None = None,
) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Sen local agent icin action ureten modelsin. Uygulama yetkin yok; "
                "sadece strict JSON dondur. Tool uydurma. JSON disinda aciklama yazma. "
                "Sadece workspace_files icindeki dosyalari referans goster."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "plan": plan,
                    "context": context,
                    "workspace_files": workspace_files()[:120],
                    "required_output_schema": {
                        "summary": "Kisa aciklama",
                        "needed_files": ["workspace icinden gercek dosyalar"],
                        "actions": [
                            {
                                "type": "read_file",
                                "path": "bridge.py",
                                "reason": "CLI giris akisini incelemek icin",
                            }
                        ],
                        "risk_level": "low|medium|high",
                        "requires_user_approval": False,
                    },
                    "allowed_action_types": [
                        "list_workspace_files",
                        "read_file_limited",
                        "write_file_with_diff",
                        "validate_python_syntax_sandboxed",
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
                        "complete",
                    ],
                    "tool_examples": [
                        {"type": "list_workspace_files", "reason": "Workspace kokunu listelemek icin"},
                        {"type": "read_file_limited", "path": "bridge.py", "reason": "Giris akisini incelemek icin"},
                        {"type": "write_file_with_diff", "path": "todo_app/index.html", "content": "...", "reason": "Kullanici acikca degisiklik isterse"},
                        {"type": "validate_python_syntax_sandboxed", "path": "strategy.py", "reason": "Sadece izole syntax dogrulamasi icin"},
                        {"type": "read_recent_event_logs", "limit": 20, "reason": "Son Windows hata kayitlarini salt-okunur incelemek icin"},
                    ],
                    "rules": [
                        "Cevabin tamami tek JSON obje olmali.",
                        "actions[].type kullan; tool veya action anahtari kullanirsan sistem type'a cevirir ama type tercih edilir.",
                        "needed_files sadece workspace_files listesindeki gercek dosyalardan olusmali.",
                        "read_file_limited path sadece workspace_files icindeki dosya olabilir.",
                        "write_file_with_diff sadece kullanici acikca dosya yazma/olusturma izni verdiyse kullanilabilir.",
                        "Browser, scheduler, GUI automation ve generic shell komutlari bu runtime'da yoktur.",
                        "Workspace listelemek gerekiyorsa list_workspace_files kullan.",
                        "Once gerekli dosyalari oku; tahminle kritik dosya yazma.",
                        "relative/path/to, path/to/file, example.py, your_file.py gibi placeholder path kullanma.",
                        "Dosya listede yoksa complete summary icinde 'dosya listede yok' de.",
                        "Klasor olusturmak icin mkdir yazma; write_file parent klasoru otomatik olusturur.",
                        "Windows teshis tool'lari salt-okunurdur; command, script, shell veya arguman alanlari uretme.",
                        "Dosya silme, paket kurma, sistem ayari, hesap, mail, odeme veya gizli bilgi degisimi bu runtime'da yasaktir.",
                        "Kullanici klasor adi verdiyse ayni adi kullan.",
                        "Is bittiyse complete ile kisa ozet ver.",
                    ],
                    "revision_prompt": revision_prompt,
                    "previous_actions": previous_actions or [],
                    "return_schema": {"actions": []},
                },
                ensure_ascii=False,
            ),
        },
    ]


def build_critic_messages(goal: str, plan: dict, actions: list[dict], preview: list[dict]) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Sen guclu critic modelsin. Sadece kisa JSON karar ver. "
                "Onay icin action list hedefe uygun, guvenli ve yeterli olmali."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "planner_plan": plan,
                    "local_model_actions": actions,
                    "python_preview": preview,
                    "return_schema": {
                        "decision": "approve|revise|reject",
                        "reason": "...",
                        "revision_prompt": "Groq Llama 70B modeline verilecek kisa duzeltme talimati",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


def _forbidden_tools_from_goal(goal: str) -> set[str]:
    normalized = (goal or "").lower()
    forbidden: set[str] = set()
    if "dosya yazma" in normalized or "dosya oluşturma" in normalized or "dosya olusturma" in normalized:
        forbidden.add("write_file")
    if "komut çalıştırma" in normalized or "komut calistirma" in normalized:
        forbidden.add("run_command")
    return forbidden


def _write_allowed_from_goal(goal: str) -> bool:
    normalized = (goal or "").lower()
    if "dosya yazma" in normalized or "dosya oluşturma" in normalized or "dosya olusturma" in normalized:
        return False
    allow_markers = [
        "dosya yaz",
        "dosya oluştur",
        "dosya olustur",
        "site yap",
        "web sitesi yap",
        "uygulama yap",
        "kod yaz",
        "ekle",
        "guncelle",
        "güncelle",
        "duzenle",
        "düzenle",
        "duzelt",
        "düzelt",
        "refactor",
    ]
    return any(marker in normalized for marker in allow_markers)


def validate_needed_files(needed_files: list, workspace_set: set[str]) -> tuple[list[str], list[str]]:
    valid: list[str] = []
    errors: list[str] = []
    for raw_path in needed_files or []:
        ok, normalized, reason = validate_workspace_read_path(str(raw_path), workspace_set)
        if ok:
            if normalized not in valid:
                valid.append(normalized)
        else:
            errors.append(reason)
    return valid, errors


def validate_actions(
    actions: list[dict],
    forbidden_tools: set[str] | None = None,
    workspace_file_set: set[str] | None = None,
    allow_write: bool = False,
) -> list[dict]:
    forbidden_tools = forbidden_tools or set()
    workspace_file_set = workspace_file_set or set(workspace_files())
    preview: list[dict] = []
    for index, action in enumerate(actions, start=1):
        tool = action.get("tool")
        item = {"index": index, "tool": tool, "ok": True, "notes": []}
        if tool in forbidden_tools:
            item["ok"] = False
            item["notes"].append(f"Kullanici bu tool'u yasakladi: {tool}")
        if tool not in ALLOWED_TOOLS:
            item["ok"] = False
            item["notes"].append("Bilinmeyen tool.")
        if tool == "read_file":
            ok, normalized, reason = validate_workspace_read_path(action.get("path", ""), workspace_file_set)
            action["path"] = normalized
            if not ok:
                item["ok"] = False
                item["notes"].append(reason)
        if tool == "list_directory":
            raw_path = action.get("path", ".")
            normalized = normalize_workspace_path(raw_path)
            if normalized in {"", "."}:
                action["path"] = "."
            elif is_placeholder_path(normalized) or is_risky_path(normalized):
                item["ok"] = False
                item["notes"].append(f"Riskli veya placeholder klasor yolu reddedildi: {raw_path}")
            else:
                try:
                    security.validate_path(normalized)
                    action["path"] = normalized
                except Exception as exc:
                    item["ok"] = False
                    item["notes"].append(str(exc))
        if tool == "write_file":
            content = action.get("content", "")
            path = action.get("path", "")
            try:
                if not allow_write:
                    raise security.SecurityError("write_file icin kullanici acik izni yok.")
                ok, normalized, reason = validate_workspace_write_path(path)
                action["path"] = normalized
                if not ok:
                    raise security.SecurityError(reason)
                target = Path(security.validate_path(normalized))
                security._validate_extension(target)
                if normalized in security.PROTECTED_FILES:
                    raise security.SecurityError(f"Korunan dosya: {normalized}")
                if target.suffix == ".py":
                    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as temp:
                        temp.write(content)
                        temp_path = temp.name
                    try:
                        py_compile.compile(temp_path, doraise=True)
                    finally:
                        os.unlink(temp_path)
            except Exception as exc:
                item["ok"] = False
                item["notes"].append(f"write_file validation: {exc}")
        if tool == "run_command":
            item["ok"] = False
            item["notes"].append("Generic command execution is disabled; propose a typed capability instead.")
        if tool == "open_application":
            target = str(action.get("target", "")).lower().strip()
            if target not in {"chrome", "browser"}:
                item["ok"] = False
                item["notes"].append(f"Desteklenmeyen uygulama hedefi: {target}")
        if tool == "open_browser":
            target = str(action.get("target", "")).strip()
            if target and is_risky_path(target):
                item["ok"] = False
                item["notes"].append(f"Riskli browser hedefi: {target}")
        if tool == "search_web":
            if not str(action.get("query", "")).strip():
                item["ok"] = False
                item["notes"].append("search_web query bos olamaz.")
        if tool in {"create_alarm", "create_reminder"}:
            if not str(action.get("title", "")).strip() or not str(action.get("datetime", "")).strip():
                item["ok"] = False
                item["notes"].append(f"{tool} title ve datetime ister.")
            if not action.get("requires_approval"):
                item["ok"] = False
                item["notes"].append(f"{tool} kullanici onayi gerektirir.")
        if tool in {"mouse_click", "keyboard_type"} and not action.get("requires_approval"):
            item["ok"] = False
            item["notes"].append(f"{tool} kullanici onayi gerektirir.")
        if tool == "start_static_server":
            try:
                ok, normalized, reason = validate_workspace_write_path(action.get("path", ""))
                action["path"] = normalized
                if not ok:
                    raise security.SecurityError(reason)
                port = int(action.get("port", 8000))
                if port < 1024 or port > 65535:
                    raise security.SecurityError("Port 1024-65535 arasinda olmali.")
            except Exception as exc:
                item["ok"] = False
                item["notes"].append(str(exc))
        preview.append(item)
    return preview


def _diff_for_write(path: str, new_content: str) -> str:
    try:
        old_content = security.safe_read_file(path)
    except Exception:
        old_content = ""
    diff = difflib.unified_diff(
        old_content.splitlines(),
        (new_content or "").splitlines(),
        fromfile=f"{path} (old)",
        tofile=f"{path} (new)",
        lineterm="",
    )
    return "\n".join(list(diff)[:200])


def execute_actions(actions: list[dict]) -> list[ActionResult]:
    raise RuntimeError("Direct action execution is disabled. Submit typed actions to TaskRuntime.")


def _resolve_alarm_datetime(value: str) -> datetime:
    raw = (value or "").strip()
    now = datetime.now()
    if not raw or raw == "needs_user_datetime_confirmation":
        raise ValueError("Alarm zamani net degil.")

    if raw.startswith("next_occurrenceT"):
        time_part = raw.split("T", 1)[1]
        hour, minute, second = [int(part) for part in time_part.split(":")]
        candidate = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if raw.startswith("next_noon_referenceT"):
        time_part = raw.split("T", 1)[1]
        hour, minute, second = [int(part) for part in time_part.split(":")]
        if 1 <= hour <= 11:
            hour += 12
        candidate = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Alarm zamani okunamadi: {raw}") from exc


def create_windows_alarm(title: str, datetime_hint: str) -> tuple[bool, str]:
    return False, "Alarm creation is disabled; it is not an allowlisted TaskRuntime capability."


def start_static_server(path: str, port: int) -> str:
    raise security.SecurityError("Static server startup is disabled; it is not an allowlisted TaskRuntime capability.")


def _normalize_agent_plan(draft: dict, workspace_set: set[str]) -> tuple[dict, list[dict]]:
    needed_files, needed_errors = validate_needed_files(draft.get("needed_files", []), workspace_set)
    actions: list[dict] = []

    for raw_action in draft.get("actions", []) or []:
        if not isinstance(raw_action, dict):
            continue
        action_type = raw_action.get("type") or raw_action.get("tool") or raw_action.get("action")
        if not action_type:
            continue
        action_type = str(action_type).lower().strip()
        action = {
            "tool": action_type,
            "reason": raw_action.get("reason", ""),
            "requires_approval": bool(raw_action.get("requires_approval", raw_action.get("requires_user_approval", False))),
        }
        if "path" in raw_action:
            action["path"] = raw_action.get("path")
        if "target" in raw_action:
            action["target"] = raw_action.get("target")
        if "query" in raw_action:
            action["query"] = raw_action.get("query")
        if "title" in raw_action:
            action["title"] = raw_action.get("title")
        if "datetime" in raw_action:
            action["datetime"] = raw_action.get("datetime")
        if "x" in raw_action:
            action["x"] = raw_action.get("x")
        if "y" in raw_action:
            action["y"] = raw_action.get("y")
        if "text" in raw_action:
            action["text"] = raw_action.get("text")
        if "command" in raw_action:
            action["command"] = raw_action.get("command")
        if "content" in raw_action:
            action["content"] = raw_action.get("content")
        if "port" in raw_action:
            action["port"] = raw_action.get("port")
        if "summary" in raw_action:
            action["summary"] = raw_action.get("summary")
        actions.append(action)

    if not actions and draft.get("summary"):
        actions.append({"tool": "complete", "summary": draft.get("summary", "")})

    agent_plan = {
        "summary": draft.get("summary", ""),
        "needed_files": needed_files,
        "needed_file_errors": needed_errors,
        "risk_level": draft.get("risk_level", "low"),
        "requires_user_approval": bool(draft.get("requires_user_approval", False)),
    }
    return agent_plan, actions


def _draft_agent_plan(
    goal: str,
    plan: dict,
    context: dict[str, str],
    workspace_set: set[str],
    revision_prompt: str = "",
    previous_actions: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    draft = extract_json(coder_chat(build_coder_messages(goal, plan, context, revision_prompt, previous_actions), 0.1))
    return _normalize_agent_plan(draft, workspace_set)


def _remove_forbidden_actions(actions: list[dict], forbidden_tools: set[str]) -> list[dict]:
    if not forbidden_tools:
        return actions

    filtered = [action for action in actions if action.get("tool") not in forbidden_tools]
    if filtered:
        return filtered

    return [
        {
            "tool": "complete",
            "summary": (
                "Kullanici kisitlari nedeniyle yazma/komut aksiyonlari uygulanmadi. "
                "Sadece raporlama ile tamamlandi."
            ),
        }
    ]


def run_hybrid_goal(goal: str, execute: bool = False) -> HybridReport:
    files = workspace_files()
    workspace_set = set(files)
    plan = extract_json(planner_chat(build_planner_messages(goal, files), 0.1))
    context = _read_context(plan.get("context_files", []))
    agent_plan, actions = _draft_agent_plan(goal, plan, context, workspace_set)
    forbidden_tools = _forbidden_tools_from_goal(goal)
    if not execute:
        forbidden_tools = forbidden_tools | {"write_file", "run_command", "start_static_server"}
    actions = _remove_forbidden_actions(actions, forbidden_tools)
    allow_write = _write_allowed_from_goal(goal)

    critic = {}
    for _ in range(2):
        preview = validate_actions(
            actions,
            forbidden_tools=forbidden_tools,
            workspace_file_set=workspace_set,
            allow_write=allow_write,
        )
        if not all(item["ok"] for item in preview):
            revision = json.dumps({"validator_errors": preview}, ensure_ascii=False)
            agent_plan, actions = _draft_agent_plan(goal, plan, context, workspace_set, revision, actions)
            actions = _remove_forbidden_actions(actions, forbidden_tools)
            critic = {"decision": "revise", "reason": "Python validator revizyon istedi.", "revision_prompt": revision}
            continue

        critic = extract_json(critic_chat(build_critic_messages(goal, plan, actions, preview), 0.1))
        if critic.get("decision") == "approve":
            break
        if critic.get("decision") == "revise":
            agent_plan, actions = _draft_agent_plan(
                goal,
                plan,
                context,
                workspace_set,
                critic.get("revision_prompt", critic.get("reason", "")),
                actions,
            )
            actions = _remove_forbidden_actions(actions, forbidden_tools)
            continue
        break

    report = HybridReport(goal=goal, plan=plan, agent_plan=agent_plan, draft_actions=actions, critic=critic)
    if critic.get("decision") != "approve":
        return report

    if execute:
        report.critic = {
            **report.critic,
            "execution": "blocked",
            "reason": "Hybrid orchestrator drafts plans only; TaskRuntime owns execution.",
        }
    return report


def format_report(report: HybridReport) -> str:
    lines = [
        "HIBRIT AKIS RAPORU",
        f"Hedef: {report.goal}",
        f"Plan tipi: {report.plan.get('task_type', '-')}",
        f"Risk: {report.plan.get('risk', '-')}",
        f"Agent ozet: {report.agent_plan.get('summary', '-')}",
        "Needed files:",
    ]
    needed_files = report.agent_plan.get("needed_files") or []
    if needed_files:
        lines.extend(f"- {path}" for path in needed_files)
    else:
        lines.append("- (yok)")

    needed_errors = report.agent_plan.get("needed_file_errors") or []
    if needed_errors:
        lines.append("Dosya liste hatalari:")
        lines.extend(f"- {error}" for error in needed_errors)

    lines.extend([
        f"Critic karari: {report.critic.get('decision', 'validator_reject')}",
        f"Critic gerekcesi: {report.critic.get('reason', '-')}",
        "",
        "Aksiyonlar:",
    ])
    for action in report.draft_actions:
        label = action.get("tool", "?")
        target = action.get("path") or action.get("command") or ""
        lines.append(f"- {label}: {target}")

    if report.results:
        lines.append("")
        lines.append("Sonuclar:")
        for result in report.results:
            status = "OK" if result.ok else "HATA"
            first_line = result.message.splitlines()[0] if result.message else ""
            lines.append(f"- {status} {result.tool}: {first_line}")
    return "\n".join(lines)
