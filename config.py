# -*- coding: utf-8 -*-
"""
config.py — Merkezi Konfigürasyon Modülü
==========================================
Tüm API anahtarları, model seçimleri, klasör yolları ve güvenlik ayarları.
Değerler .env dosyasından okunur, yoksa varsayılanlar kullanılır.
"""

import os
import sys
import logging
from pathlib import Path

# ─────────────────────────────────────────────
# .env dosyasını yükle (varsa)
# ─────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass  # python-dotenv yoksa çevre değişkenleri doğrudan okunur

# ─────────────────────────────────────────────
# PROJE YOLLARI
# ─────────────────────────────────────────────
env_path = Path(__file__).with_name(".env")
if env_path.exists():
    with env_path.open("r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

PROJECT_ROOT = os.getenv(
    "PROJECT_ROOT",
    os.path.dirname(os.path.abspath(__file__))
)
STRATEGY_FILE = os.path.join(PROJECT_ROOT, "strategy.py")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
BACKUP_DIR = os.path.join(PROJECT_ROOT, "backups")

# Dizinleri oluştur
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# API ANAHTARLARI
# ─────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")  # Opsiyonel yedek anahtar

# Model saglayicilar:
#   api         -> API saglayici (su an Groq backend)
#   browser     -> AI web arayuzu (ChatGPT, Claude, Gemini, Groq, Perplexity)
#   local_tool  -> Sadece yerel tool executor
#   groq/browser_gpt eski isimler olarak desteklenir.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")

# Cost-aware hybrid roles:
# planner/critic use browser GPT; coder/action drafting uses Groq Llama 70B.
PLANNER_PROVIDER = os.getenv("PLANNER_PROVIDER", "browser_gpt")
CODER_PROVIDER = os.getenv("CODER_PROVIDER", "groq")
CRITIC_PROVIDER = os.getenv("CRITIC_PROVIDER", "browser_gpt")

# General-purpose orchestrator roles.
CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", LLM_PROVIDER)
ROUTER_PROVIDER = os.getenv("ROUTER_PROVIDER", LLM_PROVIDER)
PROMPT_ARCHITECT_PROVIDER = os.getenv("PROMPT_ARCHITECT_PROVIDER", PLANNER_PROVIDER)
LOCAL_AGENT_PROVIDER = os.getenv("LOCAL_AGENT_PROVIDER", CODER_PROVIDER)
WEB_QUERY_PROVIDER = os.getenv("WEB_QUERY_PROVIDER", "browser_gpt")
RESEARCH_PROVIDER = os.getenv("RESEARCH_PROVIDER", WEB_QUERY_PROVIDER)
DEFAULT_CHAT_PROVIDER = os.getenv("DEFAULT_CHAT_PROVIDER", CHAT_PROVIDER)
DEFAULT_RESEARCH_PROVIDER = os.getenv("DEFAULT_RESEARCH_PROVIDER", RESEARCH_PROVIDER)
DEFAULT_CODING_PROVIDER = os.getenv("DEFAULT_CODING_PROVIDER", CODER_PROVIDER)
DEFAULT_AGENT_PROVIDER = os.getenv("DEFAULT_AGENT_PROVIDER", LOCAL_AGENT_PROVIDER)
DEFAULT_BROWSER_TARGET = os.getenv("DEFAULT_BROWSER_TARGET", "chatgpt")
ORCHESTRATOR_BRAIN_PROVIDER = os.getenv("ORCHESTRATOR_BRAIN_PROVIDER", "browser")
ORCHESTRATOR_BRAIN_TARGET = os.getenv("ORCHESTRATOR_BRAIN_TARGET", DEFAULT_BROWSER_TARGET)
ORCHESTRATOR_BRAIN_FALLBACK_PROVIDER = os.getenv("ORCHESTRATOR_BRAIN_FALLBACK_PROVIDER", "api")
ORCHESTRATOR_BRAIN_FALLBACK_MODEL = os.getenv("ORCHESTRATOR_BRAIN_FALLBACK_MODEL", "llama-3.1-8b-instant")
ORCHESTRATOR_BRAIN_USE_LLM = os.getenv("ORCHESTRATOR_BRAIN_USE_LLM", "true").lower() == "true"
WORKSPACE_ANALYSIS_PROVIDER = os.getenv("WORKSPACE_ANALYSIS_PROVIDER", "api")


# Local model providers (optional; never required for the main flow).
LOCAL_MODEL_PROVIDER = os.getenv("LOCAL_MODEL_PROVIDER", "lmstudio")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LMSTUDIO_BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
LOCAL_FAST_MODEL = os.getenv("LOCAL_FAST_MODEL", "Qwen3.5-4B-Q4_K_M")
LOCAL_REASONER_MODEL = os.getenv("LOCAL_REASONER_MODEL", "DeepSeek-R1-Distill-Llama-8B-Q4_K_M")
LOCAL_CODER_MODEL = os.getenv("LOCAL_CODER_MODEL", "Qwen3.5-4B-Q4_K_M")
USE_LOCAL_FAST = os.getenv("USE_LOCAL_FAST", "false").lower() == "true"
USE_LOCAL_CRITIC = os.getenv("USE_LOCAL_CRITIC", "false").lower() == "true"
USE_LOCAL_CODER = os.getenv("USE_LOCAL_CODER", "false").lower() == "true"
USE_LOCAL_ROUTER = os.getenv("USE_LOCAL_ROUTER", "false").lower() == "true"
LOCAL_MODEL_TIMEOUT_SECONDS = int(os.getenv("LOCAL_MODEL_TIMEOUT_SECONDS", "20"))
LOCAL_MODEL_MAX_TOKENS = int(os.getenv("LOCAL_MODEL_MAX_TOKENS", "800"))
DISABLE_SLOW_LOCAL_AFTER_FAILURES = int(os.getenv("DISABLE_SLOW_LOCAL_AFTER_FAILURES", "3"))
LOCAL_MODEL_MAX_FAILURES = int(os.getenv("LOCAL_MODEL_MAX_FAILURES", "3"))
LOCAL_MODEL_FAILURES = 0
LOCAL_MODEL_SLOW_COUNT = 0
LAST_RUN_TOOLS = []
LAST_RUN_APPROVAL_REQUIRED = False
LAST_RUN_APPROVAL_GRANTED = False
LAST_RUN_BLOCKED_BY_PLAN_ONLY = False
LAST_RUN_APPROVAL_WOULD_BE_REQUIRED = False
LAST_RUN_FILE_OPERATION_TYPE = ""
LAST_RUN_SOURCE_REDACTED = ""
LAST_RUN_TARGET_REDACTED = ""
LAST_RUN_APPLICATION_NAME = ""
LAST_RUN_APPLICATION_ACTION_TYPE = ""  # launch or diagnostics
LAST_RUN_REGISTRY_MATCH_CONFIDENCE = 0.0
LAST_RUN_REGISTRY_VERIFIED = False
LAST_RUN_LAUNCH_TYPE = ""
LAST_RUN_DIAGNOSTIC_STATUS = ""
LAST_RUN_EVIDENCE_COUNT = 0
LAST_RUN_ACTIONS_EXECUTED_COUNT = 0
LAST_RUN_REMEDIATION_ACTION_TYPE = ""
LAST_RUN_TARGET_PROCESS_NAMES_REDACTED = ""
LAST_RUN_TARGET_PIDS_COUNT = 0
LAST_RUN_TARGET_PATHS_REDACTED = ""
LAST_RUN_DIAGNOSTIC_REPORT_LINKED = False
INTERACTIVE_MODE = False
PLAN_ONLY = False


# ─────────────────────────────────────────────
# MODEL SEÇİMLERİ
# ─────────────────────────────────────────────
PM_MODEL = os.getenv("PM_MODEL", "llama-3.3-70b-versatile")
CODER_MODEL = os.getenv("CODER_MODEL", "llama-3.3-70b-versatile")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "llama-3.3-70b-versatile")
SECURITY_MODEL = os.getenv("SECURITY_MODEL", "llama-3.3-70b-versatile")
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.1-8b-instant")

