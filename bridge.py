# -*- coding: utf-8 -*-
"""Antigravity autonomous agent bridge.

The bridge has two lanes:
1. Direct answer lane for ordinary questions, lists, explanations and research.
   It prints the answer to the terminal and writes no workspace files.
2. Autonomous build lane for explicit coding/file tasks.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import config
import security
from agents import active_model_info, chat_model, groq_chat, web_query_chat
from browser_model_provider import browser_brain_health_check
from diagnostic_intents import build_readonly_diagnostic_actions
from diagnostic_playbooks import match_playbook
from evidence_correlator import build_diagnostic_report
from local_agent import build_plan_only_report, format_local_agent_report, run_local_agent
from local_model_provider import benchmark as benchmark_local_models
from local_model_provider import health_check as local_model_health_check
from provider_selector import select_provider
from prompt_architect import build_agent_prompt
from result_synthesizer import synthesize_result
from router import classify_message
from task_planner import build_task_plan, build_task_plan_fallback
from task_runtime import RuntimePlan, RuntimeReport, TaskRuntime


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.LOG_DIR, "bridge.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
TURKISH_CITIES_AND_DISTRICTS = {
    # Cities (81)
    "istanbul", "ankara", "izmir", "bursa", "adana", "antalya", "konya", "kayseri", 
    "samsun", "trabzon", "diyarbakir", "eskisehir", "kocaeli", "sakarya", "mersin", 
    "denizli", "malatya", "kahramanmaras", "erzurum", "van", "batman", "elazig", 
    "sivas", "manisa", "balikesir", "aydin", "mugla", "tekirdag", "edirne", "kirklareli", 
    "canakkale", "yalova", "bolu", "duzce", "zonguldak", "rize", "ordu", "giresun", 
    "artvin", "kars", "ardahan", "igdir", "mus", "agri", "bingol", "tunceli", "hakkari", 
    "sirnak", "siirt", "mardin", "sanliurfa", "urfa", "antep", "gaziantep", "kilis", 
    "maras", "hatay", "antakya", "osmaniye", "karaman", "nigde", "nevsehir", "aksaray", 
    "kirikkale", "cankiri", "karabuk", "bartin", "sinop", "kastamonu", "corum", "amasya", 
    "tokat", "yozgat", "kirsehir", "afyon", "usak", "kutahya", "bilecik", "isparta", 
    "burdur", "gumushane", "bayburt", "mus", "agri", "ardahan", "artvin", "kars", 
    "erzincan", "bitlis", "bingol", "tunceli",
    # Districts & regions
    "devrek", "eregli", "bodrum", "cesme", "alanya", "manavgat", "fethiye", "marmaris", 
    "datca", "kusadasi", "didim", "ayvalik", "edremit", "bandirma", "inegol", "gemlik", 
    "tarsus", "silifke", "anamur", "iskenderun", "polatli", "sereflikochisar", "beypazari",
    "gebze", "darica", "corlu", "cerkezkoy", "luleburgaz"
}

def capitalize_turkish(s: str) -> str:
    if not s:
        return ""
    first = s[0]
    if first == "i":
        return "İ" + s[1:]
    if first == "ı":
        return "I" + s[1:]
    return s.capitalize()


def translate_category_for_display(cat: str) -> str:
    mapping = {
        "local_computer_action": "local_agent_task",
        "weather_query": "web_query"
    }
    return mapping.get(cat, cat)


def update_web_server_status(runtime_report) -> None:
    status_to_set = "success"
    if runtime_report.status == "denied":
        status_to_set = "denied"
    elif runtime_report.status == "needs_approval":
        status_to_set = "pending_approval"
    elif runtime_report.status in {"failed", "blocked"}:
        status_to_set = "failed"
    elif runtime_report.status == "partial":
        # Check if any action was successful
        any_success = any(r.ok for r in runtime_report.results)
        status_to_set = "success" if any_success else "failed"
    else:
        # Check if there are any results and if any failed
        if runtime_report.results and not any(r.ok for r in runtime_report.results):
            status_to_set = "failed"
            
    # Check if there's any status hint inside results data (e.g. decoupled tool status_hint)
    for res_item in runtime_report.results:
        if isinstance(res_item.data, dict) and "status_hint" in res_item.data:
            status_to_set = res_item.data["status_hint"]

    try:
        import web_server
        web_server.ACTIVE_EXECUTION["status"] = status_to_set
    except Exception:
        pass


def extract_weather_location(goal: str) -> str | None:
    from router import _normalize
    import re
    text = _normalize(goal)
    text_clean = re.sub(r"[.,\/#!$%\^&\*;:{}=\-_`~()?]", " ", text)
    words = text_clean.split()
    
    # Check exact word matches first
    for word in words:
        if word in TURKISH_CITIES_AND_DISTRICTS:
            return capitalize_turkish(word)
            
    # Substring matching to handle suffixes (e.g. "istanbul'da", "zonguldak'in", "devrek'te")
    for word in words:
        for loc in TURKISH_CITIES_AND_DISTRICTS:
            if word.startswith(loc):
                suffix = word[len(loc):]
                if not suffix or any(suffix.startswith(sfx) for sfx in ["da", "de", "ta", "te", "in", "in", "un", "un", "ya", "ye", "yi", "yi", "ler", "lar"]):
                    return capitalize_turkish(loc)
                    
    return None

logger = logging.getLogger("bridge")

BANNER = """
=========================================================================+
          ANTIGRAVITY OTONOM AJAN KOPRUSU (V1.1)
    "Siz sadece hayal edin ve hedefi yazin, gerisini ajanlara birakin."
