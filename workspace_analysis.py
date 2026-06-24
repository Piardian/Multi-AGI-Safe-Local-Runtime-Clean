# -*- coding: utf-8 -*-
"""Workspace analysis synthesis from real project files."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import config
import security
from agents import orchestrator_brain_chat
from data_policy import protect_workspace_context

logger = logging.getLogger("workspace_analysis")

PREFERRED_FILES = [
    "bridge.py",
    "router.py",
    "task_planner.py",
    "executor_graph.py",
    "provider_selector.py",
    "tools/registry.py",
    "security_policy.py",
    "policy_engine.py",
    "browser_model_provider.py",
    "coding_loop.py",
    "direct_tool_mapper.py",
]


def analyze_workspace(goal: str, workspace_files: list[str], max_chars_per_file: int = 5000) -> dict:
    # 1. Filter out ignored directories
    ignored = {".venv", "__pycache__", ".git", "node_modules", "dist", "build", ".pytest_cache"}
    filtered_files = []
    for f in workspace_files:
        path_parts = Path(f).parts
        if not any(part in ignored for part in path_parts):
            filtered_files.append(f)

    # 2. Select important files
    selected = [path for path in PREFERRED_FILES if path in set(filtered_files)]
    if not selected:
        selected = [path for path in filtered_files if path.endswith(".py")][:10]

    # 3. Read files safely (limiting to max_chars_per_file)
    contents: dict[str, str] = {}
    for path in selected[:12]:
        try:
            content = security.safe_read_file(path)
            contents[path] = content[:max_chars_per_file]
        except Exception as exc:
            contents[path] = f"[READ_ERROR] {exc}"

    # 4. Determine analysis provider
    provider = getattr(config, "WORKSPACE_ANALYSIS_PROVIDER", "local").lower().strip()

    if provider == "local":
        # Force fallback local analysis to bypass external LLM entirely
        return _fallback_analyze(goal, contents, selected, Exception("Workspace analysis provider 'local' is selected."))

    elif provider == "browser":
        warning_msg = "Workspace analysis browser provider kullanıyor. Seçili kod içerikleri browser modeline gönderilebilir."
        print(f"\n{warning_msg}\n")
        logger.warning(warning_msg)

        orig_provider = config.ORCHESTRATOR_BRAIN_PROVIDER
        config.ORCHESTRATOR_BRAIN_PROVIDER = "browser"
        try:
            return _llm_analyze(goal, contents, selected)
        except Exception as exc:
            return _fallback_analyze(goal, contents, selected, exc)
        finally:
            config.ORCHESTRATOR_BRAIN_PROVIDER = orig_provider

    elif provider == "api":
        orig_provider = config.ORCHESTRATOR_BRAIN_PROVIDER
        config.ORCHESTRATOR_BRAIN_PROVIDER = "api"
        try:
            return _llm_analyze(goal, contents, selected)
        except Exception as exc:
            return _fallback_analyze(goal, contents, selected, exc)
        finally:
            config.ORCHESTRATOR_BRAIN_PROVIDER = orig_provider

    else:
        # Default behavior: try LLM, fallback to local rule-based
        try:
            return _llm_analyze(goal, contents, selected)
        except Exception as exc:
            return _fallback_analyze(goal, contents, selected, exc)


def _llm_analyze(goal: str, contents: dict[str, str], selected: list[str]) -> dict:
    messages = [
        {
            "role": "system",
            "content": (
                "Sen yazilim mimarisi denetleyen senior reviewersin. Sana gercek workspace "
                "dosya icerikleri verilecek. Mimari gozlem, sorun, risk ve iyilestirme plani "
                "uret. Dosya yazma veya komut onerme; plan-only guvenli rapor ver. Sadece JSON dondur."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "selected_files": selected,
                    "file_contents": protect_workspace_context(contents, config.ORCHESTRATOR_BRAIN_PROVIDER),
                    "return_schema": {
                        "summary": "...",
                        "architecture_observations": [],
                        "problems": [],
                        "risk_areas": [],
                        "recommended_fixes": [],
                        "next_actions": [],
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]
    data = _extract_json(orchestrator_brain_chat(messages, temperature=0.1))
    return _normalize_report(data, selected)


def _fallback_analyze(goal: str, contents: dict[str, str], selected: list[str], exc: Exception) -> dict:
    problems = []
    if "bridge.py" in contents and "run_bridge_autonomous_loop" in contents["bridge.py"]:
        problems.append("CLI orchestration logic bridge.py icinde yogunlasmis; graph/executor ayrimi daha da netlestirilebilir.")
    if "executor_graph.py" in contents and "evaluate_step" in contents["executor_graph.py"]:
        problems.append("Executor graph critic eklenmis; ancak domain-specific validation node'lari ayrica genisletilmeli.")
    if "router.py" in contents and "GUARDRAIL_ACTION_PATTERNS" in contents["router.py"]:
        problems.append("Router brain-first olsa da guardrail regex listesi halen buyuyor; guardrail kapsam testleri eklenmeli.")

    return {
        "summary": f"Workspace analizi fallback ile uretildi; detay: {str(exc)[:180]}",
        "architecture_observations": [
            f"{len(selected)} onemli dosya gercek workspace listesinden secildi.",
            "Sistem router, planner, provider selector, graph executor ve tool registry katmanlarina ayrilmis.",
        ],
        "problems": problems or ["Belirgin problem icin LLM analiz tamamlanamadi; manuel inceleme onerilir."],
        "risk_areas": [
            "Browser automation oturum/modal durumlarina bagimli.",
            "Workspace agent ve graph executor ayrimi kademeli olarak tamamlanmali.",
        ],
        "recommended_fixes": [
            "Graph node tipleri icin unit test ekle.",
            "Workspace analizini read_file node'lari ve analysis node'u olarak graph'a tamamen tasiyin.",
            "Browser adapter selector'larini ChatGPT disindaki hedefler icin ozellestirin.",
        ],
        "next_actions": [
            "Dosya yazmadan mimari raporu kullaniciya sun.",
            "Onay verilirse dusuk riskli refactor adimlarini ayri graph olarak uygula.",
        ],
        "selected_files": selected,
    }


def _normalize_report(data: dict, selected: list[str]) -> dict:
    return {
        "summary": str(data.get("summary", "")),
        "architecture_observations": list(data.get("architecture_observations", []) or []),
        "problems": list(data.get("problems", []) or []),
        "risk_areas": list(data.get("risk_areas", []) or []),
        "recommended_fixes": list(data.get("recommended_fixes", []) or []),
        "next_actions": list(data.get("next_actions", []) or []),
        "selected_files": selected,
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
    raise ValueError("Workspace analysis JSON ayiklayamadi.")
