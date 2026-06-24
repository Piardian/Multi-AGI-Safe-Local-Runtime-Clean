# -*- coding: utf-8 -*-
"""Model client helpers for the autonomous bridge."""

from __future__ import annotations

import json
import time

import config
from data_policy import redact_messages


def _groq_chat(messages: list[dict], model: str, temperature: float = 0.2) -> str:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY ayarlanmamis. .env dosyasini kontrol edin.")

    try:
        from groq import Groq
    except ImportError as exc:
        raise RuntimeError(
            "groq paketi kurulu degil. requirements.txt bagimliliklarini kurun."
        ) from exc

    client = Groq(api_key=config.GROQ_API_KEY, timeout=config.API_TIMEOUT, max_retries=0)
    last_error: Exception | None = None
    fallback_model = getattr(config, "GROQ_FALLBACK_MODEL", "")
    models = [model]
    if fallback_model and fallback_model not in models:
        models.append(fallback_model)

    for selected_model in models:
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                completion = client.chat.completions.create(
                    model=selected_model,
                    messages=messages,
                    temperature=temperature,
                )
                return completion.choices[0].message.content.strip()
            except Exception as exc:
                last_error = exc
                lowered = str(exc).lower()
                if "rate_limit" in lowered or "429" in lowered or attempt == config.MAX_RETRIES:
                    break
                time.sleep(config.RATE_LIMIT_SLEEP * attempt)

    raise RuntimeError(f"Groq istegi basarisiz oldu: {last_error}")


def chat_with_provider(
    provider: str,
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.2,
    browser_target: str | None = None,
) -> str:
    """Call a concrete provider while keeping the rest of the app provider-neutral."""

    provider = provider.lower().strip()
    outbound_messages = messages if provider in {"local_model", "local"} else redact_messages(messages)
    
    if provider in {"groq", "api", "api_provider"}:
        try:
            return _groq_chat(outbound_messages, model or config.CODER_MODEL, temperature)
        except Exception as exc:
            text = str(exc).lower()
            if "429" in text or "rate_limit" in text or "rate limit" in text:
                import re
                retry_match = re.search(r"retry_after_seconds[\":\s]+([0-9.]+)", text) or re.search(r"retry\s+in\s+([0-9.]+)", text)
                retry_seconds = float(retry_match.group(1)) if retry_match else 60.0
                
                print(f"\n[RATE_LIMIT] Groq rate limit (429) doldu. Hata: {exc}")
                print(f"Lütfen {retry_seconds:.1f} saniye bekleyin veya alternatif sağlayıcıyı deneyin.")
                
                # Check fallback to local
                if getattr(config, "USE_LOCAL_CODER", False):
                    from cost_aware_provider_selector import _is_local_model_suitable
                    if _is_local_model_suitable(config.LOCAL_CODER_MODEL):
                        failures = getattr(config, "LOCAL_MODEL_FAILURES", 0)
                        slow_count = getattr(config, "LOCAL_MODEL_SLOW_COUNT", 0)
                        if failures < getattr(config, "LOCAL_MODEL_MAX_FAILURES", 3) and slow_count < getattr(config, "DISABLE_SLOW_LOCAL_AFTER_FAILURES", 3):
                            print("[FALLBACK] Groq rate limit nedeniyle yerel modele (local_model) geçiliyor...")
                            try:
                                from local_model_provider import local_chat
                                res = local_chat(outbound_messages, role="coding_worker", timeout=15)
                                if res.status == "success":
                                    return res.response
                            except Exception as local_exc:
                                print(f"[FALLBACK] Yerel model de başarısız oldu: {local_exc}")
                
                # Check fallback to browser
                if config.ORCHESTRATOR_BRAIN_PROVIDER in {"browser", "browser_gpt"}:
                    print("[FALLBACK] Groq rate limit nedeniyle tarayıcı modeline (browser) geçiliyor...")
                    try:
                        from browser_model_provider import ask_browser_model
                        return ask_browser_model(outbound_messages, target=config.CHAT_BROWSER_TARGET)
                    except Exception as browser_exc:
                        print(f"[FALLBACK] Tarayıcı modeli de başarısız oldu: {browser_exc}")
                
                raise RuntimeError(f"Groq rate limit doldu, {retry_seconds:.1f} saniye sonra tekrar deneyebilirim. Hata: {exc}")
            raise exc

    if provider in {"local_model", "local"}:
        from local_model_provider import local_chat
        role = "reasoning_critic" if (model and "reason" in model.lower()) or "critic" in str(messages).lower() else "coding_worker"
        
        failures = getattr(config, "LOCAL_MODEL_FAILURES", 0)
        slow_count = getattr(config, "LOCAL_MODEL_SLOW_COUNT", 0)
        max_failures = getattr(config, "LOCAL_MODEL_MAX_FAILURES", 3)
        disable_after_slow = getattr(config, "DISABLE_SLOW_LOCAL_AFTER_FAILURES", 3)
        
        if failures >= max_failures:
            print("[LOCAL_MODEL] Yerel model çok sayıda hata verdiği için devre dışı bırakıldı. API fallback kullanılıyor.")
            return chat_with_provider("api", messages, model, temperature, browser_target)
        if slow_count >= disable_after_slow:
            print("[LOCAL_MODEL] Yerel model çok yavaş yanıt verdiği için devre dışı bırakıldı. API fallback kullanılıyor.")
            return chat_with_provider("api", messages, model, temperature, browser_target)

        result = local_chat(
            outbound_messages,
            model=model,
            role=role,
            timeout=config.LOCAL_MODEL_TIMEOUT_SECONDS,
            max_tokens=config.LOCAL_MODEL_MAX_TOKENS,
        )
        if result.status != "success":
            config.LOCAL_MODEL_FAILURES = getattr(config, "LOCAL_MODEL_FAILURES", 0) + 1
            print(f"[LOCAL_MODEL] Local model basarisiz; API fallback kullaniliyor. Hata: {result.error}")
            return chat_with_provider("api", messages, model, temperature, browser_target)

        # Update metrics & healing
        if result.response_time_seconds > 15.0:
            config.LOCAL_MODEL_SLOW_COUNT = getattr(config, "LOCAL_MODEL_SLOW_COUNT", 0) + 1
        else:
            config.LOCAL_MODEL_FAILURES = 0
            config.LOCAL_MODEL_SLOW_COUNT = 0
            
        return result.response

    if provider in {"browser_gpt", "browser", "browser_provider"}:
        if provider in {"browser", "browser_provider"}:
            from browser_model_provider import ask_browser_model
            import sys

            requested_target = (browser_target or config.CHAT_BROWSER_TARGET or "chatgpt").lower().strip()
            try:
                return ask_browser_model(outbound_messages, target=requested_target)
            except Exception as primary_exc:
                if requested_target != "chatgpt":
                    print(f"\n{requested_target.capitalize()} başarısız oldu. ChatGPT fallback kullanılsın mı? (evet/HAYIR): ", end="")
                    try:
                        sys.stdout.flush()
                        ans = input().strip().lower()
                        use_fallback = ans in {"evet", "e", "yes", "y"}
                    except Exception:
                        use_fallback = False
                    
                    if use_fallback:
                        print("[BROWSER_PROVIDER] ChatGPT fallback kullaniliyor...")
                        try:
                            response = ask_browser_model(outbound_messages, target="chatgpt")
                            import json
                            verification_log = {
                                "requested_target": requested_target,
                                "actual_url": "https://chatgpt.com",
                                "adapter_name": "ChatGPTAdapter",
                                "response_source": "chatgpt",
                                "fallback_used": True
                            }
                            print(f"\n[BROWSER_VERIFICATION_LOG]\n{json.dumps(verification_log, indent=2)}")
                            return response
                        except Exception as fallback_exc:
                            raise RuntimeError(f"Fallback da basarisiz oldu: {fallback_exc}") from fallback_exc
                
                if getattr(config, "BROWSER_PROVIDER_API_FALLBACK", False):
                    print("[BROWSER_PROVIDER] Browser basarisiz; API fallback kullaniliyor.")
                    return chat_with_provider("api", messages, model, temperature, browser_target)
                raise primary_exc
        from browser_gpt import ask_chatgpt
        return ask_chatgpt(outbound_messages)

    if provider in {"local_tool", "local_tool_provider"}:
        raise RuntimeError("local_tool provider model cevabi uretmez; tool executor kullanilmali.")
    raise RuntimeError(f"Desteklenmeyen provider: {provider}")



