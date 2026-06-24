# -*- coding: utf-8 -*-
"""Deterministic task planner for the general-purpose orchestrator."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import config
from agents import orchestrator_brain_chat
from tools.registry import registered_tools


@dataclass
class TaskStep:
    step: int
    description: str
    executor: str
    requires_approval: bool = False


@dataclass
class TaskPlan:
    task_type: str
    goal: str
    steps: list[TaskStep]
    risks: list[str] = field(default_factory=list)
    expected_output: str = "Kullaniciya temiz sonuc dondur."

    def as_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "goal": self.goal,
            "steps": [step.__dict__ for step in self.steps],
            "risks": self.risks,
            "expected_output": self.expected_output,
        }


def build_task_plan(goal: str, route: dict) -> TaskPlan:
    if route.get("intent_type") in {"small_talk", "weather_query"} or (route.get("metadata") or {}).get("missing_destination"):
        return build_task_plan_fallback(goal, route)
    if getattr(config, "ORCHESTRATOR_BRAIN_USE_LLM", True):
        try:
            return build_task_plan_with_brain(goal, route)
        except Exception:
            pass
    return build_task_plan_fallback(goal, route)


def build_task_plan_with_brain(goal: str, route: dict) -> TaskPlan:
    messages = [
        {
            "role": "system",
            "content": (
                "Sen genel amacli kisisel AI orkestratorunun task planner katmanisin. "
                "Router sonucuna ve mevcut tool listesine gore uygulanabilir plan cikar. "
                "Executor secenekleri: browser_brain, api_worker, local_tool, workspace_agent, web_provider. "
                "Tool yoksa conversation'a dusme; missing tool/fallback mantigini plana yaz. "
                "Sadece gecerli JSON dondur."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "route": route,
                    "available_tools": registered_tools(),
                    "return_schema": {
                        "goal": goal,
                        "intent": route.get("category"),
                        "steps": [
                            {
                                "id": 1,
                                "executor": "browser_brain|api_worker|local_tool|workspace_agent|web_provider",
                                "action": "Yapilacak is",
                                "requires_approval": False,
                            }
                        ],
                        "tools_needed": [],
                        "risk_level": "low|medium|high",
                        "expected_output": "...",
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    raw = orchestrator_brain_chat(messages, temperature=0)
    data = _extract_json(raw)
    task_type = str(data.get("intent") or route.get("category") or "conversation")
    steps: list[TaskStep] = []
    for index, item in enumerate(data.get("steps", []) or [], start=1):
        steps.append(
            TaskStep(
                step=int(item.get("id") or item.get("step") or index),
                description=str(item.get("action") or item.get("description") or ""),
                executor=str(item.get("executor") or "api_worker"),
                requires_approval=bool(item.get("requires_approval", False)),
            )
        )
    if not steps:
        steps = [TaskStep(1, "Gorevi uygun executor ile calistir.", "api_worker")]
    risk = str(data.get("risk_level") or route.get("risk") or "low")
    return TaskPlan(
        task_type=task_type,
        goal=str(data.get("goal") or goal),
        steps=steps,
        risks=[] if risk == "low" else [f"{risk} risk gorev"],
        expected_output=str(data.get("expected_output") or "Kullaniciya temiz sonuc dondur."),
    )


def build_task_plan_fallback(goal: str, route: dict) -> TaskPlan:
    task_type = route.get("category", "conversation")
    risk = route.get("risk", "low")
    risks = [] if risk == "low" else [f"{risk} risk gorev"]

    intent = route.get("intent_type")
    metadata = route.get("metadata") or {}
    if metadata.get("missing_destination"):
        steps = [TaskStep(1, "Eksik hedef icin clarification sor.", "direct_response")]
        expected = "Nereye tasinacagi/kopyalanacagi sorusu."
        task_type = "file_operation_clarification"
    elif intent == "small_talk" or task_type == "small_talk":
        steps = [TaskStep(1, "Basit sohbet cevabi uret.", "direct_response")]
        expected = "Direct local reply."
        task_type = "small_talk"
    elif intent == "weather_query" or task_type == "weather_query":
        steps = [TaskStep(1, "Hava durumunu sorgula.", "web_research_provider")]
        expected = "Hava durumu tahmini."
        task_type = "weather_query"
    elif task_type == "conversation":
        steps = [TaskStep(1, "Kullanici mesajina dogrudan cevap ver.", "api_model")]
        expected = "Kisa ve net sohbet cevabi."
    elif task_type == "content_generation":
        steps = [TaskStep(1, "Istenen metni uret.", "api_model")]
        expected = "Dosya yazmadan uretilmis metin."
    elif task_type == "research":
        steps = [TaskStep(1, "Guncel bilgiyi arastir.", "web_research_provider")]
        expected = "Kaynak/tarih bilinciyle sade arastirma cevabi."
    elif task_type == "browser_model_task":
        steps = [TaskStep(1, "Secilen web AI arayuzune prompt gonder.", "browser_model")]
        expected = "Browser model cevabi."
    elif task_type == "local_computer_action":
        steps = [TaskStep(1, "Yerel tool aksiyonunu guvenli sekilde calistir.", "local_tool", risk == "high")]
        expected = "Tool sonuc ozeti."
    elif task_type == "file_workspace_task":
        steps = [TaskStep(1, "Workspace dosya listesini cikar ve guvenli dosyalari incele.", "workspace_agent")]
        expected = "Workspace analiz/plani."
    elif task_type == "coding":
        steps = [
            TaskStep(1, "Kodlama gorevini uygulanabilir plana cevir.", "workspace_agent"),
            TaskStep(2, "Gerekli dosya degisikliklerini izinlere gore uygula.", "workspace_agent", risk != "low"),
        ]
        expected = "Kodlama sonucu ve degisen dosyalar."
    else:
        steps = [
            TaskStep(1, "Gorevi alt adimlara bol.", "api_model"),
            TaskStep(2, "Gerekli provider/tool/agent hattini calistir.", "workspace_agent", risk == "high"),
        ]
        expected = "Cok adimli gorev raporu."

    return TaskPlan(task_type=task_type, goal=goal, steps=steps, risks=risks, expected_output=expected)


def _extract_json(text: str) -> dict:
    cleaned = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
    raise ValueError("Task planner JSON ayiklayamadi.")
