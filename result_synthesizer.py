# -*- coding: utf-8 -*-
"""User-facing result formatting for orchestrator runs."""

from __future__ import annotations

import json


def synthesize_result(task_plan: dict, provider: dict, result: str | dict, debug: bool = False) -> str:
    import time
    import config
    start_time = getattr(config, "LAST_RUN_START_TIME", None)
    if start_time is not None:
        config.LAST_RUN_DURATION = round(time.perf_counter() - start_time, 3)

    try:
        import task_memory
        task_memory.save_run(task_plan, provider, result)
    except Exception as exc:
        import logging
        logging.getLogger("result_synthesizer").error("Task memory kaydetme hatasi: %s", exc)


    if debug:
        return "\n".join(
            [
                "ORKESTRATOR DEBUG RAPORU",
                f"Task plan: {json.dumps(task_plan, ensure_ascii=False, indent=2)}",
                f"Provider: {json.dumps(provider, ensure_ascii=False, indent=2)}",
                "Sonuc:",
                result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2),
            ]
        )

    if getattr(config, "PLAN_ONLY", False) and not debug:
        return _format_result(result)

    if getattr(config, "INTERACTIVE_MODE", False) and not debug:
        formatted = _format_result(result)
        p_name = provider.get('provider_name') or provider.get('provider_type') or '-'
        return "\n".join(
            [
                f"Görev: {task_plan.get('goal', task_plan.get('task_type', '-'))}",
                f"Yol: {p_name}",
                f"Durum: {_status_from_result(result)}",
                f"Sonuç: {formatted}",
            ]
        )

    formatted = _format_result(result)
    return "\n".join(
        [
            f"Görev: {task_plan.get('goal', task_plan.get('task_type', '-'))}",
            f"Durum: {_status_from_result(result)}",
            f"Kullanılan yol: {provider.get('provider_type', '-')} / {provider.get('provider_name', '-')}",
            "Yapılanlar:",
            "",
            formatted,
        ]
    )


def _status_from_result(result) -> str:
    try:
        import web_server
        web_status = web_server.ACTIVE_EXECUTION["status"]
        if web_status == "needs_clarification":
            return "Bilgi gerekiyor"
        if web_status == "pending_approval":
            return "Onay bekliyor"
        if web_status == "denied":
            return "Reddedildi"
        if web_status == "failed":
            return "Başarısız"
    except Exception:
        pass

    if isinstance(result, dict):
        if "analysis_report" in result:
            return "Başarılı"
        status = str(result.get("status") or result.get("graph", {}).get("status") or "").lower()
        if status in {"needs_approval", "pending_approval"}:
            return "Onay bekliyor"
        if status in {"failed", "error", "failed_low_relevance"}:
            return "Başarısız"
        if status == "needs_clarification":
            return "Bilgi gerekiyor"
        if status == "denied":
            return "Reddedildi"
        if status == "success":
            return "Başarılı"

    text = str(result).lower().replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
    if "needs_approval" in text or "onayi gerekiyor" in text or "pending_approval" in text:
        return "Onay bekliyor"
    if "denied" in text or "status=denied" in text:
        return "Reddedildi"
    if "needs_clarification" in text or "bilgi gerekiyor" in text or ("nereye" in text and "istiyorsun" in text) or ("hangi sehir" in text and "istiyorsun" in text):
        return "Bilgi gerekiyor"
    if "failed" in text or "hata" in text or "basarisiz" in text:
        return "Başarısız"
    return "Başarılı"


def _format_result(result) -> str:
    if not isinstance(result, dict):
        return str(result).strip()

    if "task_runtime" in result or "safe_plan" in result:
        from data_policy import redact_text
        task_rt = result.get("task_runtime", {})
        calls = task_rt.get("calls", [])
        
        if not calls:
            safe_actions = result.get("safe_plan", {}).get("actions", [])
            calls = []
            for act in safe_actions:
                tool_name = act.get("tool") or act.get("type")
                payload = {k: v for k, v in act.items() if k not in {"tool", "type", "reason", "would_execute", "actual_execution"}}
                if tool_name:
                    calls.append({"tool": tool_name, "payload": payload})
                    
        lines = ["Plan:", ""]
        if not calls:
            lines.append("(Plan boş)")
        else:
            for idx, call in enumerate(calls, start=1):
                tool = call.get("tool")
                payload = call.get("payload") or {}
                hedef = payload.get("path") or payload.get("target") or payload.get("app") or payload.get("query") or ""
                redacted_hedef = redact_text(str(hedef))
                lines.append(f"{idx}. {tool}")
                if redacted_hedef:
                    lines.append(f"   Hedef: {redacted_hedef}")
                lines.append("   Durum: Çalıştırılmadı")
                lines.append("   Sebep: plan_only_active")
                lines.append("")
        lines.append("Hiçbir işlem uygulanmadı.")
        return "\n".join(lines)

    lines: list[str] = []
    if "analysis_report" in result:
        report = result["analysis_report"]
        lines.append(f"* Özet: {report.get('summary', '-')}")
        if report.get("architecture_observations"):
            lines.append("* Mimari gözlemler:")
            lines.extend(f"  - {item}" for item in report["architecture_observations"])
        if report.get("problems"):
            lines.append("* Sorunlar:")
            lines.extend(f"  - {item}" for item in report["problems"])
        if report.get("risk_areas"):
            lines.append("* Risk alanları:")
            lines.extend(f"  - {item}" for item in report["risk_areas"])
        if report.get("recommended_fixes"):
            lines.append("* Önerilen düzeltmeler:")
            lines.extend(f"  - {item}" for item in report["recommended_fixes"])
        if report.get("next_actions"):
            lines.append("* Sonraki öneri:")
            lines.extend(f"  - {item}" for item in report["next_actions"])
        return "\n".join(lines)

    if result.get("created_files"):
        lines.append("* Oluşturulan dosyalar:")
        lines.extend(f"  - {path}" for path in result["created_files"])
    if result.get("validation"):
        validation = result["validation"]
        lines.append(f"* Basic validation: {'geçti' if validation.get('passed') else 'başarısız'}")
        for issue in validation.get("issues", []):
            lines.append(f"  - {issue}")
    if result.get("summary"):
        lines.append(f"* {result['summary']}")
    if not lines:
        lines.append(json.dumps(result, ensure_ascii=False, indent=2))
    return "\n".join(lines)