def groq_chat(messages: list[dict], model: str | None = None, temperature: float = 0.2) -> str:
    """Backward-compatible default chat entry point."""

    return chat_with_provider(config.LLM_PROVIDER, messages, model, temperature)


def chat_model(messages: list[dict], temperature: float = 0.2) -> str:
    return chat_with_provider(
        config.CHAT_PROVIDER,
        messages,
        config.CHAT_MODEL,
        temperature,
        browser_target=config.CHAT_BROWSER_TARGET,
    )


def router_chat(messages: list[dict], temperature: float = 0.0) -> str:
    return chat_with_provider(config.ROUTER_PROVIDER, messages, config.ROUTER_MODEL, temperature)


def orchestrator_brain_chat(messages: list[dict], temperature: float = 0.0) -> str:
    try:
        return chat_with_provider(
            config.ORCHESTRATOR_BRAIN_PROVIDER,
            messages,
            config.CHAT_MODEL,
            temperature,
            browser_target=config.ORCHESTRATOR_BRAIN_TARGET,
        )
    except Exception as primary_exc:
        fallback_provider = getattr(config, "ORCHESTRATOR_BRAIN_FALLBACK_PROVIDER", "")
        if not fallback_provider:
            raise
        try:
            print(f"[BRAIN] Primary provider failed ({config.ORCHESTRATOR_BRAIN_PROVIDER}); fallback={fallback_provider}")
            return chat_with_provider(
                fallback_provider,
                messages,
                getattr(config, "ORCHESTRATOR_BRAIN_FALLBACK_MODEL", config.GROQ_FALLBACK_MODEL),
                temperature,
                browser_target=config.ORCHESTRATOR_BRAIN_TARGET,
            )
        except Exception as fallback_exc:
            raise RuntimeError(
                f"Brain primary ve fallback basarisiz. primary={primary_exc}; fallback={fallback_exc}"
            ) from fallback_exc


