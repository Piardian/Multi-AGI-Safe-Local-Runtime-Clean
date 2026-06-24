# -*- coding: utf-8 -*-
"""Cost-aware provider selection logic."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
import config


@dataclass
class ProviderSelection:
    provider_type: str
    provider_name: str
    browser_target: str = ""
    reason: str = ""
    estimated_cost: str = "free_local"
    selected_model: str = ""
    fallback_chain: list[str] = field(default_factory=list)
    decision_log: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "provider_type": self.provider_type,
            "provider_name": self.provider_name,
            "browser_target": self.browser_target,
            "reason": self.reason,
            "estimated_cost": self.estimated_cost,
            "selected_model": self.selected_model,
            "fallback_chain": self.fallback_chain,
            "decision_log": self.decision_log
        }


def _is_local_model_suitable(model_name: str) -> bool:
    import json
    import os
    import time
    
    results_path = os.path.join(config.PROJECT_ROOT, "logs", "local_benchmark_results.json")
    if not os.path.exists(results_path):
        return False
        
    # Check age of benchmark file (must be < 24 hours = 86400 seconds)
    try:
        mtime = os.path.getmtime(results_path)
        age_seconds = time.time() - mtime
        if age_seconds > 86400:
            print(f"\n[UYARI] Lokal model benchmark sonuçları 24 saatten eski! Lütfen benchmarkı tekrar çalıştırın:")
            print("   .venv\\Scripts\\python.exe bridge.py --benchmark-local-models\n")
            return False
    except Exception:
        return False
        
    try:
        with open(results_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        decision = data.get(model_name)
        if not decision:
            return False
            
        final_dec = decision.get("final_decision", {})
        
        # simulated=false zorunlu kalsın.
        if final_dec.get("simulated", True) is not False:
            return False
            
        # must be tested and suitable
        if not final_dec.get("tested", False):
            return False
            
        return bool(final_dec.get("use_in_main_flow", False))
    except Exception:
        return False


def select_cost_aware_provider(
    task_plan: dict,
    goal: str,
    cli_provider: str | None = None,
    cli_browser_target: str | None = None
) -> ProviderSelection:
    task_type = task_plan.get("task_type", "conversation")
    normalized = _normalize(goal)

    if task_type == "small_talk":
        sel = ProviderSelection("direct_response_provider", "direct_response", "", "Direct response local handler for small talk.")
        sel.estimated_cost = "free_local"
        sel.selected_model = "-"
        sel.fallback_chain = []
        sel.decision_log = {
            "intent": "small_talk",
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    if task_type == "weather_query":
        # Check location
        from bridge import extract_weather_location
        location = extract_weather_location(goal)
        p_name = "direct_response" if not location else "local_tool"
        p_type = "direct_response_provider" if not location else "local_tool_executor"
        p_reason = "Clarification handler for weather location." if not location else "Weather search local execution."
        
        sel = ProviderSelection(p_type, p_name, "", p_reason)
        sel.estimated_cost = "free_local"
        sel.selected_model = "-"
        sel.fallback_chain = []
        sel.decision_log = {
            "intent": "weather_query",
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    if task_type == "file_operation_clarification":
        sel = ProviderSelection("direct_response_provider", "direct_response", "", "Clarification handler for missing destination.")
        sel.estimated_cost = "free_local"
        sel.selected_model = "-"
        sel.fallback_chain = []
        sel.decision_log = {
            "intent": "file_operation_clarification",
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    browser_target = cli_browser_target or getattr(config, "DEFAULT_BROWSER_TARGET", getattr(config, "CHAT_BROWSER_TARGET", "chatgpt"))
    provider_override = cli_provider

    if "tarayicidan" in normalized or "tarayici uzerinden" in normalized or "chatgpt uzerinden" in normalized:
        provider_override = "browser"
    if "api ile" in normalized or "api uzerinden" in normalized:
        provider_override = "api"
    if "perplexity" in normalized:
        provider_override = "browser"
        browser_target = "perplexity"
    if "claude" in normalized:
        provider_override = "browser"
        browser_target = "claude"
    if "gemini" in normalized:
        provider_override = "browser"
        browser_target = "gemini"

    # If overridden
    if provider_override:
        p_name = provider_override.lower().strip()
        if p_name in {"browser", "browser_gpt"}:
            sel = ProviderSelection("browser_model_provider", "browser", browser_target, "Kullanıcı/browser override istedi.")
            sel.estimated_cost = "browser_subscription"
            sel.selected_model = browser_target
            sel.fallback_chain = ["api"]
        elif p_name in {"api", "groq"}:
            sel = ProviderSelection("api_model_provider", p_name, "", "Kullanıcı/API override istedi.")
            sel.estimated_cost = "api_limited"
            sel.selected_model = config.CODER_MODEL
            sel.fallback_chain = ["browser", "local_model"]
        elif p_name == "local_model":
            sel = ProviderSelection("local_model_provider", "local_model", "", "Kullanıcı/local model override istedi.")
            sel.estimated_cost = "free_local"
            sel.selected_model = config.LOCAL_FAST_MODEL
            sel.fallback_chain = ["deterministic_fallback"]
        else:
            sel = ProviderSelection("api_model_provider", p_name, "", "Kullanıcı override.")
            sel.estimated_cost = "api_limited"
            sel.selected_model = "unknown"
            sel.fallback_chain = []

        sel.decision_log = {
            "intent": task_type,
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    # 1. Rule A: Direkt local tool ile çözülebilen işler
    from direct_tool_mapper import try_direct_map
    if try_direct_map(goal):
        sel = ProviderSelection("local_tool_executor", "local_tool", "", "Direct local tool (no model/API/browser needed).")
        sel.estimated_cost = "free_local"
        sel.selected_model = "none"
        sel.fallback_chain = []
        sel.decision_log = {
            "intent": task_type,
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    # 2. Rule B: Basit JSON/tool seçimi or lightweight goals
    if task_type in {"conversation", "content_generation"}:
        if config.USE_LOCAL_FAST and _is_local_model_suitable(config.LOCAL_FAST_MODEL) and _is_lightweight_goal(normalized):
            sel = ProviderSelection("local_model_provider", "local_model", "", "Hafif sohbet/icerik icin local fast model.")
            sel.estimated_cost = "free_local"
            sel.selected_model = config.LOCAL_FAST_MODEL
            sel.fallback_chain = ["api"]
        else:
            provider = getattr(config, "DEFAULT_CHAT_PROVIDER", config.CHAT_PROVIDER)
            p_type = "browser_model_provider" if provider == "browser" else "api_model_provider"
            sel = ProviderSelection(p_type, provider, browser_target, "Sohbet/icerik varsayılan provider.")
            sel.estimated_cost = "browser_subscription" if provider == "browser" else "api_limited"
            sel.selected_model = browser_target if provider == "browser" else config.CODER_MODEL
            sel.fallback_chain = ["api" if provider == "browser" else "browser"]

        sel.decision_log = {
            "intent": task_type,
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    # 3. Rule E: Araştırma
    if task_type == "research":
        provider = getattr(config, "DEFAULT_RESEARCH_PROVIDER", config.RESEARCH_PROVIDER)
        if provider == "local_tool":
            sel = ProviderSelection("local_tool_executor", "local_tool", "", "Public web research capability.")
            sel.estimated_cost = "free_local"
            sel.selected_model = "none"
            sel.fallback_chain = ["browser"]
        else:
            p_type = "browser_model_provider" if provider == "browser" else "api_model_provider"
            sel = ProviderSelection(p_type, provider, browser_target, "Araştırma varsayılan provider.")
            sel.estimated_cost = "browser_subscription" if provider == "browser" else "api_limited"
            sel.selected_model = browser_target if provider == "browser" else config.CODER_MODEL
            sel.fallback_chain = ["api" if provider == "browser" else "browser"]

        sel.decision_log = {
            "intent": task_type,
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    # 4. Rule C: Critic / ikinci görüş
    is_critic_task = "critic" in normalized or "degerlendir" in normalized or "kontrol et" in normalized
    if is_critic_task and config.USE_LOCAL_CRITIC and _is_local_model_suitable(config.LOCAL_REASONER_MODEL):
        sel = ProviderSelection("local_model_provider", "local_model", "", "Critic/ikinci görüş için local reasoner model.")
        sel.estimated_cost = "free_local"
        sel.selected_model = config.LOCAL_REASONER_MODEL
        sel.fallback_chain = ["browser", "api"]
        sel.decision_log = {
            "intent": task_type,
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    # 5. Rule D: Zor reasoning / genel planlama (Workspace analysis, coding, etc.)
    if task_type in {"file_workspace_task", "coding", "multi_step_agent_task"}:
        if task_type == "coding" and config.USE_LOCAL_CODER and _is_local_model_suitable(config.LOCAL_CODER_MODEL):
            sel = ProviderSelection("local_model_provider", "local_model", "", "Kucuk coding isi icin local coder.")
            sel.estimated_cost = "free_local"
            sel.selected_model = config.LOCAL_CODER_MODEL
            sel.fallback_chain = ["browser", "api"]
        else:
            provider = getattr(config, "DEFAULT_AGENT_PROVIDER", config.LOCAL_AGENT_PROVIDER)
            p_type = "browser_model_provider" if provider == "browser" else "api_model_provider"
            sel = ProviderSelection(p_type, provider, browser_target, "Workspace/local agent varsayılan provider.")
            sel.estimated_cost = "browser_subscription" if provider == "browser" else "api_limited"
            sel.selected_model = browser_target if provider == "browser" else config.CODER_MODEL
            sel.fallback_chain = ["api" if provider == "browser" else "browser"]

        sel.decision_log = {
            "intent": task_type,
            "selected_provider": sel.provider_name,
            "selected_model": sel.selected_model,
            "reason": sel.reason,
            "estimated_cost": sel.estimated_cost,
            "fallback_chain": sel.fallback_chain
        }
        return sel

    # 6. Default fallback
    provider = getattr(config, "DEFAULT_CHAT_PROVIDER", config.CHAT_PROVIDER)
    p_type = "browser_model_provider" if provider == "browser" else "api_model_provider"
    sel = ProviderSelection(p_type, provider, browser_target, "Varsayılan selection.")
    sel.estimated_cost = "browser_subscription" if provider == "browser" else "api_limited"
    sel.selected_model = browser_target if provider == "browser" else config.CODER_MODEL
    sel.fallback_chain = []
    sel.decision_log = {
        "intent": task_type,
        "selected_provider": sel.provider_name,
        "selected_model": sel.selected_model,
        "reason": sel.reason,
        "estimated_cost": sel.estimated_cost,
        "fallback_chain": sel.fallback_chain
    }
    return sel


def _normalize(text: str) -> str:
    replacements = {
        "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
        "\u0131": "i", "\u011f": "g", "\u00fc": "u", "\u015f": "s", "\u00f6": "o", "\u00e7": "c",
    }
    lowered = (text or "").lower()
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    return lowered.strip()


def _is_lightweight_goal(normalized_goal: str) -> bool:
    words = normalized_goal.split()
    if len(words) > 35:
        return False
    heavy_markers = ["mimari", "projeyi incele", "buyuk", "detayli", "akademik kaynakli", "uzun", "analiz"]
    return not any(marker in normalized_goal for marker in heavy_markers)