=========================================================================+
"""


DIRECT_INTENT_PATTERNS = [
    r"\bkim\b",
    r"\bnedir\b",
    r"\bnasil\b",
    r"\bneden\b",
    r"\bne zaman\b",
    r"\bkac\b",
    r"\bsirala\b",
    r"\blistele\b",
    r"\bacikla\b",
    r"\bozetle\b",
    r"\bara[st]tir\b",
    r"\bbilgi\b",
    r"\bcevapla\b",
    r"\bsoyle\b",
]

BUILD_INTENT_PATTERNS = [
    r"\bkod\b",
    r"\bscript\b",
    r"\bdosya\b",
    r"\bsite\b",
    r"\bweb\b",
    r"\buygulama\b",
    r"\bproje\b",
    r"\bolustur\b",
    r"\byap\b",
    r"\bduzelt\b",
    r"\bekle\b",
    r"\bguncelle\b",
    r"\brefactor\b",
]


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])

    raise ValueError("JSON icerigi ayiklanamadi.")


def list_workspace_files() -> list[str]:
    root = Path(config.PROJECT_ROOT)
    ignored = {"logs", "backups", "__pycache__", ".git", ".env", ".venv", ".browser-profile", "node_modules"}
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return sorted(files)


def is_direct_answer_goal(goal: str) -> bool:
    if not config.DIRECT_ANSWER_MODE:
        return False

    normalized = (
        goal.lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    has_direct_intent = any(re.search(pattern, normalized) for pattern in DIRECT_INTENT_PATTERNS)
    has_build_intent = any(re.search(pattern, normalized) for pattern in BUILD_INTENT_PATTERNS)

    # "site yap", "dosya duzelt" gibi istekler build lane'e gider.
    if has_build_intent and not has_direct_intent:
        return False

    # "cirosu en fazla 10 sirketi sirala" gibi istekler dosya uretmemeli.
    return has_direct_intent


def is_workspace_analysis_goal(goal: str) -> bool:
    normalized = (
        (goal or "").lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    phrases = [
        "projeyi incele",
        "mimari sorun",
        "duzeltme plani",
        "kod yapisini analiz",
        "hatalari bul",
        "dosyalari oku ve raporla",
        "workspace analiz",
        "proje analiz",
        "mimari analiz"
    ]
    return any(p in normalized for p in phrases)



def answer_directly(goal: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Sen Turkce cevap veren pratik bir asistansin. Kullanici sadece bilgi, "
                "liste, ozet veya arastirma cevabi istiyorsa dosya olusturma, komut "
                "calistirma veya plan yazma. Cevabi dogrudan terminalde okunacak sekilde "
                "ver. Emin olmadigin guncel verilerde kaynak/tarih sinirini belirt."
            ),
        },
        {"role": "user", "content": goal},
    ]
    return chat_model(messages, temperature=0.2)


def answer_web_query(goal: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Kullanici guncel internet bilgisi istiyor. Web erisimin varsa guncel "
                "bilgiyi sade, tarih belirterek cevapla. Web erisimin yoksa bunu acikca "
                "soyle ve kullanicidan web aracini etkinlestirmesini iste. Dosya yazma, "
                "terminal komutu calistirma."
            ),
        },
        {"role": "user", "content": goal},
    ]
    return web_query_chat(messages, temperature=0.2)


def answer_content_generation(goal: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "Sen Turkce icerik ureten pratik bir asistansin. Kullanici uzun metin, "
                "makale, CV metni, paragraf veya akademik tarzda yazi istiyor. Dosya "
                "olusturma, terminal komutu calistirma veya local agent aksiyonu yapma; "
                "metni dogrudan cevaba yaz."
            ),
        },
        {"role": "user", "content": goal},
    ]
    return chat_model(messages, temperature=0.3)


def is_model_info_question(goal: str) -> bool:
    normalized = (
        (goal or "").lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
    return "hangi model" in normalized or "hangi modeli" in normalized or "model kullan" in normalized


def maybe_run_direct_local_tool(goal: str, route: dict, files: list[str], auto_approve_risky: bool, debug: bool = False) -> bool:
    """Deprecated: direct local tools are intentionally disabled.

    All official actions are now drafted first and run only by TaskRuntime.
    """
    return False


def maybe_run_simple_coding_graph(goal: str, route: dict, auto_approve_risky: bool, debug: bool = False) -> str | None:
    """Deprecated template coding lane; it must not bypass TaskRuntime."""
TOOL_APPROVAL_DETAILS = {
    "create_directory": {
        "description": "Bu işlem bilgisayarınızın masaüstünde veya proje klasöründe yeni bir klasör oluşturacak.",
        "effect": "Yeni bir klasör eklenecek. Mevcut dosyalar silinmeyecek veya değiştirilmeyecek.",
        "if_approved": "Sistem bu klasörü oluşturacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "open_browser": {
        "description": "Bu işlem varsayılan tarayıcınızı açarak belirtilen web sitesine yönlendirecek.",
        "effect": "Yeni bir tarayıcı penceresi veya sekmesi açılacak.",
        "if_approved": "Tarayıcı belirtilen URL adresiyle açılacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "open_application": {
        "description": "Bu işlem bilgisayarınızda allowlist içindeki güvenli bir uygulamayı (Not Defteri veya Hesap Makinesi) çalıştıracak.",
        "effect": "Belirtilen uygulama penceresi açılacak.",
        "if_approved": "Uygulama çalıştırılacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "write_file_with_diff": {
        "description": "Bu işlem proje klasöründeki bir dosyanın içeriğini güncelleyecek veya yeni bir dosya oluşturacak.",
        "effect": "Dosya içeriği güncellenecek. Önceki sürümle arasındaki fark (diff) uygulanacak.",
        "if_approved": "Değişiklikler dosyaya yazılacak.",
        "if_denied": "Dosya içeriği değiştirilmeyeacak."
    },
    "validate_python_syntax_sandboxed": {
        "description": "Bu işlem yazılan Python kodunun sözdizimini (syntax) güvenli bir ortamda kontrol edecek.",
        "effect": "Kod sözdizimi doğrulanacak, herhangi bir kod çalıştırılmayacak.",
        "if_approved": "Doğrulama işlemi gerçekleştirilecek.",
        "if_denied": "Doğrulama işlemi atlanacak."
    },
    "web_search_public": {
        "description": "Bu işlem internette halka açık kaynaklarda arama yapacak.",
        "effect": "İnternet araması gerçekleştirilecek.",
        "if_approved": "Arama sonuçları toplanacak.",
        "if_denied": "Arama yapılmayacak."
    },
    "open_folder": {
        "description": "Bu işlem Windows Explorer aracılığıyla belirtilen klasörü açacak.",
        "effect": "Klasör Explorer penceresinde görüntülenecek.",
        "if_approved": "Klasör açılacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "copy_file": {
        "description": "Bu işlem dosyayı veya klasörü kopyalayacak.",
        "effect": "Kaynak dosya korunacak ve hedef konumda yeni bir kopya oluşturulacak.",
        "if_approved": "Dosya kopyalanacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "move_file": {
        "description": "Bu işlem dosyayı bulunduğu yerden alıp başka bir klasöre taşıyacak.",
        "effect": "Dosya artık eski konumunda olmayacak, hedef klasöre taşınacak.",
        "if_approved": "Dosya taşınacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "safe_delete_file": {
        "description": "Bu işlem dosyayı kalıcı silme yerine proje içi güvenli çöp klasörüne taşıyacak.",
        "effect": "Dosya logs/safe_trash/ klasörüne taşınacak ve orijinal konumundan kaldırılacak.",
        "if_approved": "Dosya güvenli çöp klasörüne taşınacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "close_application_process": {
        "description": "Bu işlem belirtilen uygulamanın çalışan tüm proseslerini sonlandıracak.",
        "effect": "Uygulama kapatılacak ve kaydedilmemiş veriler kaybolabilir. Sistem dosyaları etkilenmez.",
        "if_approved": "Uygulama prosesleri sonlandırılacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "restart_application_resolved": {
        "description": "Bu işlem belirtilen uygulamanın çalışan tüm proseslerini kapatacak ve ardından uygulamayı yeniden başlatacak.",
        "effect": "Uygulama kapatılıp yeniden açılacak. Kaydedilmemiş veriler kaybolabilir.",
        "if_approved": "Uygulama kapatılacak ve yeniden başlatılacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "archive_application_logs": {
        "description": "Bu işlem belirtilen uygulamanın log dosyalarını kopyalayarak yedekleyecek.",
        "effect": "Orijinal log dosyaları korunur. Dosyalar yerel backup klasörüne kopyalanır.",
        "if_approved": "Log dosyaları kopyalanacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "backup_application_config": {
        "description": "Bu işlem belirtilen uygulamanın konfigürasyon/ayar dosyasını yedekleyecek.",
        "effect": "Orijinal ayar dosyası korunur. Dosya yerel backup klasörüne kopyalanır.",
        "if_approved": "Config dosyası kopyalanacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    },
    "clear_safe_application_cache": {
        "description": "Bu işlem belirtilen uygulamanın cache/önbellek klasöründeki dosyaları temizleyecek.",
        "effect": "Cache dosyaları silinmez, güvenli yedek klasörüne taşınır. Sistem klasörleri etkilenmez.",
        "if_approved": "Cache dosyaları yedek klasörüne taşınacak.",
        "if_denied": "Hiçbir işlem yapılmayacak."
    }
}


def format_path_friendly(path_str: str) -> str:
    if not path_str:
        return "-"
    from tools.safe_file_ops import resolve_file_path, resolve_special_folder
    try:
        resolved = resolve_file_path(path_str)
    except Exception:
        resolved = Path(path_str)
    
    try:
        desktop = resolve_special_folder("desktop")
        docs = resolve_special_folder("documents")
        downloads = resolve_special_folder("downloads")
    except Exception:
        home = Path.home()
        desktop = home / "Desktop"
        docs = home / "Documents"
        downloads = home / "Downloads"

    workspace = Path(config.PROJECT_ROOT).resolve()
    
    try:
        rel = resolved.relative_to(desktop)
        return f"Masaüstü / {rel.as_posix()}"
    except ValueError:
        pass
        
    try:
        rel = resolved.relative_to(docs)
        return f"Belgeler / {rel.as_posix()}"
    except ValueError:
        pass
        
    try:
        rel = resolved.relative_to(downloads)
        return f"İndirilenler / {rel.as_posix()}"
    except ValueError:
        pass
        
    try:
        rel = resolved.relative_to(workspace)
        return f"Proje Klasörü / {rel.as_posix()}"
    except ValueError:
        pass
        
    from data_policy import redact_text
    return redact_text(resolved.as_posix())


def _populate_plan_only_file_ops(runtime_plan: RuntimePlan) -> None:
    from data_policy import redact_text
    from tools.safe_file_ops import resolve_file_path
    for call in runtime_plan.calls:
        if call.tool in {"open_folder", "copy_file", "move_file", "safe_delete_file", "search_files", "get_file_info"}:
            config.LAST_RUN_FILE_OPERATION_TYPE = (
                "open_folder" if call.tool == "open_folder" else
                "copy" if call.tool == "copy_file" else
                "move" if call.tool == "move_file" else
                "delete" if call.tool == "safe_delete_file" else
                "search" if call.tool == "search_files" else
                "get_info"
            )
            src_p = call.payload.get("src") or call.payload.get("source") or call.payload.get("path") or ""
            dst_p = call.payload.get("dst") or call.payload.get("destination") or call.payload.get("target") or ""
            if call.tool == "safe_delete_file" and src_p:
                filename = Path(src_p).name
                dst_p = f"logs/safe_trash/{filename}"
            if src_p:
                try:
                    resolved_src = resolve_file_path(src_p)
                    config.LAST_RUN_SOURCE_REDACTED = redact_text(str(resolved_src))
                except Exception:
                    config.LAST_RUN_SOURCE_REDACTED = redact_text(str(src_p))
            if dst_p:
                try:
                    resolved_dst = resolve_file_path(dst_p)
                    config.LAST_RUN_TARGET_REDACTED = redact_text(str(resolved_dst))
                except Exception:
                    config.LAST_RUN_TARGET_REDACTED = redact_text(str(dst_p))
            break
        elif call.tool == "launch_application_resolved":
            import application_registry
            app_name = call.payload.get("app") or call.payload.get("application") or ""
            app_data = application_registry.match_application(app_name)
            if app_data:
                config.LAST_RUN_APPLICATION_NAME = app_data["display_name"]
                config.LAST_RUN_APPLICATION_ACTION_TYPE = "launch"
                config.LAST_RUN_REGISTRY_MATCH_CONFIDENCE = app_data["confidence"]
                config.LAST_RUN_REGISTRY_VERIFIED = app_data["verified"]
                config.LAST_RUN_LAUNCH_TYPE = app_data["launch_type"]
            else:
                config.LAST_RUN_APPLICATION_NAME = app_name
                config.LAST_RUN_APPLICATION_ACTION_TYPE = "launch"
                config.LAST_RUN_REGISTRY_MATCH_CONFIDENCE = 0.0
                config.LAST_RUN_REGISTRY_VERIFIED = False
                config.LAST_RUN_LAUNCH_TYPE = ""
            config.LAST_RUN_DIAGNOSTIC_STATUS = ""
            config.LAST_RUN_EVIDENCE_COUNT = 0
            config.LAST_RUN_ACTIONS_EXECUTED_COUNT = 0
            break
        elif call.tool == "application_diagnostics":
            app_name = call.payload.get("app") or call.payload.get("application") or ""
            config.LAST_RUN_APPLICATION_NAME = app_name
            config.LAST_RUN_APPLICATION_ACTION_TYPE = "diagnostics"
            config.LAST_RUN_REGISTRY_MATCH_CONFIDENCE = 0.0
            config.LAST_RUN_REGISTRY_VERIFIED = False
            config.LAST_RUN_LAUNCH_TYPE = ""
            config.LAST_RUN_DIAGNOSTIC_STATUS = "unknown"
            config.LAST_RUN_EVIDENCE_COUNT = 0
            config.LAST_RUN_ACTIONS_EXECUTED_COUNT = 0
            break
        elif call.tool in {
            "close_application_process", "restart_application_resolved", "archive_application_logs",
            "backup_application_config", "clear_safe_application_cache"
        }:
            import application_registry
            app_name = call.payload.get("app") or call.payload.get("application") or call.payload.get("application_name") or ""
            app_data = application_registry.match_application(app_name)
            
            disp_name = app_data.get("display_name", app_name) if app_data else app_name
            config.LAST_RUN_APPLICATION_NAME = disp_name
            config.LAST_RUN_APPLICATION_ACTION_TYPE = "remediation"
            
            action_map = {
                "close_application_process": "close_process",
                "restart_application_resolved": "restart_app",
                "archive_application_logs": "archive_logs",
                "backup_application_config": "backup_config",
                "clear_safe_application_cache": "clear_cache"
            }
            config.LAST_RUN_REMEDIATION_ACTION_TYPE = action_map.get(call.tool, "")
            config.LAST_RUN_TARGET_PROCESS_NAMES_REDACTED = disp_name if call.tool in {"close_application_process", "restart_application_resolved"} else ""
            
            pids_count = 0
            if call.tool in {"close_application_process", "restart_application_resolved"} and app_data:
                try:
                    from tools.app_launcher_tools import find_running_pids
                    pids_count = len(find_running_pids(app_data))
                except Exception:
                    pass
            config.LAST_RUN_TARGET_PIDS_COUNT = pids_count
            
            target_path = ""
            if call.tool == "archive_application_logs" and app_data:
                target_path = app_data.get("log_path", "")
            elif call.tool == "backup_application_config" and app_data:
                target_path = app_data.get("config_path", "")
                from tools.app_launcher_tools import check_file_contains_secrets
                if check_file_contains_secrets(target_path):
                    target_path = "<REDACTED>"
            elif call.tool == "clear_safe_application_cache" and app_data:
                target_path = app_data.get("cache_path", "")
                
            config.LAST_RUN_TARGET_PATHS_REDACTED = target_path
            config.LAST_RUN_DIAGNOSTIC_REPORT_LINKED = call.payload.get("diagnostic_report_linked", False)
            config.LAST_RUN_ACTIONS_EXECUTED_COUNT = 0
            config.LAST_RUN_REGISTRY_VERIFIED = app_data.get("verified", False) if app_data else False
            break


def _cli_approval(plan: RuntimePlan, decisions: list) -> bool:
    from data_policy import redact_text
    print("\nBu işlem için onay gerekiyor.\n")
    for index, (call, decision) in enumerate(zip(plan.calls, decisions), start=1):
        if len(plan.calls) > 1:
            print(f"--- Aksiyon {index} ---")
            
        tool_details = TOOL_APPROVAL_DETAILS.get(call.tool)
        if not tool_details and call.tool in {
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
        if not tool_details:
            tool_details = {
                "description": f"Bu işlem '{call.tool}' aracını çalıştıracak.",
                "effect": "Belirtilen işlem gerçekleştirilecek.",
                "if_approved": "İşlem uygulanacak.",
                "if_denied": "Hiçbir işlem yapılmayacak."
            }

        if call.tool == "launch_application_resolved":
            import application_registry
            app_name = call.payload.get("app") or call.payload.get("application") or ""
            app_data = application_registry.match_application(app_name)
            
            disp_name = app_data.get("display_name", app_name) if app_data else app_name
            l_type = app_data.get("launch_type", "unknown") if app_data else "unknown"
            source = app_data.get("source", "unknown") if app_data else "unknown"
            target = app_data.get("launch_target", "") if app_data else ""
            
            source_map = {
                "steam": "Steam manifest / registry match",
                "start_menu": "Start Menu shortcut / registry match",
                "desktop": "Desktop shortcut / registry match",
                "alias": "User defined alias / registry match",
                "metadata": "Program Files metadata / registry match"
            }
            source_desc = source_map.get(source, f"{source} / registry match")
            
            if l_type == "steam_uri":
                change_desc = "Steam üzerinden oyun başlatılmaya çalışılacak. Sistem dosyaları değiştirilmez."
                approved_desc = f"Steam üzerinden {disp_name} başlatılacak."
            elif l_type == "shortcut":
                change_desc = "Kısayol hedefindeki uygulama başlatılacak. Sistem dosyaları değiştirilmez."
                approved_desc = f"{disp_name} başlatılacak."
            elif l_type == "browser_url":
                change_desc = "Tarayıcı üzerinden belirtilen adres açılacak. Sistem dosyaları değiştirilmez."
                approved_desc = f"Tarayıcıda {disp_name} açılacak."
            else:
                change_desc = "Belirtilen uygulama başlatılacak. Sistem dosyaları değiştirilmez."
                approved_desc = f"{disp_name} başlatılacak."
                
            print(f"Kullanıcının isteği:\n{redact_text(plan.goal)}")
            print(f"\nBulunan uygulama:\n{disp_name}")
            print(f"\nTeknik işlem:\n{call.tool}")
            print(f"\nLaunch türü:\n{l_type}")
            print(f"\nKaynak:\n{source_desc}")
            print(f"\nHedef:\n{target}")
            print(f"\nBilgisayarda ne değişecek?\n{change_desc}")
            print(f"\nRisk seviyesi:\n{decision.risk}")
            print(f"\nOnay verirseniz:\n{approved_desc}")
            print(f"\nOnay vermezseniz:\nHiçbir işlem yapılmayacak.")
        elif call.tool in {
            "close_application_process", "restart_application_resolved", "archive_application_logs",
            "backup_application_config", "clear_safe_application_cache"
        }:
            import application_registry
            app_name = call.payload.get("app") or call.payload.get("application") or call.payload.get("application_name") or ""
            app_data = application_registry.match_application(app_name)
            
            disp_name = app_data.get("display_name", app_name) if app_data else app_name
            
            pids = []
            if app_data:
                try:
                    from tools.app_launcher_tools import find_running_pids
                    pids = find_running_pids(app_data)
                except Exception:
                    pass
            pids_str = ", ".join(pids) if pids else "Bulunamadı (Çalışmıyor)"
            
            print(f"Kullanıcının isteği:\n{redact_text(plan.goal)}")
            print(f"\nBulunan uygulama:\n{disp_name}")
            print(f"\nTeknik işlem:\n{call.tool}")
            
            if call.tool in {"close_application_process", "restart_application_resolved"}:
                print(f"\nİlişkili PID'ler:\n{pids_str}")
                print("\n[UYARI] Bu işlem kaydedilmemiş verilerin kaybına neden olabilir!")
            elif call.tool == "backup_application_config":
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
                    print("\n[UYARI] Bu dosya gizli değerler içerebilir, yalnızca yerel backup klasörüne kopyalanacak")
                print(f"\nConfig dosyası:\n{display_path}")
            elif call.tool == "archive_application_logs":
                log_path = app_data.get("log_path", "") if app_data else "-"
                print(f"\nLog yolu:\n{log_path}")
            elif call.tool == "clear_safe_application_cache":
                cache_path = app_data.get("cache_path", "") if app_data else "-"
                print(f"\nCache yolu:\n{cache_path}")
                
            print(f"\nBilgisayarda ne değişecek?\n{tool_details['effect']}")
            print(f"\nRisk seviyesi:\n{decision.risk}")
            print(f"\nOnay verirseniz:\n{tool_details['if_approved']}")
            print(f"\nOnay vermezseniz:\n{tool_details['if_denied']}")
        elif call.tool in {"copy_file", "move_file", "safe_delete_file"}:
            if call.tool == "safe_delete_file":
                src_path = call.payload.get("path") or ""
                src_formatted = format_path_friendly(src_path)
                filename = Path(src_path).name if src_path else "file"
                dst_formatted = f"logs/safe_trash / {filename}"
            else:
                src_path = call.payload.get("src") or call.payload.get("source") or ""
                dst_path = call.payload.get("dst") or call.payload.get("destination") or call.payload.get("target") or ""
                src_formatted = format_path_friendly(src_path)
                dst_formatted = format_path_friendly(dst_path)
 
            print(f"Kullanıcının isteği:\n{redact_text(plan.goal)}")
            print(f"\nTeknik işlem:\n{call.tool}")
            print(f"\nBasit açıklama:\n{tool_details['description']}")
            print(f"\nKaynak:\n{src_formatted}")
            print(f"\nHedef:\n{dst_formatted}")
            print(f"\nBilgisayarda ne değişecek?\n{tool_details['effect']}")
            print(f"\nRisk seviyesi:\n{decision.risk}")
            print(f"\nOnay verirseniz:\n{tool_details['if_approved']}")
            print(f"\nOnay vermezseniz:\n{tool_details['if_denied']}")
        else:
            target = call.payload.get("target") or call.payload.get("path") or call.payload.get("app") or call.payload.get("query") or ""
            redacted_target = redact_text(str(target))
            if call.tool == "create_directory":
                loc_str = "Masaüstü" if call.payload.get("location") == "desktop" else "Proje Klasörü"
                redacted_target = f"{loc_str} / {redacted_target}"
                 
            print(f"Kullanıcının isteği:\n{redact_text(plan.goal)}")
            print(f"\nTeknik işlem:\n{call.tool}")
            print(f"\nBasit açıklama:\n{tool_details['description']}")
            print(f"\nHedef:\n{redacted_target if redacted_target else '-'}")
            print(f"\nBilgisayarda ne değişecek?\n{tool_details['effect']}")
            print(f"\nRisk seviyesi:\n{decision.risk}")
            print(f"\nOnay verirseniz:\n{tool_details['if_approved']}")
            print(f"\nOnay vermezseniz:\n{tool_details['if_denied']}")
        
        if decision.preview.get("diff"):
            print("\n   Diff:")
            print(redact_text(decision.preview["diff"]))
        print()

    answer = input("Devam edilsin mi? (evet/hayır): ").strip().lower()
    return answer in {"evet", "e", "yes", "y"}


def _format_runtime_report(report: RuntimeReport) -> str:
    diagnostic_tools = {
        "get_system_info", "read_recent_event_logs", "read_reliability_history", "get_last_boot_reason",
        "list_recent_crashes", "check_disk_health_readonly", "list_driver_errors",
        "list_windows_update_history", "list_startup_apps", "list_running_processes_summary",
    }
    lines = [f"TaskRuntime | task={report.task_id} | status={report.status}"]
    for rejected in report.rejected_actions:
        lines.append(f"- RED: {rejected}")
    for index, result in enumerate(report.results):
        lines.append(f"- {'OK' if result.ok else 'HATA'}: {result.message}")
        tool = report.decisions[index].preview.get("tool") if index < len(report.decisions) else ""
        if tool in diagnostic_tools and result.data:
            details = json.dumps(result.data, ensure_ascii=False, indent=2, default=str)
            lines.append(details[:16_000] + ("\n[diagnostic output truncated]" if len(details) > 16_000 else ""))
        if tool == "web_search_public" and result.data:
            lines.append("Public kaynaklar:")
            for item in result.data.get("results", []):
                lines.append(f"- {item.get('title', '-')} | {item.get('source', '-')} | {item.get('url', '-')}")
                if item.get("snippet"):
                    lines.append(f"  {item['snippet']}")
    if not report.results and report.status == "needs_approval":
        lines.append("- Plan onay bekliyor.")
    return "\n".join(lines)


def _format_diagnostic_report(report) -> str:
    lines = [
        "TESHIS RAPORU",
        f"Senaryo: {report.scenario}",
        f"Ozet: {report.summary}",
        f"Risk seviyesi: {report.severity}",
        f"Guven skoru: {report.confidence:.0%}",
        "Kanıtlar:",
    ]
    lines.extend(f"- [{item.severity}] {item.summary}" for item in report.evidence[:12])
    if report.blocked_or_unavailable_sources:
        lines.append("Erisilemeyen kaynaklar:")
        lines.extend(f"- {item['source']}: {item['reason']}" for item in report.blocked_or_unavailable_sources)
    lines.append("Olası nedenler:")
    lines.extend(f"- {item}" for item in report.possible_causes)
    lines.append("Sonraki adımlar:")
    lines.extend(f"- {item}" for item in report.recommended_next_steps)
    return "\n".join(lines)


def _audit_diagnostic_report(runtime: TaskRuntime, report) -> None:
    runtime.audit.record(
        "diagnostic_report_generated",
        report.task_id if hasattr(report, "task_id") else "diagnostic_report",
        scenario=report.scenario,
        severity=report.severity,
        confidence=report.confidence,
        unavailable_sources=[item["source"] for item in report.blocked_or_unavailable_sources],
    )


def build_architect_prompt(goal: str, files: list[str]) -> list[dict]:
    files_list = "\n".join(f"- {item}" for item in files) if files else "(bos)"
    system_prompt = f"""
