# -*- coding: utf-8 -*-
"""LLM critic node for executor graph observations."""

from __future__ import annotations

import json
from typing import Any

from agents import orchestrator_brain_chat


DEFAULT_CRITIC = {
    "passed": True,
    "score": 80,
    "issues": [],
    "retry_needed": False,
    "suggested_fix": "",
    "next_step_recommendation": "",
}


def evaluate_step(goal: str, node: dict, output: Any) -> dict:
    """Ask the orchestrator brain whether a graph step actually satisfied intent."""
    messages = [
        {
            "role": "system",
            "content": (
                "Sen executor graph icin LLM critic node'sun. Bir adimin hedefe hizmet edip "
                "etmedigini, cikti formatini, eksik bilgiyi ve retry gerekip gerekmedigini "
                "degerlendir. Sadece JSON dondur."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "node": node,
                    "output": output,
                    "return_schema": {
                        "passed": True,
                        "score": 0,
                        "issues": [],
                        "retry_needed": False,
                        "suggested_fix": "",
                        "next_step_recommendation": "",
                    },
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    ]
    try:
        data = _extract_json(orchestrator_brain_chat(messages, temperature=0))
    except Exception as exc:
        data = {**DEFAULT_CRITIC, "issues": [f"critic calisamadi: {exc}"], "next_step_recommendation": "Heuristic observe sonucu kullan."}

    return {
        "passed": bool(data.get("passed", True)),
        "score": int(data.get("score", 80) or 0),
        "issues": list(data.get("issues", []) or []),
        "retry_needed": bool(data.get("retry_needed", False)),
        "suggested_fix": str(data.get("suggested_fix", "") or ""),
        "next_step_recommendation": str(data.get("next_step_recommendation", "") or ""),
    }


def _extract_json(text: str) -> dict:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
    raise ValueError("Critic JSON ayiklayamadi.")
