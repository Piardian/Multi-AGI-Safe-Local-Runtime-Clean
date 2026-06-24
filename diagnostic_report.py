# -*- coding: utf-8 -*-
"""Common schema for deterministic Windows diagnostic reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DiagnosticEvidence:
    source: str
    summary: str
    timestamp: str = ""
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiagnosticReport:
    summary: str
    severity: str
    confidence: float
    evidence: list[DiagnosticEvidence]
    timeline: list[DiagnosticEvidence]
    possible_causes: list[str]
    recommended_next_steps: list[str]
    blocked_or_unavailable_sources: list[dict]
    scenario: str = ""

    def as_dict(self) -> dict:
        return {
            "summary": self.summary,
            "severity": self.severity,
            "confidence": self.confidence,
            "evidence": [item.as_dict() for item in self.evidence],
            "timeline": [item.as_dict() for item in self.timeline],
            "possible_causes": self.possible_causes,
            "recommended_next_steps": self.recommended_next_steps,
            "blocked_or_unavailable_sources": self.blocked_or_unavailable_sources,
            "scenario": self.scenario,
        }