Sen kidemli bir yazilim mimarisin. Kullanici hedefini uygulanabilir adimlara bol.

Mevcut dosyalar:
{files_list}

Hedef:
{goal}

Kurallar:
- Bilgi sorusu, listeleme veya arastirma istekleri icin dosya hedefi uretme; tek COMPLETE adimi oner.
- Mevcut altyapi dosyalarini hedef dosya yapma: agents.py, bridge.py, security.py, config.py, orchestrator.py.
- Sadece gecerli JSON dondur.

Format:
{{
  "analysis": "kisa analiz",
  "steps": [
    {{"step_number": 1, "description": "adim", "target_files": ["path"]}}
  ]
}}
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Sadece JSON dondur."},
    ]


def build_coder_prompt(goal: str, plan: dict, files: list[str], iteration: int) -> list[dict]:
    files_list = "\n".join(f"- {item}" for item in files) if files else "(bos)"
    system_prompt = f"""
Sen uzman bir gelistirici ajanisin. Siradaki tek aksiyonu JSON olarak sec.

Hedef: {goal}
Plan: {json.dumps(plan, ensure_ascii=False)}
Mevcut dosyalar:
{files_list}
Iterasyon: {iteration}

Aksiyonlar:
- WRITE_FILE: {{"action":"WRITE_FILE","file_path":"relative/path","content":"..."}}
- READ_FILE: {{"action":"READ_FILE","file_path":"relative/path"}}
- RUN_COMMAND: {{"action":"RUN_COMMAND","command":"..."}}
- COMPLETE: {{"action":"COMPLETE","summary":"..."}}

Kurallar:
- Bilgi sorularinda dogrudan COMPLETE sec; dosya yazma.
- agents.py, bridge.py, security.py, config.py, orchestrator.py, requirements.txt dosyalarini yazma.
- Sadece gecerli JSON dondur.
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Siradaki aksiyonu JSON olarak ver."},
    ]


def build_critic_prompt(action_taken: str, result: str, goal: str) -> list[dict]:
    system_prompt = f"""
