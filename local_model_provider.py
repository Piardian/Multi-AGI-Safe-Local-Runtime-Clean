# -*- coding: utf-8 -*-
"""Optional local model provider for LM Studio and Ollama.

This module is intentionally isolated: if local endpoints are down, callers get
structured reports/errors and the main orchestrator can fall back cleanly.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib import error, request

import config


@dataclass
class LocalCallResult:
    provider: str
    model: str
    role: str
    response: str
    response_time_seconds: float
    status: str
    fallback_used: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def local_chat(
    messages: list[dict],
    model: str | None = None,
    role: str = "fast_local_worker",
    provider: str | None = None,
    timeout: int | None = None,
    max_tokens: int | None = None,
) -> LocalCallResult:
    selected_provider = (provider or config.LOCAL_MODEL_PROVIDER).lower().strip()
    selected_model = model or _model_for_role(role)
    start = time.perf_counter()
    try:
        if selected_provider == "ollama":
            response = _ollama_chat(messages, selected_model, timeout, max_tokens)
        elif selected_provider == "lmstudio":
            response = _lmstudio_chat(messages, selected_model, timeout, max_tokens)
        else:
            raise RuntimeError(f"Desteklenmeyen local provider: {selected_provider}")
        elapsed = round(time.perf_counter() - start, 3)
        if not response.strip():
            raise RuntimeError("Local model bos cevap dondurdu.")
        return LocalCallResult(selected_provider, selected_model, role, response.strip(), elapsed, "success")
    except Exception as exc:
        elapsed = round(time.perf_counter() - start, 3)
        return LocalCallResult(selected_provider, selected_model, role, "", elapsed, "failed", error=str(exc))


def health_check() -> dict:
    report = {
        "ollama": _check_ollama(),
        "lmstudio": _check_lmstudio(),
        "models": {},
        "summary": [],
    }
    for role, model in [
        ("fast_local_worker", config.LOCAL_FAST_MODEL),
        ("reasoning_critic", config.LOCAL_REASONER_MODEL),
    ]:
        result = local_chat(
            [{"role": "user", "content": "Sadece OK yaz."}],
            model=model,
            role=role,
            timeout=config.LOCAL_MODEL_TIMEOUT_SECONDS,
            max_tokens=32,
        )
        report["models"][model] = result.as_dict()
        report["summary"].append(_format_log_line(result))
    return report


def benchmark() -> dict:
    provider_status = _check_lmstudio() if config.LOCAL_MODEL_PROVIDER.lower() == "lmstudio" else _check_ollama()
    tests = [
        {
            "name": "Türkçe intent",
            "role": "fast_local_worker",
            "prompt": "Bu cümlede kullanıcı bilgi mi istiyor eylem mi istiyor: Masaüstünde deneme klasörü oluştur",
            "expect_json": False,
        },
        {
            "name": "Tool JSON",
            "role": "fast_local_worker",
            "prompt": 'Şu JSON şemasına uygun cevap ver:\n{\n"intent": "...",\n"tool": "...",\n"risk": "..."\n}',
            "expect_json": True,
        },
        {
            "name": "Basit kod",
            "role": "fast_local_worker",
            "prompt": "Basit bir HTML todo uygulaması için index.html üret.",
            "expect_json": False,
        },
        {
            "name": "Critic",
            "role": "reasoning_critic",
            "prompt": "Aşağıdaki plan iyi mi? Eksikleri JSON olarak yaz.",
            "expect_json": True,
        },
        {
            "name": "Tool action",
            "role": "fast_local_worker",
            "prompt": "Kullanıcı Chrome'u açmak istiyor. Uygun tool çağrısını JSON üret.",
            "expect_json": True,
        },
    ]

    rows: list[dict] = []
    model_decisions: dict[str, dict] = {}

    for model, default_role in [
        (config.LOCAL_FAST_MODEL, "fast_local_worker"),
        (config.LOCAL_REASONER_MODEL, "reasoning_critic"),
    ]:
        model_rows = []
        for test in tests:
            role = test["role"] if model == config.LOCAL_FAST_MODEL else default_role
            if not provider_status.get("ok"):
                result = LocalCallResult(
                    config.LOCAL_MODEL_PROVIDER,
                    model,
                    role,
                    "",
                    0.0,
                    "failed",
                    error=f"Endpoint kapali: {provider_status.get('error')}",
                )
                valid_json = False
            else:
                result = local_chat(
                    [{"role": "user", "content": test["prompt"]}],
                    model=model,
                    role=role,
                    timeout=config.LOCAL_MODEL_TIMEOUT_SECONDS,
                    max_tokens=config.LOCAL_MODEL_MAX_TOKENS,
                )
                valid_json = _is_valid_json(result.response) if test["expect_json"] else False

            row = {
                "test": test["name"],
                "model_name": model,
                "role": role,
                "response_time_seconds": result.response_time_seconds,
                "valid_json": valid_json,
                "answer_quality": _quality_score(result, valid_json, test["expect_json"]),
                "status": result.status,
                "notes": result.error or _notes_for_answer(result.response, valid_json, test["expect_json"]),
                "recommended_role": "reasoning_critic" if model == config.LOCAL_REASONER_MODEL else "fast_local_worker"
            }
            rows.append(row)
            model_rows.append(row)

        # Calculate metrics per model
        successful_runs = [r for r in model_rows if r["status"] == "success"]
        avg_time = round(sum(r["response_time_seconds"] for r in successful_runs) / len(successful_runs), 3) if successful_runs else 0.0
        
        json_runs = [r for r in model_rows if r["test"] in {"Tool JSON", "Critic", "Tool action"}]
        json_ok = [r for r in json_runs if r["valid_json"]]
        json_rate = round(len(json_ok) / len(json_runs), 3) if json_runs else 0.0
        
        avg_quality = round(sum(r["answer_quality"] for r in model_rows) / len(model_rows), 2)
        
        # Decisions and suitability thresholds
        if model == config.LOCAL_REASONER_MODEL:
            should_use_in_main_flow = len(successful_runs) > 0 and avg_time < 20.0 and json_rate >= 0.7 and avg_quality >= 3.0
            recommended_role = "reasoning_critic"
        else:
            should_use_in_main_flow = len(successful_runs) > 0 and avg_time < 10.0 and json_rate >= 0.8 and avg_quality >= 3.0
            recommended_role = "fast_local_worker"

        if not provider_status.get("ok"):
            rationale = "Local model endpoint kapalı olduğu için kalite testi yapılamadı."
            final_decision = {
                "model": model,
                "tested": False,
                "simulated": False,
                "response_time_seconds": 0.0,
                "valid_json": 0.0,
                "quality_score": 1.0,
                "recommended_role": recommended_role,
                "use_in_main_flow": False,
                "reason": rationale
            }
        else:
            if should_use_in_main_flow:
                rationale = f"Hizli yanit suresi ({avg_time}s) ve iyi JSON basari orani ({json_rate:.0%}) nedeniyle ana akista kullanilabilir."
            else:
                reasons_failed = []
                if avg_time >= (20.0 if model == config.LOCAL_REASONER_MODEL else 10.0):
                    reasons_failed.append(f"yavas ({avg_time}s)")
                if json_rate < (0.7 if model == config.LOCAL_REASONER_MODEL else 0.8):
                    reasons_failed.append(f"dusuk JSON basarisi ({json_rate:.0%})")
                if avg_quality < 3.0:
                    reasons_failed.append(f"dusuk kalite skoru ({avg_quality})")
                if not reasons_failed:
                    reasons_failed.append("beklenmedik hata")
                rationale = f"Ana akis kriterlerini saglayamadi: {', '.join(reasons_failed)}."

            final_decision = {
                "model": model,
                "tested": True,
                "simulated": False,
                "response_time_seconds": avg_time,
                "valid_json": json_rate,
                "quality_score": avg_quality,
                "recommended_role": recommended_role,
                "use_in_main_flow": should_use_in_main_flow,
                "reason": rationale
            }

        model_decisions[model] = {
            "model_name": model,
            "average_response_time_seconds": avg_time,
            "json_success_rate": json_rate,
            "quality_score": avg_quality,
            "recommended_role": recommended_role,
            "should_use_in_main_flow": should_use_in_main_flow if provider_status.get("ok") else False,
            "rationale": rationale,
            "final_decision": final_decision
        }

    # Save decisions to local_benchmark_results.json
    import os
    results_path = os.path.join(config.PROJECT_ROOT, "logs", "local_benchmark_results.json")
    try:
        os.makedirs(os.path.dirname(results_path), exist_ok=True)
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(model_decisions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Overall averages
    successful = [row for row in rows if row["status"] == "success"]
    avg = round(sum(row["response_time_seconds"] for row in successful) / len(successful), 3) if successful else None
    json_tests = [row for row in rows if row["test"] in {"Tool JSON", "Critic"}]
    json_ok = [row for row in json_tests if row["valid_json"]]
    
    return {
        "provider": config.LOCAL_MODEL_PROVIDER,
        "timeout_seconds": config.LOCAL_MODEL_TIMEOUT_SECONDS,
        "average_response_time_seconds": avg,
        "json_success_rate": round(len(json_ok) / len(json_tests), 3) if json_tests else 0,
        "results": rows,
        "model_decisions": model_decisions
    }



def _model_for_role(role: str) -> str:
    if "critic" in role or "reason" in role:
        return config.LOCAL_REASONER_MODEL
    return config.LOCAL_FAST_MODEL


def _lmstudio_chat(messages: list[dict], model: str, timeout: int | None, max_tokens: int | None) -> str:
    url = config.LMSTUDIO_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens or config.LOCAL_MODEL_MAX_TOKENS,
    }
    data = _post_json(url, payload, timeout or config.LOCAL_MODEL_TIMEOUT_SECONDS)
    return data["choices"][0]["message"]["content"]


def _ollama_chat(messages: list[dict], model: str, timeout: int | None, max_tokens: int | None) -> str:
    url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens or config.LOCAL_MODEL_MAX_TOKENS, "temperature": 0.1},
    }
    data = _post_json(url, payload, timeout or config.LOCAL_MODEL_TIMEOUT_SECONDS)
    return data.get("message", {}).get("content", "")


def _check_lmstudio() -> dict:
    url = config.LMSTUDIO_BASE_URL.rstrip("/") + "/models"
    try:
        data = _get_json(url, timeout=3)
        models = [item.get("id", "") for item in data.get("data", [])]
        return {"ok": True, "base_url": config.LMSTUDIO_BASE_URL, "models": models}
    except Exception as exc:
        return {"ok": False, "base_url": config.LMSTUDIO_BASE_URL, "error": str(exc), "models": []}


def _check_ollama() -> dict:
    url = config.OLLAMA_BASE_URL.rstrip("/") + "/api/tags"
    try:
        data = _get_json(url, timeout=3)
        models = [item.get("name", "") for item in data.get("models", [])]
        return {"ok": True, "base_url": config.OLLAMA_BASE_URL, "models": models}
    except Exception as exc:
        return {"ok": False, "base_url": config.OLLAMA_BASE_URL, "error": str(exc), "models": []}


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _get_json(url: str, timeout: int) -> dict:
    with request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _is_valid_json(text: str) -> bool:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        json.loads(cleaned)
        return True
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                json.loads(cleaned[start : end + 1])
                return True
            except Exception:
                return False
    return False


def _quality_score(result: LocalCallResult, valid_json: bool, expect_json: bool) -> int:
    if result.status != "success":
        return 1
    if result.response_time_seconds > config.LOCAL_MODEL_TIMEOUT_SECONDS:
        return 2
    if expect_json and not valid_json:
        return 2
    if len(result.response.strip()) < 10:
        return 2
    if result.response_time_seconds <= 8:
        return 5
    if result.response_time_seconds <= 15:
        return 4
    return 3


def _notes_for_answer(answer: str, valid_json: bool, expect_json: bool) -> str:
    if expect_json and not valid_json:
        return "JSON bekleniyordu ama gecerli JSON alinmadi."
    return (answer or "").replace("\n", " ")[:160]


def _format_log_line(result: LocalCallResult) -> str:
    return (
        f"Provider: local_model | Model: {result.model} | Role: {result.role} | "
        f"Response time: {result.response_time_seconds}s | Status: {result.status} | "
        f"Fallback used: {str(result.fallback_used).lower()}"
    )
