# -*- coding: utf-8 -*-
"""Message router for the general-purpose Antigravity orchestrator."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

import config
from security_policy import classify_risk
from agents import orchestrator_brain_chat, router_chat
from tools.registry import registered_tools


CATEGORIES = {
    "conversation",
    "content_generation",
    "research",
    "coding",
    "local_computer_action",
    "file_workspace_task",
    "browser_model_task",
    "multi_step_agent_task",
    "weather_query",
}


@dataclass
class RouteResult:
    category: str
    confidence: float
    reason: str
    needs_web: bool = False
    needs_local_agent: bool = False
    risk: str = "low"
    intent_type: str = "INFORMATION_REQUEST"
    metadata: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> str:
    cleaned = (text or "").replace("\ufeff", "").replace("ï»¿", "")
    import unicodedata
    # Decompose character accents and filter out Mn category (non-spacing marks)
    decomposed = unicodedata.normalize("NFD", cleaned)
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    
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
        "Ã§": "c",
        "Ã¼": "u",
        "ÄŸ": "g",
        "ÅŸ": "s",
        "Ã¶": "o",
        "Ä±": "i",
        "Ã‡": "c",
        "Ãœ": "u",
        "Ä°": "i",
        "ã§": "c",
        "ã¼": "u",
        "äÿ": "g",
        "åÿ": "s",
        "ã¶": "o",
        "ä±": "i",
        "ã‡": "c",
        "ãœ": "u",
        "ä°": "i"
    }
    lowered = without_accents.lower().replace("ı", "i")
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    return lowered.strip()


def _has_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


WEB_PATTERNS = [
    r"\bbugun\b",
    r"\bsu an\b",
    r"\bguncel\b",
    r"\bson dakika\b",
    r"\bhaber\b",
    r"\bhava\b",
    r"\bfiyat\b",
    r"\bkur\b",
    r"\bdolar\b",
    r"\beuro\b",
    r"\bborsa\b",
    r"\bkim kazandi\b",
    r"\bne kadar\b.*\b202[0-9]\b",
]

PUBLIC_SEARCH_PATTERNS = [
    r"\b(?:webde|web'de|internette|google(?:da)?|bing(?:de)?)\s+ara\b",
    r"\b(?:belge|belgeleri|kaynak|kaynaklari|kaynakları)\w*\s+ara\b",
    r"\bpublic\s+(?:web\s+)?ara\b",
    r"\bweb\s+aramasi\b",
]

CONTENT_GENERATION_PATTERNS = [
    r"\b\d{3,6}\s+kelime",
    r"\bmakale\b.*\byaz\b",
    r"\bakademik\b.*\byaz\b",
    r"\bparagraf\b.*\byaz\b",
    r"\bcv\b.*\b(olustur|yaz|hazirla)\b",
    r"\bmetin\b.*\b(olustur|yaz)\b",
    r"\byazi\b.*\byaz\b",
    r"\bessay\b",
    r"\biçerik\b.*\b(olustur|yaz)\b",
    r"\bicerik\b.*\b(olustur|yaz)\b",
    r"\bmail\b.*\btasla(?:k|g)\w*\b",
    r"\btasla(?:k|g)\w*\b.*\b(olustur|yaz|hazirla)\b",
]

ACTION_VERB_PATTERNS = [
    r"\bkur\b",
    r"\bolustur\b",
    r"\byap\b",
    r"\bcalistir\b",
    r"\bincele\b",
    r"\btara\b",
    r"\bac\b",
    r"\bindir\b",
    r"\byukle\b",
    r"\bduzenle\b",
    r"\bkaydet\b",
    r"\bgonder\b",
    r"\barastir\b",
    r"\banaliz et\b",
    r"\bklasor\b.*\bolustur\b",
    r"\bdosya\b.*\byaz\b",
    r"\balarm\b.*\bkur\b",
    r"\bchrome\b.*\bac\b",
    r"\bpdf\b.*\bincele\b",
    r"\bbot\b.*\bcalistir\b",
]

INFO_PATTERNS = [
    r"\bnedir\b",
    r"\bne demek\b",
    r"\banlat\b",
    r"\bacikla\b",
    r"\bozetle\b",
    r"\bnasilsin\b",
    r"\bkim\b",
    r"\bneden\b",
]

CODING_PATTERNS = [
    r"\bkod\b",
    r"\bscript\b",
    r"\bprogram\b",
    r"\buygulama\b",
    r"\bweb sitesi\b",
    r"\bsite\b.*\byap\b",
    r"\bhtml\b",
    r"\bcss\b",
    r"\bjavascript\b",
    r"\bpython\b",
    r"\bapi\b",
    r"\bdebug\b",
    r"\brefactor\b",
]

FILE_PATTERNS = [
    r"\bdosya\w*\b",
    r"\bklasor\w*\b",
    r"\bolustur\b",
    r"\byaz\b",
    r"\boku\b",
    r"\bduzenle\b",
    r"\bhata\w*\b",
    r"\btasi\b",
    r"\bsil\b",
    r"\bzip\b",
    r"\bexcel\b",
    r"\bcsv\b",
]

LOCAL_AGENT_PATTERNS = [
    r"\bprojeyi incele\b",
    r"\bhatala?r[ıi]?\b.*\bduzelt\b",
    r"\bklasor\w*\b.*\bincele\b",
    r"\bterminal\b",
    r"\bcalistir\b",
    r"\btest et\b",
    r"\bkur\b",
    r"\blocal\b",
    r"\bbilgisayarimdaki\b",
]

PLANNING_PATTERNS = [
    r"\bplan\b",
    r"\bplani\b",
    r"\byol haritasi\b",
    r"\badim adim\b",
    r"\bmimari\b",
    r"\btasarim\b",
    r"\bstrateji\b",
    r"\bkompleks\b",
]

RISK_PATTERNS = [
    r"\bsil\b",
    r"\bformat\b",
    r"\bkur\b",
    r"\buninstall\b",
    r"\bapi key\b",
    r"\b.env\b",
    r"\bmail gonder\b",
    r"\bodeme\b",
    r"\bhesap\b",
    r"\bsistem ayari\b",
]


def detect_intent_type(message: str) -> tuple[str, str, float]:
    text = _normalize(message)
    if _has_any(text, ACTION_VERB_PATTERNS):
        return (
            "ACTION_REQUEST",
            "Kullanici bilgi istemiyor, sistemden eylem gerceklestirmesini istiyor.",
            0.91,
        )
    if _has_any(text, INFO_PATTERNS):
        return (
            "INFORMATION_REQUEST",
            "Kullanici bilgi, aciklama veya sohbet cevabi istiyor.",
            0.84,
        )
    return (
        "INFORMATION_REQUEST",
        "Emir/gorev fiili tespit edilmedi; bilgi/sohbet istegi varsayildi.",
        0.65,
    )


def is_weather_query(message: str) -> bool:
    text = _normalize(message)
    weather_phrases = ["hava durumu", "hava nasil", "weather forecast", "haftalik hava", "gunluk hava"]
    if any(phrase in text for phrase in weather_phrases):
        return True
    
    if "hava" in text:
        weather_terms = {"tahmin", "sicaklik", "derece", "yagis", "nasil", "olacak", "hafta", "gun", "meteoroloji", "forecast"}
        if any(term in text for term in weather_terms):
            return True
            
    if "weather" in text or "forecast" in text:
        return True
        
    return False


def is_alarm_query(message: str) -> bool:
    text = _normalize(message)
    return "alarm" in text or "hatirlatici" in text or "hatirlat" in text or "reminder" in text


def classify_message(message: str, route_only: bool = False, plan_only: bool = False) -> RouteResult:
    """Classify locally by default; an LLM router is an explicit opt-in."""
    # 1. Clean up numbered/list prefix
    cleaned_message = re.sub(r"^\s*(?:\d+[\.\)]|\[\d+\])\s*", "", message)

    # 2. Check deterministic routes early
    if is_small_talk(cleaned_message):
        return apply_guardrails(cleaned_message, RouteResult(
            category="conversation",
            confidence=1.0,
            reason="Small talk detected.",
            risk="low",
            intent_type="small_talk"
        ))

    if is_weather_query(cleaned_message):
        return apply_guardrails(cleaned_message, RouteResult(
            category="weather_query",
            confidence=1.0,
            reason="Hava durumu tahmini sorgusu.",
            risk="low",
            intent_type="weather_query"
        ))

    if is_alarm_query(cleaned_message):
        return apply_guardrails(cleaned_message, RouteResult(
            category="local_computer_action",
            confidence=1.0,
            reason="Alarm/reminder request, scheduler tool not connected.",
            risk="low",
            intent_type="unsupported_alarm_or_reminder"
        ))

    if getattr(config, "ROUTER_USE_LLM", False):
        if (route_only or plan_only) and config.ROUTER_PROVIDER in {"browser", "browser_gpt"}:
            return apply_guardrails(cleaned_message, deterministic_classify(cleaned_message))
        try:
            routed = apply_guardrails(cleaned_message, classify_with_llm(cleaned_message))
            if routed.category in CATEGORIES:
                return routed
        except Exception as exc:
            fallback = deterministic_classify(cleaned_message)
            fallback.reason = f"LLM classifier calisamadi; deterministic fallback kullanildi. Sebep: {_compact_error(exc)}. {fallback.reason}"
            return apply_guardrails(cleaned_message, fallback)

    return apply_guardrails(cleaned_message, deterministic_classify(cleaned_message))


def is_small_talk(message: str) -> bool:
    text = _normalize(message)
    # Remove punctuation
    text_clean = re.sub(r"[.,\/#!$%\^&\*;:{}=\-_`~()?]", " ", text)
    words = text_clean.split()
    if not words:
        return False
        
    greeting_words = {
        "selam", "selamlar", "merhaba", "merhabalar", "naber", "nasilsin", 
        "hi", "hello", "hey", "gunaydin", "iyi", "aksamlar", "geceler", 
        "gunler", "nasil", "gidiyor", "tesekkur", "tesekkurler", "sagol", 
        "sagolasin", "gorusuruz", "hosca", "kal", "bye", "meraba"
    }
    
    auxiliary_words = {
        "de", "da", "sen", "ben", "ne", "haber", "ederim", "neler", 
        "var", "yok", "bizden", "senden", "benden", "ve", "en", "daha"
    }
    
    task_keywords = {
        "dosya", "klasor", "tasi", "sil", "kopyala", "ac", "baslat", "calistir",
        "kod", "yaz", "script", "program", "hata", "duzelt", "bak", "incele",
        "olustur", "hava", "weather", "forecast", "mgm", "sicaklik", "tahmin",
        "search", "ara", "bul", "google", "chrome", "notepad", "hesap", "takvim"
    }
    
    # Greeting + task does not route to small talk
    for w in words:
        if w in task_keywords:
            return False
            
    all_small_talk = True
    for w in words:
        if w not in greeting_words and w not in auxiliary_words:
            all_small_talk = False
            break
            
    if all_small_talk:
        return True
        
    matching_greetings = [w for w in words if w in greeting_words]
    if len(matching_greetings) >= 1 and len(words) <= 3:
        if not any(w in task_keywords for w in words):
            return True
            
    return False


def deterministic_classify(message: str) -> RouteResult:
    """Guardrail/fallback classifier. This is not the primary router."""
    if is_small_talk(message):
        return RouteResult(
            category="conversation",
            confidence=1.0,
            reason="Small talk detected.",
            risk="low",
            intent_type="small_talk"
        )

    if is_weather_query(message):
        return RouteResult(
            category="weather_query",
            confidence=1.0,
            reason="Hava durumu tahmini sorgusu.",
            risk="low",
            intent_type="weather_query"
        )

    text = _normalize(message)
    risk = classify_risk(message)
    intent_type, intent_reason, intent_confidence = detect_intent_type(message)

    if (
        ("chatgpt" in text or "claude" in text or "gemini" in text or "perplexity" in text or "groq" in text)
        and ("uzerinden" in text or "tarayici" in text or "sor" in text)
    ):
        return RouteResult(
            category="browser_model_task",
            confidence=0.9,
            reason="Kullanici belirli bir browser AI arayuzu uzerinden islem istiyor.",
            risk=risk,
            intent_type="ACTION_REQUEST",
        )

    if _has_any(text, CONTENT_GENERATION_PATTERNS):
        if _has_any(text, WEB_PATTERNS) or "kaynak" in text:
            return RouteResult(
                category="research",
                confidence=0.86,
                reason="Kullanici kaynak/guncel bilgi ile destekli icerik istiyor.",
                needs_web=True,
                risk=risk,
                intent_type="INFORMATION_REQUEST",
            )
        return RouteResult(
            category="content_generation",
            confidence=0.88,
            reason="Kullanici dosya islemi degil, uzun/biçimli metin uretimi istiyor.",
            risk=risk,
            intent_type="INFORMATION_REQUEST",
        )

    if _has_any(text, PUBLIC_SEARCH_PATTERNS):
        return RouteResult(
            category="research",
            confidence=0.9,
            reason="Kullanici public web kaynaklarinda arama istiyor.",
            needs_web=True,
            risk=risk,
            intent_type="INFORMATION_REQUEST",
        )

    if intent_type == "ACTION_REQUEST":
        if "calendar" in text or "takvim" in text or "etkinlik" in text:
            return RouteResult(
                category="local_computer_action",
                confidence=max(0.84, intent_confidence),
                reason="Kullanici takvim/etkinlik olusturma gibi yerel veya browser destekli bir eylem istiyor.",
                needs_local_agent=True,
                risk=risk,
                intent_type=intent_type,
                metadata={
                    "known_tool_available": False,
                    "missing_tool": "create_calendar_event",
                    "fallback_options": [
                        "Google Calendar'i tarayicida acip kullanici onayi ile islem yapmak",
                        ".ics takvim dosyasi olusturmak",
                        "calendar tool eklemek",
                    ],
                },
            )
        if _has_any(text, CONTENT_GENERATION_PATTERNS):
            return RouteResult(
                category="content_generation",
                confidence=max(0.84, intent_confidence),
                reason="Kullanici gercek sistem eylemi degil, metin/taslak uretimi istiyor.",
                risk=risk,
                intent_type=intent_type,
            )
        if "chatgpt" in text or "claude" in text or "gemini" in text or "perplexity" in text:
            return RouteResult(
                category="browser_model_task",
                confidence=max(0.88, intent_confidence),
                reason="Kullanici gorevin browser AI arayuzu uzerinden yapilmasini istiyor.",
                needs_local_agent=False,
                risk=risk,
                intent_type=intent_type,
            )
        if _has_any(text, PLANNING_PATTERNS) and _has_any(text, LOCAL_AGENT_PATTERNS + FILE_PATTERNS):
            return RouteResult(
                category="multi_step_agent_task",
                confidence=max(0.84, intent_confidence),
                reason="Kullanici workspace/proje uzerinde cok adimli ajan gorevi istiyor.",
                needs_local_agent=True,
                risk=risk,
                intent_type=intent_type,
            )
        if _has_any(text, CODING_PATTERNS):
            return RouteResult(
                category="coding",
                confidence=max(0.86, intent_confidence),
                reason=intent_reason,
                needs_local_agent=True,
                risk=risk,
                intent_type=intent_type,
            )
        if _has_any(text, FILE_PATTERNS):
            return RouteResult(
                category="file_workspace_task",
                confidence=max(0.86, intent_confidence),
                reason=intent_reason,
                needs_local_agent=True,
                risk=risk,
                intent_type=intent_type,
            )
        return RouteResult(
            category="local_computer_action",
            confidence=intent_confidence,
            reason=intent_reason,
            needs_local_agent=True,
            risk=risk,
            intent_type=intent_type,
        )

    if _has_any(text, WEB_PATTERNS):
        return RouteResult(
            category="research",
            confidence=0.82,
            reason="Mesaj guncel internet bilgisi gerektiriyor gibi gorunuyor.",
            needs_web=True,
            risk=risk,
            intent_type=intent_type,
        )

    if intent_type == "INFORMATION_REQUEST" and _has_any(text, INFO_PATTERNS):
        return RouteResult(
            category="conversation",
            confidence=max(0.82, intent_confidence),
            reason=intent_reason,
            risk=risk,
            intent_type=intent_type,
        )

    if _has_any(text, FILE_PATTERNS):
        return RouteResult(
            category="file_workspace_task",
            confidence=0.78,
            reason="Mesaj dosya veya klasor islemi iceriyor.",
            needs_local_agent=True,
            risk=risk,
            intent_type=intent_type,
        )

    if _has_any(text, LOCAL_AGENT_PATTERNS):
        return RouteResult(
            category="local_computer_action",
            confidence=0.78,
            reason="Mesaj yerel proje/terminal/inceleme aksiyonu istiyor.",
            needs_local_agent=True,
            risk=risk,
            intent_type=intent_type,
        )

    if _has_any(text, CODING_PATTERNS):
        return RouteResult(
            category="coding",
            confidence=0.8,
            reason="Mesaj kod veya uygulama gelistirme istegi iceriyor.",
            needs_local_agent=True,
            risk=risk,
            intent_type=intent_type,
        )

    if _has_any(text, PLANNING_PATTERNS) or len(text.split()) > 35:
        return RouteResult(
            category="content_generation",
            confidence=0.7,
            reason="Mesaj uzun cevap/plan uretimi gerektiriyor; dosya veya local islem belirtilmedi.",
            risk=risk,
            intent_type=intent_type,
        )

    return RouteResult(
        category="conversation",
        confidence=0.72,
        reason="Mesaj sohbet/aciklama/ozet tipi gorunuyor.",
        risk=risk,
        intent_type=intent_type,
    )


GUARDRAIL_ACTION_PATTERNS = [
    r"\bolustur\w*\b",
    r"\bekle\w*\b",
    r"\bkur\w*\b",
    r"\bac\w*\b",
    r"\bcalistir\w*\b",
    r"\bgonder\w*\b",
    r"\bduzenle\w*\b",
    r"\bsil\w*\b",
    r"\bindir\w*\b",
    r"\byukle\w*\b",
    r"\btakvim\w*\b",
    r"\betkinlik\w*\b",
    r"\balarm\w*\b",
    r"\bdosya\w*\b",
    r"\bklasor\w*\b",
    r"\bproje\w*\b",
    r"\bmail\w*\b",
    r"\bkodla\w*\b",
    r"\bara[st]tir\w*\b",
    r"\bfiyat\w*\b",
    r"\bhesab\w*\b",
    r"\bbilgisayar\w*\b",
]


def apply_guardrails(message: str, route: RouteResult) -> RouteResult:
    text = _normalize(message)
    if (
        ("chatgpt" in text or "claude" in text or "gemini" in text or "perplexity" in text or "groq" in text)
        and ("uzerinden" in text or "tarayici" in text or "sor" in text)
        and route.category == "conversation"
    ):
        return RouteResult(
            category="browser_model_task",
            confidence=0.82,
            reason="Guardrail: Kullanici belirli browser AI arayuzu uzerinden cevap istiyor; conversation olarak birakilmadi.",
            needs_web=False,
            needs_local_agent=False,
            risk=classify_risk(message),
            intent_type="ACTION_REQUEST",
            metadata=route.metadata,
        )
    if route.category != "conversation":
        return route
    if not _has_any(text, GUARDRAIL_ACTION_PATTERNS):
        return route

    metadata = dict(route.metadata or {})
    if "takvim" in text or "calendar" in text or "etkinlik" in text:
        metadata.update(
            {
                "known_tool_available": False,
                "missing_tool": "create_calendar_event",
                "fallback_options": [
                    "Google Calendar'i tarayicida acip kullanici onayi ile islem yapmak",
                    ".ics takvim dosyasi olusturmak",
                    "calendar tool eklemek",
                ],
            }
        )
        return RouteResult(
            category="local_computer_action",
            confidence=0.74,
            reason="Guardrail: eylem sinyali var; calendar tool eksik olsa da conversation olamaz.",
            needs_local_agent=True,
            risk=classify_risk(message),
            intent_type="ACTION_REQUEST",
            metadata=metadata,
        )

    if _has_any(text, FILE_PATTERNS + LOCAL_AGENT_PATTERNS + PLANNING_PATTERNS):
        category = "multi_step_agent_task" if _has_any(text, PLANNING_PATTERNS) else "file_workspace_task"
    elif _has_any(text, CODING_PATTERNS):
        category = "coding"
    else:
        category = "local_computer_action"

    return RouteResult(
        category=category,
        confidence=0.76,
        reason="Guardrail: eylem/gorev sinyali var; conversation sonucu reddedildi.",
        needs_local_agent=category in {"coding", "local_computer_action", "file_workspace_task", "multi_step_agent_task"},
        needs_web=category == "research",
        risk=classify_risk(message),
        intent_type="ACTION_REQUEST",
        metadata=metadata,
    )


def classify_with_llm(message: str) -> RouteResult:
    """Brain-backed intent classifier; deterministic logic is only a guardrail."""
    prompt = [
        {
            "role": "system",
            "content": (
                "Sen genel amacli kisisel AI orkestratorunun intent classifier katmanisin. "
                "Kullanici istegini analiz et. Kullanici sadece bilgi/sohbet mi istiyor, "
                "yoksa sistemden gercek dunyada veya bilgisayarda bir eylem yapmasini mi istiyor? "
                "Eger kullanici olustur, kur, ekle, gonder, ac, duzenle, planla, arastir, kodla, "
                "dosya olustur, takvime ekle gibi bir sey istiyorsa bu conversation degildir. "
                "Conversation sadece gercek sohbet veya bilgi aciklamasi icindir. "
                "Sadece gecerli JSON dondur, markdown yazma."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "message": message,
                    "available_tools": registered_tools(),
                    "intents": [
                        "conversation",
                        "content_generation",
                        "research",
                        "coding",
                        "local_computer_action",
                        "file_workspace_task",
                        "browser_model_task",
                        "multi_step_agent_task",
                    ],
                    "critical_rule": [
                        "alarm kur, takvime ekle, dosya olustur, mail gonder, chrome ac, projeyi incele, kod yaz, web sitesi yap, arastir, fiyat bul, hesabima gir, klasoru tara, bunu bilgisayarimda yap -> conversation olamaz.",
                        "Bilinmeyen eylemler conversation'a dusmez; uygun action intent secilir ve eksik tool belirtilir.",
                    ],
                    "return_schema": {
                        "intent": "conversation|content_generation|research|coding|local_computer_action|file_workspace_task|browser_model_task|multi_step_agent_task",
                        "is_action_request": False,
                        "needs_browser_model": False,
                        "needs_local_tools": False,
                        "needs_workspace": False,
                        "needs_web": False,
                        "risk_level": "low|medium|high",
                        "reason": "...",
                        "known_tool_available": True,
                        "missing_tool": "",
                        "fallback_options": [],
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    raw = router_chat(prompt, temperature=0)
    data = _extract_json(raw)
    category = str(data.get("intent") or data.get("category") or "conversation").strip()
    if category not in CATEGORIES:
        category = "conversation"
    metadata = {
        "known_tool_available": data.get("known_tool_available"),
        "missing_tool": data.get("missing_tool", ""),
        "fallback_options": data.get("fallback_options", []),
        "raw_classifier": data,
    }
    is_action = bool(data.get("is_action_request", category not in {"conversation", "content_generation", "research"}))
    return RouteResult(
        category=category,
        confidence=float(data.get("confidence", 0.82 if is_action else 0.75)),
        reason=str(data.get("reason", "")),
        needs_web=bool(data.get("needs_web", category == "research")),
        needs_local_agent=bool(
            data.get("needs_local_tools")
            or data.get("needs_workspace")
            or category in {"coding", "local_computer_action", "file_workspace_task", "multi_step_agent_task"}
        ),
        risk=str(data.get("risk_level") or data.get("risk") or classify_risk(message)),
        intent_type="ACTION_REQUEST" if is_action else "INFORMATION_REQUEST",
        metadata=metadata,
    )


def _extract_json(text: str) -> dict:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
    raise ValueError("Router JSON ayiklayamadi.")


def _compact_error(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")
    if "modal-no-auth-rate-limit" in text:
        return "ChatGPT oturumu/login veya no-auth rate limit modali nedeniyle kullanilamadi"
    if "Timeout" in text or "timeout" in text:
        return "Browser provider timeout"
    return text[:240]