def prompt_architect_chat(messages: list[dict], temperature: float = 0.2) -> str:
    return chat_with_provider(
        config.PROMPT_ARCHITECT_PROVIDER,
        messages,
        config.PROMPT_ARCHITECT_MODEL,
        temperature,
    )


def local_agent_chat(messages: list[dict], temperature: float = 0.1) -> str:
    return chat_with_provider(
        config.LOCAL_AGENT_PROVIDER,
        messages,
        config.LOCAL_AGENT_MODEL,
        temperature,
    )


def web_query_chat(messages: list[dict], temperature: float = 0.2) -> str:
    return chat_with_provider(
        config.WEB_QUERY_PROVIDER,
        messages,
        config.CHAT_MODEL,
        temperature,
        browser_target=config.RESEARCH_BROWSER_TARGET,
    )


def active_model_info() -> str:
    """Return model/provider info from config so the assistant never invents it."""
    rows = [
        ("Chat", config.CHAT_PROVIDER, config.CHAT_MODEL, getattr(config, "CHAT_BROWSER_TARGET", "")),
        ("Router", config.ROUTER_PROVIDER, config.ROUTER_MODEL, ""),
        ("Prompt architect", config.PROMPT_ARCHITECT_PROVIDER, config.PROMPT_ARCHITECT_MODEL, ""),
        ("Local agent", config.LOCAL_AGENT_PROVIDER, config.LOCAL_AGENT_MODEL, ""),
        ("Coder", config.CODER_PROVIDER, config.CODER_MODEL, ""),
        ("Critic", config.CRITIC_PROVIDER, config.CRITIC_MODEL, ""),
        ("Web/Research", config.WEB_QUERY_PROVIDER, config.CHAT_MODEL, getattr(config, "RESEARCH_BROWSER_TARGET", "")),
        ("Orchestrator brain", config.ORCHESTRATOR_BRAIN_PROVIDER, config.CHAT_MODEL, getattr(config, "ORCHESTRATOR_BRAIN_TARGET", "")),
        ("Brain fallback", getattr(config, "ORCHESTRATOR_BRAIN_FALLBACK_PROVIDER", ""), getattr(config, "ORCHESTRATOR_BRAIN_FALLBACK_MODEL", ""), ""),
        ("Local coder", config.LOCAL_MODEL_PROVIDER, config.LOCAL_FAST_MODEL, ""),
        ("Local critic", config.LOCAL_MODEL_PROVIDER, config.LOCAL_REASONER_MODEL, ""),
    ]
    lines = ["Aktif model/provider bilgisi config dosyasindan okunuyor:"]
    for name, provider, model, target in rows:
        suffix = f" | browser_target={target}" if target else ""
        lines.append(f"- {name}: provider={provider} | model={model}{suffix}")
    lines.append(f"- Fallback model: {config.GROQ_FALLBACK_MODEL}")
    return "\n".join(lines)


def planner_chat(messages: list[dict], temperature: float = 0.2) -> str:
    return prompt_architect_chat(messages, temperature)


def coder_chat(messages: list[dict], temperature: float = 0.1) -> str:
    return local_agent_chat(messages, temperature)


def critic_chat(messages: list[dict], temperature: float = 0.2) -> str:
    return chat_with_provider(config.CRITIC_PROVIDER, messages, config.CRITIC_MODEL, temperature)


def call_pm_agent(context: str) -> str:
    return groq_chat([{"role": "user", "content": context}], config.PM_MODEL, 0.2)


def call_coder_agent(hypothesis: str, current_code: str) -> str:
    prompt = (
        "Hipotez ve mevcut kod asagida. Sadece guncel Python kodunu dondur.\n\n"
        f"HIPOTEZ:\n{hypothesis}\n\nMEVCUT KOD:\n{current_code}"
    )
    return groq_chat([{"role": "user", "content": prompt}], config.CODER_MODEL, 0.1)


def call_critic_agent(test_results: str, hypothesis: str, stderr: str = "") -> str:
    prompt = (
        "Test sonucunu ve hipotezi degerlendir. Kisa, uygulanabilir geri bildirim ver.\n\n"
        f"HIPOTEZ:\n{hypothesis}\n\nSTDOUT:\n{test_results}\n\nSTDERR:\n{stderr}"
    )
    return groq_chat([{"role": "user", "content": prompt}], config.CRITIC_MODEL, 0.2)


def test_groq_connection() -> bool:
    try:
        answer = groq_chat(
            [{"role": "user", "content": "Sadece OK yaz."}],
            config.PM_MODEL,
            0,
        )
        print(f"  [OK] Model yaniti: {answer[:80]}")
        return True
    except Exception as exc:
        print(f"  [HATA] Model baglanti testi basarisiz: {exc}")
        return False