Son aksiyonu hedefe gore degerlendir.

Hedef: {goal}
Aksiyon: {action_taken}
Sonuc: {result}

Sadece JSON dondur:
{{"status":"SUCCESS|ERROR|RETRY","critique":"...","instructions_for_next_step":"..."}}
"""
    return [{"role": "system", "content": system_prompt}]


def normalize_command(command: str) -> str:
    command = (command or "").strip()
    python_exe = f'"{sys.executable}"'
    replacements = {
        "python ": f"{python_exe} ",
        "python3 ": f"{python_exe} ",
        "py ": f"{python_exe} ",
        "pip ": f"{python_exe} -m pip ",
    }
    lowered = command.lower()
    for prefix, replacement in replacements.items():
        if lowered.startswith(prefix):
            return replacement + command[len(prefix) :]
    return command


def _print_provider_selection(provider, debug: bool) -> None:
    if not getattr(config, "INTERACTIVE_MODE", False) or debug:
        print(f"\n[PROVIDER_DECISION] Selected Provider: {provider.provider_name} | Cost Mode: {provider.estimated_cost}")
        if debug:
            print("\n[PROVIDER_DECISION_LOG_JSON]")
            print(json.dumps(provider.decision_log, ensure_ascii=False, indent=2))


def resolve_goal_route(goal: str, *, preview_only: bool = False) -> dict:
    """Resolve routing and execution pipeline details for a given goal."""
    import re
    goal = re.sub(r"^\s*(?:\d+[\.\)]|\[\d+\])\s*", "", goal)
    
    from router import is_alarm_query, RouteResult
    if is_alarm_query(goal):
        route = RouteResult(
            category="local_computer_action",
            confidence=1.0,
            reason="Unsupported alarm or reminder request.",
            risk="low",
            intent_type="unsupported_alarm_or_reminder"
        )
        return {
            "category": "local_computer_action",
            "confidence": 1.0,
            "risk": "low",
            "reason": "Unsupported alarm or reminder request.",
            "direct_map": False,
            "tools": [],
            "actions": [],
            "is_workspace_analysis": False,
            "playbook": None,
            "diagnostic_actions": [],
            "public_web_actions": [],
            "route_obj": route,
            "unsupported_alarm": True
        }

    from direct_tool_mapper import try_direct_map
    from diagnostic_playbooks import match_playbook
    from diagnostic_intents import build_readonly_diagnostic_actions
    from router import classify_message
    from coding_loop import can_handle_coding_goal
    
    # 1. Try Direct Mapper
    direct_actions = try_direct_map(goal)
    if direct_actions:
        if any(act.get("error") == "missing_destination" for act in direct_actions):
            act = direct_actions[0]
            return {
                "category": "local_computer_action",
                "confidence": 1.0,
                "risk": "low",
                "reason": "Destination folder is missing.",
                "direct_map": True,
                "tools": [],
                "actions": [],
                "is_workspace_analysis": False,
                "playbook": None,
                "diagnostic_actions": [],
                "public_web_actions": [],
                "route_obj": RouteResult(
                     "local_computer_action",
                     1.0,
                     "Destination folder is missing.",
                     risk="low",
                     intent_type="file_operation",
                     metadata={"missing_destination": True, "filename": act.get("filename"), "operation": act.get("operation")}
                 ),
                "missing_destination": True,
                "filename": act.get("filename"),
                "operation": act.get("operation")
            }
        tools = [act.get("tool") or act.get("type") for act in direct_actions if act.get("tool") or act.get("type")]
        from policy_engine import TOOL_RISKS
        max_risk = "low"
        for t in tools:
            risk_val = TOOL_RISKS.get(t, "low")
            if risk_val == "high":
                max_risk = "high"
            elif risk_val == "medium" and max_risk != "high":
                max_risk = "medium"
        return {
            "category": "local_computer_action",
            "confidence": 1.0,
            "risk": max_risk,
            "reason": "Direct tool mapping match.",
            "direct_map": True,
            "tools": tools,
            "actions": direct_actions,
            "is_workspace_analysis": False,
            "playbook": None,
            "diagnostic_actions": [],
            "public_web_actions": [],
            "route_obj": RouteResult("local_computer_action", 1.0, "Direct tool mapping match.", risk=max_risk, intent_type="ACTION_REQUEST")
        }
        
    # 2. Try Workspace Analysis
    if is_workspace_analysis_goal(goal):
        return {
            "category": "file_workspace_task",
            "confidence": 1.0,
            "risk": "low",
            "reason": "Mapped to workspace analysis task.",
            "direct_map": False,
            "tools": ["list_workspace_files", "read_file_limited"],
            "actions": [],
            "is_workspace_analysis": True,
            "playbook": None,
            "diagnostic_actions": [],
            "public_web_actions": [],
            "route_obj": RouteResult("file_workspace_task", 1.0, "Mapped to workspace analysis task.", risk="low", intent_type="ACTION_REQUEST")
        }
        
    # 3. Normal Route Classification
    route = classify_message(goal, route_only=preview_only, plan_only=preview_only)
    
    if route.category == "weather_query" or route.intent_type == "weather_query":
        location = extract_weather_location(goal)
        normalized_query = f"7 günlük {location} hava durumu" if location else ""
        return {
            "category": "weather_query",
            "confidence": route.confidence,
            "risk": "low",
            "reason": route.reason,
            "direct_map": False,
            "tools": ["web_search_public"] if location else [],
            "actions": [],
            "is_workspace_analysis": False,
            "playbook": None,
            "diagnostic_actions": [],
            "public_web_actions": [{"type": "web_search_public", "query": normalized_query, "limit": 5, "reason": "Hava durumu arastirmasi."}] if location else [],
            "route_obj": route,
            "location": location
        }

    # 4. Check Coding Loop
    if can_handle_coding_goal(goal, route.category):
        from coding_loop import build_file_plan
        try:
            files_preview = list(build_file_plan(goal))
        except Exception:
            files_preview = []
        return {
            "category": route.category,
            "confidence": route.confidence,
            "risk": route.risk,
            "reason": route.reason,
            "direct_map": False,
            "tools": ["write_file_with_diff", "read_file_limited"],
            "actions": [{"tool": "write_file_with_diff", "path": f} for f in files_preview],
            "is_workspace_analysis": False,
            "playbook": None,
            "diagnostic_actions": [],
            "public_web_actions": [],
            "route_obj": route
        }
        
    # 5. Playbook Match
    playbook = match_playbook(goal)
    diagnostic_actions = playbook.actions() if playbook else build_readonly_diagnostic_actions(goal)
    public_web_actions = (
        [{"type": "web_search_public", "query": goal, "limit": 5, "reason": "Public kaynakli arastirma istegi."}]
        if route.category == "research"
        else []
    )
    
    if diagnostic_actions and route.category in {"conversation", "content_generation", "research"}:
        route.category = "local_computer_action"
        route.needs_local_agent = True
        route.risk = "low"
        route.reason = "Deterministic read-only Windows diagnostic capability selected."
        
    tools = []
    if diagnostic_actions:
        tools = [act.get("tool") or act.get("type") for act in diagnostic_actions if act.get("tool") or act.get("type")]
    elif public_web_actions:
        tools = [act.get("tool") or act.get("type") for act in public_web_actions if act.get("tool") or act.get("type")]
        
    return {
        "category": route.category,
        "confidence": route.confidence,
        "risk": route.risk,
        "reason": route.reason,
        "direct_map": False,
        "tools": tools,
        "actions": diagnostic_actions or public_web_actions or [],
        "is_workspace_analysis": False,
        "playbook": playbook,
        "diagnostic_actions": diagnostic_actions,
        "public_web_actions": public_web_actions,
        "route_obj": route
    }


def run_bridge_autonomous_loop(
    goal: str,
    max_iterations: int = 15,
    route_only: bool = False,
    auto_approve_risky: bool = False,
    plan_only: bool = False,
    provider_override: str | None = None,
    browser_target: str | None = None,
    debug: bool = False,
    approval_callback=None,
    diagnostic_report_callback=None,
) -> None:
    import time
    import re
    goal = re.sub(r"^\s*(?:\d+[\.\)]|\[\d+\])\s*", "", goal)
    
    config.PLAN_ONLY = plan_only
    config.LAST_RUN_START_TIME = time.perf_counter()
    config.LAST_RUN_TOOLS = []
    try:
        import web_server
        web_server.ACTIVE_EXECUTION["status"] = "running"
    except Exception:
        pass
    config.LAST_RUN_APPROVAL_REQUIRED = False
    config.LAST_RUN_APPROVAL_GRANTED = False
    config.LAST_RUN_BLOCKED_BY_PLAN_ONLY = plan_only
    if not getattr(config, "INTERACTIVE_MODE", False) or debug:
        print(f"\nHedef alindi: {goal}")

    # Use the shared route resolver
    res = resolve_goal_route(goal, preview_only=(route_only or plan_only))
    route = res["route_obj"]
    
    if res.get("unsupported_alarm"):
        answer = "Şu an alarm kurma aracı bağlı değil."
        try:
            import web_server
            web_server.ACTIVE_EXECUTION["status"] = "unsupported_capability"
            web_server.ACTIVE_EXECUTION["summary"] = answer
            web_server.ACTIVE_EXECUTION["tools_used"] = []
        except Exception:
            pass
        task_plan = build_task_plan_fallback(goal, route.as_dict())
        provider = select_provider(task_plan.as_dict(), goal, provider_override, browser_target)
        provider.provider_name = "direct_response"
        provider.provider_type = "direct_response_provider"
        _print_provider_selection(provider, debug)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return
    
    if res.get("missing_destination"):
        filename = res.get("filename", "dosyayi")
        op_verb = "taşımamı" if res.get("operation") == "tasi" else "kopyalamamı"
        answer = f"{filename} dosyasını nereye {op_verb} istiyorsun?"
        try:
            import web_server
            web_server.ACTIVE_EXECUTION["status"] = "needs_clarification"
            web_server.ACTIVE_EXECUTION["summary"] = answer
            web_server.ACTIVE_EXECUTION["tools_used"] = []
        except Exception:
            pass
        task_plan = build_task_plan_fallback(goal, route.as_dict())
        provider = select_provider(task_plan.as_dict(), goal, provider_override, browser_target)
        _print_provider_selection(provider, debug)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return

    direct_actions = res["actions"] if res["direct_map"] else []
    playbook = res["playbook"]
    diagnostic_actions = res["diagnostic_actions"]
    public_web_actions = res["public_web_actions"]

    if res["direct_map"]:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print(f"\n[DIRECT_MAP] Mapped goal directly: {res['tools']}")
            print(
                "\nRouter sonucu: "
                f"{translate_category_for_display(route.category)} | guven={route.confidence:.2f} | risk={route.risk}"
            )
            print(f"Neden: {route.reason}")
        if route_only:
            return
        task_plan = build_task_plan_fallback(goal, route.as_dict())
        provider = select_provider(task_plan.as_dict(), goal, provider_override, browser_target)
        _print_provider_selection(provider, debug)

        runtime = TaskRuntime()
        runtime_plan = runtime.build_plan(goal, direct_actions)
        if plan_only:
            preview = runtime.inspect_plan(runtime_plan)
            config.LAST_RUN_TOOLS = [call.tool for call in runtime_plan.calls]
            config.LAST_RUN_APPROVAL_REQUIRED = any(d.requires_approval for d in preview)
            config.LAST_RUN_APPROVAL_GRANTED = False
            config.LAST_RUN_BLOCKED_BY_PLAN_ONLY = True
            config.LAST_RUN_APPROVAL_WOULD_BE_REQUIRED = any(d.requires_approval for d in preview)
            _populate_plan_only_file_ops(runtime_plan)
            combined = {
                "safe_plan": {
                    "summary": "Direct mapping plan only report.",
                    "workspace_files": list_workspace_files(),
                    "needed_files": [],
                    "actions": [
                        {
                            "tool": act.get("tool") or act.get("type"),
                            "would_execute": False,
                            "reason": "plan_only_active",
                            "actual_execution": "blocked"
                        }
                        for act in direct_actions
                    ],
                    "risk_level": "low",
                    "requires_user_approval": False,
                    "errors": []
                },
                "task_runtime": {
                    "task_id": runtime_plan.task_id,
                    "tools": [call.tool for call in runtime_plan.calls],
                    "calls": [{"tool": call.tool, "payload": call.payload} for call in runtime_plan.calls],
                    "policy": [decision.__dict__ for decision in preview],
                    "rejected_actions": runtime_plan.rejected_actions,
                },
            }
            print(synthesize_result(task_plan.as_dict(), provider.as_dict(), combined if not debug else json.dumps(combined, ensure_ascii=False, indent=2), debug))
            return
        runtime_report = runtime.execute_plan(
            runtime_plan,
            approval_callback=approval_callback or _cli_approval,
            dev_auto_approve=auto_approve_risky,
        )
        update_web_server_status(runtime_report)
        result_text = _format_runtime_report(runtime_report)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), result_text, debug))
        return

    if res["is_workspace_analysis"]:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print(f"\n[WORKSPACE_ANALYSIS] Goal mapped to Workspace Analysis v2.")
            print(
                "\nRouter sonucu: "
                f"{translate_category_for_display(route.category)} | guven={route.confidence:.2f} | risk={route.risk}"
            )
            print(f"Neden: {route.reason}")
        if route_only:
            return
        task_plan = build_task_plan_fallback(goal, route.as_dict())
        provider = select_provider(task_plan.as_dict(), goal, provider_override, browser_target)
        _print_provider_selection(provider, debug)

        
        from workspace_analysis import analyze_workspace
        files = list_workspace_files()
        report = analyze_workspace(goal, files)
        
        config.LAST_RUN_TOOLS = ["list_workspace_files", "read_file_limited"]
        result = {
            "status": "success",
            "analysis_report": report
        }
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), result, debug))
        return

    from coding_loop import can_handle_coding_goal, run_coding_loop, build_file_plan
    if can_handle_coding_goal(goal, route.category):
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print(f"\n[CODING_LOOP] Goal mapped to simple coding loop.")
            print(
                "\nRouter sonucu: "
                f"{translate_category_for_display(route.category)} | guven={route.confidence:.2f} | risk={route.risk}"
            )
            print(f"Neden: {route.reason}")
        if route_only:
            return
        task_plan = build_task_plan_fallback(goal, route.as_dict())
        provider = select_provider(task_plan.as_dict(), goal, provider_override, browser_target)
        _print_provider_selection(provider, debug)

        if plan_only:
            config.LAST_RUN_TOOLS = ["write_file_with_diff", "read_file_limited"]
            combined = {
                "summary": "Plan only coding report.",
                "created_files": list(build_file_plan(goal)),
                "validation": {"passed": True, "issues": []},
            }
            print(synthesize_result(task_plan.as_dict(), provider.as_dict(), combined, debug))
            return
        result = run_coding_loop(goal, auto_approve=auto_approve_risky)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), result, debug))
        return

    if not getattr(config, "INTERACTIVE_MODE", False) or debug:
        print(
            "\nRouter sonucu: "
            f"{translate_category_for_display(route.category)} | guven={route.confidence:.2f} | risk={route.risk}"
        )
        print(f"Neden: {route.reason}")
    if route.metadata and debug:
        print("Ek bilgi:")
        print(json.dumps(route.metadata, ensure_ascii=False, indent=2))

    if route_only:
        return

    if auto_approve_risky and not config.DEV_MODE:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print("\n--auto-approve-risky normal runtime'da devre disidir; kullanici onayi istenecek.")
        auto_approve_risky = False

    # Simple answers and plan-only mode must not make an additional browser/API
    # planning call. Their deterministic plan is sufficient and keeps egress
    # narrow by default.
    if diagnostic_actions or public_web_actions or plan_only or route.category in {"conversation", "content_generation", "research", "browser_model_task"}:
        task_plan = build_task_plan_fallback(goal, route.as_dict())
    else:
        task_plan = build_task_plan(goal, route.as_dict())
    provider = select_provider(task_plan.as_dict(), goal, provider_override, browser_target)
    _print_provider_selection(provider, debug)

    if debug:
        print("\nTask plan:")
        print(json.dumps(task_plan.as_dict(), ensure_ascii=False, indent=2))
        print("\nProvider secimi:")
        print(json.dumps(provider.as_dict(), ensure_ascii=False, indent=2))

    if provider.provider_name in {"api", "groq", "browser", "browser_gpt", "local_model"}:
        config.CHAT_PROVIDER = provider.provider_name
        if provider.browser_target:
            config.CHAT_BROWSER_TARGET = provider.browser_target

    if is_model_info_question(goal):
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print("\nModel bilgisi modu: cevap config dosyasindan okunacak, model tahmini yapilmayacak.\n")
        print(active_model_info())
        return

    if res.get("missing_destination"):
        filename = res.get("filename", "dosyayi")
        op_verb = "taşımamı" if res.get("operation") == "tasi" else "kopyalamamı"
        answer = f"{filename} dosyasını nereye {op_verb} istiyorsun?"
        try:
            import web_server
            web_server.ACTIVE_EXECUTION["status"] = "needs_clarification"
            web_server.ACTIVE_EXECUTION["summary"] = answer
            web_server.ACTIVE_EXECUTION["tools_used"] = []
        except Exception:
            pass
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return

    if route.intent_type == "small_talk" or task_plan.task_type == "small_talk":
        answer = "İyiyim, sen nasılsın? Ne yapmak istersin?"
        try:
            import web_server
            web_server.ACTIVE_EXECUTION["status"] = "completed"
            web_server.ACTIVE_EXECUTION["summary"] = answer
            web_server.ACTIVE_EXECUTION["tools_used"] = []
        except Exception:
            pass
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return

    if route.category == "weather_query" or task_plan.task_type == "weather_query":
        location = extract_weather_location(goal)
        if not location:
            answer = "Hangi şehir veya ilçe için 1 haftalık hava durumunu öğrenmek istiyorsun?"
            try:
                import web_server
                web_server.ACTIVE_EXECUTION["status"] = "needs_clarification"
                web_server.ACTIVE_EXECUTION["summary"] = answer
                web_server.ACTIVE_EXECUTION["tools_used"] = []
            except Exception:
                pass
            print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
            return
            
        if plan_only:
            combined = {
                "safe_plan": {
                    "summary": "Weather query plan only report.",
                    "workspace_files": list_workspace_files(),
                    "needed_files": [],
                    "actions": [
                        {
                            "tool": "web_search_public",
                            "query": f"7 günlük {location} hava durumu",
                            "would_execute": False,
                            "reason": "plan_only_active",
                            "actual_execution": "blocked"
                        }
                    ],
                    "risk_level": "low",
                    "validation": {"passed": True, "issues": []},
                }
            }
            print(synthesize_result(task_plan.as_dict(), provider.as_dict(), combined, debug))
            return
            
        weather_actions = [{"type": "get_weather", "city": location, "reason": "Hava durumu sorgusu."}]
        
        runtime = TaskRuntime()
        runtime_plan = runtime.build_plan(goal, weather_actions)
        runtime_report = runtime.execute_plan(runtime_plan, approval_callback=approval_callback or _cli_approval)
        update_web_server_status(runtime_report)
        
        # Extract the weather report text from the tool result
        weather_text = ""
        for r in runtime_report.results:
            if r.ok and r.message:
                weather_text = r.message
                break
            elif not r.ok and r.message:
                weather_text = r.message
                break
        
        if not weather_text:
            weather_text = _format_runtime_report(runtime_report)
        
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), weather_text, debug))
        return

    if route.category == "conversation" and not plan_only:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print("\nSimple chat modu: dosya olusturulmayacak, agent calismayacak.\n")
        answer = answer_directly(goal)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return

    if route.category == "content_generation" and not plan_only:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print("\nContent generation modu: dosya olusturulmayacak, metin uretim provider'ina gidilecek.\n")
        try:
            answer = answer_content_generation(goal)
        except Exception as exc:
            logger.error("Content generation hatasi: %s", exc)
            answer = f"Icerik uretimi calisamadi. Provider/model ayarlarini kontrol edin. Hata: {exc}"
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return

    if public_web_actions and not plan_only:
        runtime = TaskRuntime()
        runtime_plan = runtime.build_plan(goal, public_web_actions)
        runtime_report = runtime.execute_plan(runtime_plan, approval_callback=approval_callback or _cli_approval)
        update_web_server_status(runtime_report)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), _format_runtime_report(runtime_report), debug))
        return

    if route.category == "browser_model_task" and not plan_only:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print("\nWeb query modu: dosya olusturulmayacak, guncel bilgi saglayiciya gidilecek.\n")
        try:
            answer = answer_directly(goal) if provider.provider_type == "browser_model_provider" else answer_web_query(goal)
        except Exception as exc:
            logger.error("Web query hatasi: %s", exc)
            answer = (
                "Web sorgusu calisamadi. WEB_QUERY_PROVIDER ayarini veya browser_gpt "
                f"oturumunu kontrol edin. Hata: {exc}"
            )
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), answer, debug))
        return

    if not getattr(config, "INTERACTIVE_MODE", False) or debug:
        print("\nLocal agent akisi basliyor.")
    files = list_workspace_files()

    if plan_only:
        if not getattr(config, "INTERACTIVE_MODE", False) or debug:
            print("Plan-only modu: model veya tool cagrisi yapilmadan policy onizlemesi uretilecek.\n")
        report = build_plan_only_report(goal, files, route.as_dict())
        report["actions"] = [
            {
                "tool": act.get("tool") or act.get("type"),
                "would_execute": False,
                "reason": "plan_only_active",
                "actual_execution": "blocked"
            }
            for act in report.get("actions", [])
        ]
        runtime = TaskRuntime()
        runtime_plan = runtime.build_plan(goal, diagnostic_actions or report.get("actions") or [])
        preview = runtime.inspect_plan(runtime_plan)
        config.LAST_RUN_TOOLS = [call.tool for call in runtime_plan.calls]
        config.LAST_RUN_APPROVAL_REQUIRED = any(d.requires_approval for d in preview)
        config.LAST_RUN_APPROVAL_GRANTED = False
        config.LAST_RUN_BLOCKED_BY_PLAN_ONLY = True
        config.LAST_RUN_APPROVAL_WOULD_BE_REQUIRED = any(d.requires_approval for d in preview)
        _populate_plan_only_file_ops(runtime_plan)
        combined = {
            "safe_plan": report,
            "task_runtime": {
                "task_id": runtime_plan.task_id,
                "tools": [call.tool for call in runtime_plan.calls],
                "calls": [{"tool": call.tool, "payload": call.payload} for call in runtime_plan.calls],
                "policy": [decision.__dict__ for decision in preview],
                "rejected_actions": runtime_plan.rejected_actions,
            },
        }
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), combined if not debug else json.dumps(combined, ensure_ascii=False, indent=2), debug))
        return

    if diagnostic_actions:
        runtime = TaskRuntime()
        runtime_plan = runtime.build_plan(goal, diagnostic_actions)
        runtime_report = runtime.execute_plan(runtime_plan, approval_callback=approval_callback or _cli_approval)
        update_web_server_status(runtime_report)
        if playbook:
            diagnostic_report = build_diagnostic_report(playbook.scenario, runtime_report)
            diagnostic_report.task_id = runtime_report.task_id
            _audit_diagnostic_report(runtime, diagnostic_report)
            if diagnostic_report_callback:
                diagnostic_report_callback(diagnostic_report.as_dict())
            result_text = _format_diagnostic_report(diagnostic_report)
        else:
            result_text = _format_runtime_report(runtime_report)
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), result_text, debug))
        return

    if not getattr(config, "INTERACTIVE_MODE", False) or debug:
        print("Prompt architect istegi local agent icin net gorev promptuna cevirecek.\n")
    agent_prompt = build_agent_prompt(goal, route.as_dict(), files)

    report = run_local_agent(agent_prompt, original_goal=goal, auto_approve=False, execute=False)
    if report.critic.get("decision") != "approve":
        result_text = format_local_agent_report(report)
        try:
            import web_server
            web_server.ACTIVE_EXECUTION["status"] = "failed"
            web_server.ACTIVE_EXECUTION["summary"] = result_text
        except Exception:
            pass
        print(synthesize_result(task_plan.as_dict(), provider.as_dict(), result_text, debug))
        return

    runtime = TaskRuntime()
    runtime_plan = runtime.build_plan(goal, report.draft_actions)
    if debug:
        print("\nTaskRuntime plan preview:")
        for call, decision in zip(runtime_plan.calls, runtime.inspect_plan(runtime_plan)):
            print(json.dumps({"tool": call.tool, "payload": call.payload, "policy": decision.__dict__}, ensure_ascii=False, indent=2))

    runtime_report = runtime.execute_plan(
        runtime_plan,
        approval_callback=approval_callback or _cli_approval,
        dev_auto_approve=auto_approve_risky,
    )
    update_web_server_status(runtime_report)
    result_text = format_local_agent_report(report) + "\n\n" + _format_runtime_report(runtime_report)
    print(synthesize_result(task_plan.as_dict(), provider.as_dict(), result_text, debug))
    return


def run_brain_health_check(browser_target: str | None = None, timeout: int | None = None) -> None:
    target = browser_target or config.ORCHESTRATOR_BRAIN_TARGET
    print(f"\nBrowser brain health check basliyor: target={target}")
    result = browser_brain_health_check(target=target, timeout=timeout)
    status = "OK" if result.get("ready") else "HATA"
    print(f"Durum: {status}")
    print(f"Mesaj: {result.get('message')}")
    print(f"Site acildi: {result.get('site_opened')}")
    print(f"Login/input hazir: {result.get('login_ready') or result.get('prompt_input_found')}")
    print(f"Modal/rate-limit: {result.get('blocking_modal')}")
    print(f"Cevap alindi: {result.get('answer_received')}")
    if result.get("answer_preview"):
        print(f"Cevap onizleme: {result.get('answer_preview')}")
    if not result.get("ready"):
        print("ChatGPT tarayicida giris yap, sonra tekrar dene.")


def run_local_model_health_check() -> None:
    report = local_model_health_check()
    print("\nLOCAL MODEL HEALTH CHECK")
    print(f"Provider: {config.LOCAL_MODEL_PROVIDER}")
    for name in ["ollama", "lmstudio"]:
        item = report[name]
        status = "OK" if item.get("ok") else "HATA"
        print(f"- {name}: {status} | {item.get('base_url')}")
        if item.get("models"):
            print(f"  Modeller: {', '.join(item['models'])}")
        if item.get("error"):
            print(f"  Hata: {item['error']}")
        
        # User-friendly explanation and solution
        if not item.get("ok"):
            friendly_name = "LM Studio" if name == "lmstudio" else "Ollama"
            print(f"\n{friendly_name}: bağlantı yok")
            print("Çözüm:")
            if name == "lmstudio":
                print("1. LM Studio’yu aç")
                print(f"2. İlgili modeli yükle")
                print("3. Local Server / OpenAI Compatible API özelliğini başlat")
            else:
                print("1. Ollama'yı çalıştır")
                print("2. İlgili modeli yükle")
            print("4. Tekrar çalıştır:")
            print("   .venv\\Scripts\\python.exe bridge.py --local-model-health-check")
            print()

    for line in report["summary"]:
        print(line)


def run_local_model_benchmark() -> None:
    report = benchmark_local_models()
    print("\nLOCAL MODEL BENCHMARK")
    print(f"Provider: {report['provider']}")
    print(f"Average response time: {report['average_response_time_seconds']}s")
    print(f"JSON success rate: {report['json_success_rate']:.1%}")
    print("\n--- Model Decisions ---")
    for model_name, decision in report.get("model_decisions", {}).items():
        print(f"\nModel: {model_name}")
        print(f"  Ortalama Cevap Süresi: {decision['average_response_time_seconds']}s")
        print(f"  JSON Başarı Oranı: {decision['json_success_rate']:.1%}")
        print(f"  Kalite Skoru (1-5): {decision['quality_score']}")
        print(f"  Önerilen Rol: {decision['recommended_role']}")
        print(f"  Ana Akışta Kullanılsın mı?: {'Evet' if decision['should_use_in_main_flow'] else 'Hayır'}")
        print(f"  Gerekçe: {decision['rationale']}")
        
        final_dec = decision.get("final_decision", {})
        print(f"  --- Final Kararlar ---")
        print(json.dumps(final_dec, ensure_ascii=False, indent=2))

    print("\n--- Detaylı Test Sonuçları ---")
    for row in report["results"]:
        print(
            f"- {row['model_name']} | {row['role']} | rec_role={row.get('recommended_role')} | {row['test']} | "
            f"{row['response_time_seconds']}s | json={row['valid_json']} | "
            f"quality={row['answer_quality']} | status={row['status']} | {row['notes']}"
        )



def main() -> None:
    print(BANNER)
    parser = argparse.ArgumentParser(description="Antigravity Otonom Ajan Koprusu")
    parser.add_argument("--goal", type=str, help="Otonom olarak gerceklestirilecek hedef")
    parser.add_argument("--iterations", type=int, default=15, help="Maksimum iterasyon sayisi")
    parser.add_argument("--route-only", action="store_true", help="Sadece router sonucunu yazdir, model/agent calistirma")
    parser.add_argument(
        "--auto-approve-risky",
        action="store_true",
        help="Riskli local agent aksiyonlarini sormadan onayla (onerilmez)",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Local agent aksiyonlarini taslakla ve critic'e kontrol ettir, uygulama",
    )
    parser.add_argument(
        "--provider",
        choices=["api", "browser", "groq", "browser_gpt", "local_model"],
        help="Bu calistirma icin chat/content provider override.",
    )
    parser.add_argument(
        "--browser-target",
        choices=["chatgpt", "claude", "gemini", "groq", "perplexity"],
        help="--provider browser kullanilirken hedef AI web arayuzu.",
    )
    parser.add_argument(
        "--browser-timeout",
        type=int,
        help="Browser provider icin bu calistirmaya ozel timeout saniyesi.",
    )
    parser.add_argument("--debug", action="store_true", help="Task plan/provider/secim detaylarini yazdir.")
    parser.add_argument("--brain-health-check", action="store_true", help="Browser brain oturum ve cevap testini calistir.")
    parser.add_argument("--login-browser-brain", action="store_true", help="Browser brain hedefini acip login/cevap hazirligini test et.")
    parser.add_argument("--local-model-health-check", action="store_true", help="Ollama/LM Studio local model sagligini test et.")
    parser.add_argument("--benchmark-local-models", action="store_true", help="Local modeller icin benchmark testlerini calistir.")
    parser.add_argument("--memory-last", type=int, help="Son N gorevi goster")
    parser.add_argument("--memory-search", type=str, help="Gecmis gorevlerde ara")
    parser.add_argument("--interactive", action="store_true", help="Etkilesimli terminal modunu baslat.")
    args = parser.parse_args()
    if args.interactive:
        import app
        app.run_interactive_shell()
        return

    if args.auto_approve_risky:
        config.DEV_MODE = True

    if args.provider:
        config.CHAT_PROVIDER = args.provider
        if args.provider in {"browser", "browser_gpt"}:
            config.WEB_QUERY_PROVIDER = args.provider
    if args.browser_target:
        config.CHAT_BROWSER_TARGET = args.browser_target
    if args.browser_timeout:
        config.BROWSER_PROVIDER_TIMEOUT = args.browser_timeout

    if args.memory_last is not None:
        import task_memory
        task_memory.print_last_runs(args.memory_last)
        return
    if args.memory_search is not None:
        import task_memory
        task_memory.search_runs(args.memory_search)
        return

    if args.brain_health_check or args.login_browser_brain:
        run_brain_health_check(args.browser_target, args.browser_timeout)
        return
    if args.local_model_health_check:
        run_local_model_health_check()
        return
    if args.benchmark_local_models:
        run_local_model_benchmark()
        return


    if args.goal:
        run_bridge_autonomous_loop(
            args.goal.strip(),
            args.iterations,
            route_only=args.route_only,
            auto_approve_risky=args.auto_approve_risky,
            plan_only=args.plan_only,
            provider_override=args.provider,
            browser_target=args.browser_target,
            debug=args.debug,
        )
        return

    try:
        goal = input("\nAjanlara yaptirmak istediginiz hedefi girin:\n> ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCikis yapildi.")
        return

    if not goal:
        print("Gecersiz hedef.")
        return
    run_bridge_autonomous_loop(
        goal,
        args.iterations,
        route_only=args.route_only,
        auto_approve_risky=args.auto_approve_risky,
        plan_only=args.plan_only,
        provider_override=args.provider,
        browser_target=args.browser_target,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