CHAT_MODEL = os.getenv("CHAT_MODEL", CODER_MODEL)
ROUTER_MODEL = os.getenv("ROUTER_MODEL", CHAT_MODEL)
PROMPT_ARCHITECT_MODEL = os.getenv("PROMPT_ARCHITECT_MODEL", PM_MODEL)
LOCAL_AGENT_MODEL = os.getenv("LOCAL_AGENT_MODEL", CODER_MODEL)

# Browser GPT bridge
BROWSER_GPT_URL = os.getenv("BROWSER_GPT_URL", "https://chatgpt.com")
BROWSER_GPT_TIMEOUT = int(os.getenv("BROWSER_GPT_TIMEOUT", "180"))
BROWSER_PROVIDER_TIMEOUT = int(os.getenv("BROWSER_PROVIDER_TIMEOUT", str(BROWSER_GPT_TIMEOUT)))
BROWSER_PROVIDER_API_FALLBACK = os.getenv("BROWSER_PROVIDER_API_FALLBACK", "false").lower() == "true"
CHAT_BROWSER_TARGET = os.getenv("CHAT_BROWSER_TARGET", "chatgpt")
RESEARCH_BROWSER_TARGET = os.getenv("RESEARCH_BROWSER_TARGET", "perplexity")
BROWSER_GPT_AUTO_START = os.getenv("BROWSER_GPT_AUTO_START", "true").lower() == "true"
BROWSER_GPT_CLOSE_AFTER = os.getenv("BROWSER_GPT_CLOSE_AFTER", "false").lower() == "true"
BROWSER_GPT_PROFILE_DIR = os.getenv(
    "BROWSER_GPT_PROFILE_DIR",
    os.path.join(PROJECT_ROOT, ".browser-profile", "chatgpt"),
)
CHROME_PATH = os.getenv("CHROME_PATH", "")

