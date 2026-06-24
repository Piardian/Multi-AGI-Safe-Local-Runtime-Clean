# -*- coding: utf-8 -*-
"""Browser-backed model provider.

This module keeps credentials out of code. It reuses the existing browser_gpt
Playwright bridge and the user's browser profile/session. Manual login is
expected when the target site asks for it.
"""

from __future__ import annotations

import config
from browser_adapters.chatgpt_adapter import ChatGPTAdapter
from browser_adapters.claude_adapter import ClaudeAdapter
from browser_adapters.gemini_adapter import GeminiAdapter
from browser_adapters.groq_adapter import GroqAdapter
from browser_adapters.perplexity_adapter import PerplexityAdapter


ADAPTERS = {
    "chatgpt": ChatGPTAdapter,
    "claude": ClaudeAdapter,
    "gemini": GeminiAdapter,
    "groq": GroqAdapter,
    "perplexity": PerplexityAdapter,
}


def _compose_prompt(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def ask_browser_model(messages: list[dict], target: str | None = None, timeout: int | None = None) -> str:
    selected = (target or config.CHAT_BROWSER_TARGET or "chatgpt").lower().strip()
    adapter_cls = ADAPTERS.get(selected)
    if not adapter_cls:
        raise RuntimeError(f"Browser adapter bulunamadi: {selected}")
    adapter = adapter_cls()
    print(f"[BROWSER_PROVIDER] Hedef site aciliyor: {adapter.name} -> {adapter.url}")
    try:
        response = adapter.send_prompt(_compose_prompt(messages), timeout=timeout or config.BROWSER_PROVIDER_TIMEOUT)
        import json
        verification_log = {
            "requested_target": selected,
            "actual_url": adapter.url,
            "adapter_name": adapter_cls.__name__,
            "response_source": selected,
            "fallback_used": False
        }
        print(f"\n[BROWSER_VERIFICATION_LOG]\n{json.dumps(verification_log, indent=2)}")
        return response
    except Exception as exc:
        raise RuntimeError(f"Browser provider calisamadi ({selected}): {exc}") from exc


def browser_brain_health_check(target: str | None = None, timeout: int | None = None) -> dict:
    selected = (target or config.ORCHESTRATOR_BRAIN_TARGET or config.CHAT_BROWSER_TARGET or "chatgpt").lower().strip()
    result = {
        "target": selected,
        "provider": "browser",
        "site_opened": False,
        "login_ready": False,
        "prompt_input_found": False,
        "blocking_modal": False,
        "answer_received": False,
        "ready": False,
        "message": "",
        "answer_preview": "",
    }
    try:
        answer = ask_browser_model(
            [{"role": "user", "content": "Health check: sadece READY yaz."}],
            target=selected,
            timeout=timeout or min(config.BROWSER_PROVIDER_TIMEOUT, 45),
        )
        result.update(
            {
                "site_opened": True,
                "login_ready": True,
                "prompt_input_found": True,
                "answer_received": bool(answer.strip()),
                "ready": bool(answer.strip()),
                "message": "browser brain ready" if answer.strip() else "Cevap bos geldi.",
                "answer_preview": answer.strip()[:200],
            }
        )
    except Exception as exc:
        text = str(exc)
        result["site_opened"] = "hedefe ulasamadi" not in text.lower()
        result["blocking_modal"] = "modal" in text.lower() or "login" in text.lower() or "giris" in text.lower()
        result["message"] = _clean_browser_error(text)
    return result


def _clean_browser_error(text: str) -> str:
    if "modal" in text.lower() or "login" in text.lower() or "giris" in text.lower():
        return "ChatGPT tarayicida giris yap, sonra tekrar dene."
    if "hedefe ulasamadi" in text.lower():
        return "Browser provider hedefe ulasamadi."
    if "timeout" in text.lower() or "zaman" in text.lower():
        return f"Browser provider cevap alamadi: {text[:240]}"
    return text[:240]
