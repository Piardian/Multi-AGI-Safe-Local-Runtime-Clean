# -*- coding: utf-8 -*-
"""Small safety layer for file and subprocess operations."""

from __future__ import annotations

import os
from pathlib import Path

import config
from execution_context import require_authorized_tool


class SecurityError(Exception):
    pass


class ApprovalDeniedError(Exception):
    pass


PROTECTED_FILES = {
    "agents.py",
    "bridge.py",
    "browser_gpt.py",
    "config.py",
    "hybrid_orchestrator.py",
    "local_agent.py",
    "security.py",
    "prompt_architect.py",
    "orchestrator.py",
    "router.py",
    "requirements.txt",
    "run_bridge.bat",
    "run_quant.bat",
    "audit.py",
    "data_policy.py",
    "execution_context.py",
    "policy_engine.py",
    "sandbox_runner.py",
    "task_runtime.py",
    "executor_graph.py",
    "security_policy.py",
    "tools/registry.py",
    "tools/safe_local_tools.py",
    "tools/windows_diagnostics.py",
    "tools/public_web.py",
}


def validate_path(path: str | os.PathLike) -> str:
    root = Path(config.PROJECT_ROOT).resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    # Allow project root
    try:
        resolved.relative_to(root)
        return str(resolved)
    except ValueError:
        pass

    # Allow user special folders: Desktop, Documents, Downloads
    home = Path.home()
    allowed_bases = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "Documents",
        home / "OneDrive" / "Downloads",
    ]
    for base in allowed_bases:
        try:
            resolved.relative_to(base.resolve())
            return str(resolved)
        except ValueError:
            continue

    raise SecurityError(f"Proje klasoru veya kullanici klasorleri disina cikilamaz: {path}")


def _validate_extension(path: Path) -> None:
    if path.suffix.lower() not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(config.ALLOWED_EXTENSIONS)
        raise SecurityError(f"Izin verilmeyen dosya uzantisi: '{path.suffix}'. Izin verilenler: {allowed}")


def safe_write_file(file_path: str, content: str, allow_protected: bool = False) -> str:
    # Every official write must originate from TaskRuntime after policy and an
    # approval decision.  Legacy callers fail closed instead of silently
    # acquiring write capability.
    require_authorized_tool("write_file_with_diff")
    target = Path(validate_path(file_path))
    _validate_extension(target)

    # Check protected files only within project root
    try:
        relative_name = target.relative_to(Path(config.PROJECT_ROOT).resolve()).as_posix()
        if not allow_protected and relative_name in PROTECTED_FILES:
            raise SecurityError(
                f"Altyapi dosyasi korunuyor: {relative_name}. Ajan bu dosyayi ezemez."
            )
    except ValueError:
        pass  # File is outside project root (e.g., Desktop) — no protected file check needed

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return str(target)


def safe_read_file(file_path: str) -> str:
    target = Path(validate_path(file_path))
    return target.read_text(encoding="utf-8")


def safe_run_subprocess(cmd: str, cwd: str | None = None) -> tuple[str, str, int]:
    raise SecurityError(
        "Generic shell execution is disabled. Use an allowlisted typed tool through TaskRuntime."
    )