# Basit bilgi/cevap isteklerinde dosya yazan otonom donguye girme.
DIRECT_ANSWER_MODE = os.getenv("DIRECT_ANSWER_MODE", "true").lower() == "true"
ROUTER_USE_LLM = os.getenv("ROUTER_USE_LLM", "false").lower() == "true"

# ─────────────────────────────────────────────
# PM AJANI MODU
# ─────────────────────────────────────────────
# "groq"    → Groq API üzerinden PM çalışır (önerilen)
# "browser" → Playwright ile ChatGPT/Gemini web arayüzüne bağlanır
PM_MODE = os.getenv("PM_MODE", "groq")

# ─────────────────────────────────────────────
# BROWSER AYARLARI (PM_MODE="browser" için)
# ─────────────────────────────────────────────
CHROME_DEBUG_PORT = int(os.getenv("CHROME_DEBUG_PORT", "9222"))
# "chatgpt" veya "gemini"
BROWSER_TARGET = os.getenv("BROWSER_TARGET", "chatgpt")

# ─────────────────────────────────────────────
# RATE LIMIT & TIMEOUT
# ─────────────────────────────────────────────
RATE_LIMIT_SLEEP = float(os.getenv("RATE_LIMIT_SLEEP", "2.0"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "120"))
SUBPROCESS_TIMEOUT = int(os.getenv("SUBPROCESS_TIMEOUT", "300"))

# Official runtime safety defaults.  The normal application must remain
# fail-closed: developer overrides and executable generated code are disabled
# unless deliberately enabled in a local development environment.
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
AUDIT_LOG_FILE = os.getenv("AUDIT_LOG_FILE", os.path.join(LOG_DIR, "audit.jsonl"))
MAX_READ_FILE_CHARS = int(os.getenv("MAX_READ_FILE_CHARS", "12000"))
MAX_WRITE_FILE_CHARS = int(os.getenv("MAX_WRITE_FILE_CHARS", "200000"))
MAX_DIFF_LINES = int(os.getenv("MAX_DIFF_LINES", "240"))
MAX_LISTED_FILES = int(os.getenv("MAX_LISTED_FILES", "400"))
# syntax_only parses a temporary copy in a separate Python process and never
# executes generated application code.  A hardened backend must be added before
# any broader code execution is enabled.
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "syntax_only").lower()
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "20"))
DIAGNOSTIC_TIMEOUT_SECONDS = int(os.getenv("DIAGNOSTIC_TIMEOUT_SECONDS", "12"))
WEB_SEARCH_TIMEOUT_SECONDS = int(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "12"))
WEB_SEARCH_MAX_RESPONSE_BYTES = int(os.getenv("WEB_SEARCH_MAX_RESPONSE_BYTES", "750000"))
EXPERIMENTAL_QUANT_WORKER = os.getenv("EXPERIMENTAL_QUANT_WORKER", "false").lower() == "true"
# Code or document contents are withheld from external API/browser providers
# unless the operator explicitly opts in. Paths and small redacted summaries
# may still be used for planning.
ALLOW_EXTERNAL_WORKSPACE_CONTENT = os.getenv("ALLOW_EXTERNAL_WORKSPACE_CONTENT", "false").lower() == "true"

# ─────────────────────────────────────────────
# GÜVENLİK AYARLARI
# ─────────────────────────────────────────────
ALLOWED_EXTENSIONS = [
    ".py", ".json", ".csv", ".txt", ".log", ".md", ".toml", ".yaml", ".yml",
    ".html", ".css", ".js", ".svg"
]

# Generic command execution is intentionally absent from the official runtime.
# Do not reintroduce blacklist-based shell filtering; add a reviewed typed tool
# with explicit arguments and policy instead.

# ─────────────────────────────────────────────
# ORKESTRATÖR AYARLARI
# ─────────────────────────────────────────────
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "50"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
STOP_ON_TEST_FAILURE = os.getenv("STOP_ON_TEST_FAILURE", "true").lower() == "true"

# İterasyon arası bekleme (saniye)
ITERATION_COOLDOWN = float(os.getenv("ITERATION_COOLDOWN", "5.0"))

