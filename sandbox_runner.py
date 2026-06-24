# -*- coding: utf-8 -*-
"""Fail-closed validation runner for generated code.

This module deliberately does *not* execute generated application code.  It
copies the requested source file into a temporary workspace and runs a syntax
parser in a separate isolated Python process.  A real OS sandbox backend can
replace this implementation later without widening the current privilege set.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import config
import security
from data_policy import is_sensitive_path


def validate_python_syntax_sandboxed(payload: dict) -> tuple[bool, str, dict]:
    target = str(payload.get("path") or payload.get("target") or "").strip()
    if not target:
        return False, "validate_python_syntax_sandboxed requires a relative Python file path.", {}
    if is_sensitive_path(target):
        return False, "Sensitive files cannot be copied into the sandbox.", {}

    source = Path(security.validate_path(target))
    if source.suffix.lower() != ".py" or not source.is_file():
        return False, "Only an existing .py file can be validated.", {}

    if config.SANDBOX_MODE != "syntax_only":
        return False, "No hardened execution sandbox is configured; generated code execution remains disabled.", {}

    with tempfile.TemporaryDirectory(prefix="multi_agi_sandbox_") as temp_dir:
        copied = Path(temp_dir) / source.name
        shutil.copy2(source, copied)
        parser = "import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))"
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", parser, str(copied)],
            cwd=temp_dir,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.SANDBOX_TIMEOUT_SECONDS,
            shell=False,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:1200]
        return False, f"Sandbox syntax validation failed: {detail}", {"path": target, "mode": "syntax_only"}
    return True, "Sandboxed syntax validation passed; generated code was not executed.", {"path": target, "mode": "syntax_only"}
