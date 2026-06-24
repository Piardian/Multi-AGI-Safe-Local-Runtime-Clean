# -*- coding: utf-8 -*-
"""Append-only, redacted audit events for the official runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from data_policy import redact_value


class AuditLogger:
    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or config.AUDIT_LOG_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, task_id: str, **details: Any) -> None:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "task_id": task_id,
            "details": redact_value(details),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