# ─────────────────────────────────────────────
# LOGLAMA
# ─────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s | %(name)-12s | %(levelname)-7s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def setup_logging():
    """Merkezi loglama yapılandırması."""
    log_file = os.path.join(LOG_DIR, "orchestrator.log")

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Konsol handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    console_handler.setFormatter(
        logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    )

    # Dosya handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    )

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return root_logger


def validate_config(dry_run: bool = False):
    """Başlangıçta konfigürasyonu doğrula."""
    errors = []
    warnings = []

    valid_providers = ("api", "browser", "local_tool", "local_model", "groq", "browser_gpt")
    if LLM_PROVIDER not in valid_providers:
        errors.append(f"LLM_PROVIDER gecersiz: '{LLM_PROVIDER}'. Gecerli providerlar: {', '.join(valid_providers)}")

    role_providers = {
        "LLM_PROVIDER": LLM_PROVIDER,
        "PLANNER_PROVIDER": PLANNER_PROVIDER,
        "CODER_PROVIDER": CODER_PROVIDER,
        "CRITIC_PROVIDER": CRITIC_PROVIDER,
        "CHAT_PROVIDER": CHAT_PROVIDER,
        "ROUTER_PROVIDER": ROUTER_PROVIDER,
        "PROMPT_ARCHITECT_PROVIDER": PROMPT_ARCHITECT_PROVIDER,
        "LOCAL_AGENT_PROVIDER": LOCAL_AGENT_PROVIDER,
        "WEB_QUERY_PROVIDER": WEB_QUERY_PROVIDER,
        "RESEARCH_PROVIDER": RESEARCH_PROVIDER,
        "ORCHESTRATOR_BRAIN_PROVIDER": ORCHESTRATOR_BRAIN_PROVIDER,
    }
    for name, provider in role_providers.items():
        if provider not in valid_providers:
            errors.append(f"{name} gecersiz: '{provider}'. Gecerli providerlar: {', '.join(valid_providers)}")

    if any(provider in {"groq", "api"} for provider in role_providers.values()) and not GROQ_API_KEY:
        if dry_run:
            warnings.append("GROQ_API_KEY ayarlanmamis (dry-run modunda sorun degil)")
        else:
            errors.append("GROQ_API_KEY ayarlanmamis! .env dosyasini kontrol edin.")

    if PM_MODE not in ("groq", "browser"):
        errors.append(f"PM_MODE geçersiz: '{PM_MODE}'. 'groq' veya 'browser' olmalı.")

    if PM_MODE == "browser" and BROWSER_TARGET not in ("chatgpt", "gemini"):
        errors.append(
            f"BROWSER_TARGET geçersiz: '{BROWSER_TARGET}'. "
            "'chatgpt' veya 'gemini' olmalı."
        )

    if not os.path.isdir(PROJECT_ROOT):
        errors.append(f"PROJECT_ROOT dizini bulunamadi: {PROJECT_ROOT}")

    if errors:
        print("\n[HATA] KONFIGURASYON HATALARI:")
        for e in errors:
            print(f"   * {e}")
        return False

    if warnings:
        for w in warnings:
            print(f"   [UYARI] {w}")

    print(f"[OK] Konfigurasyon dogrulandi.")
    print(f"   Proje koku  : {PROJECT_ROOT}")
    print(f"   LLM provider: {LLM_PROVIDER}")
    print(f"   Planner     : {PLANNER_PROVIDER}")
    print(f"   Coder       : {CODER_PROVIDER}")
    print(f"   Critic      : {CRITIC_PROVIDER}")
    print(f"   Chat        : {CHAT_PROVIDER} / {CHAT_MODEL}")
    print(f"   Router      : {ROUTER_PROVIDER} / {ROUTER_MODEL}")
    print(f"   Architect   : {PROMPT_ARCHITECT_PROVIDER} / {PROMPT_ARCHITECT_MODEL}")
    print(f"   Local agent : {LOCAL_AGENT_PROVIDER} / {LOCAL_AGENT_MODEL}")
    print(f"   Web query   : {WEB_QUERY_PROVIDER} / target={RESEARCH_BROWSER_TARGET}")
    print(f"   Brain       : {ORCHESTRATOR_BRAIN_PROVIDER} / target={ORCHESTRATOR_BRAIN_TARGET}")
    print(f"   Local model : {LOCAL_MODEL_PROVIDER} / coder={LOCAL_FAST_MODEL} / reasoner={LOCAL_REASONER_MODEL}")
    print(f"   PM modu     : {PM_MODE}")
    print(f"   PM model    : {PM_MODEL}")
    print(f"   Coder model : {CODER_MODEL}")
    print(f"   Critic model: {CRITIC_MODEL}")
    print(f"   Dry-run     : {DRY_RUN}")
    return True
