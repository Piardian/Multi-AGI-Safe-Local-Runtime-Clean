# -*- coding: utf-8 -*-
"""Secret detection and redaction before external model or audit egress."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePath
from typing import Any

import config


SENSITIVE_PATH_PATTERNS = (
    r"(^|[\\/])\.env(?:\.|$)",
    r"(^|[\\/])\.ssh([\\/]|$)",
    r"(^|[\\/])credentials?([\\/._-]|$)",
    r"(^|[\\/])(secrets?|tokens?|passwords?|api[_-]?keys?)([\\/._-]|$)",
    r"\.(pem|key|pfx|p12|kdbx)$",
)

SECRET_PATTERNS = (
    re.compile(r"(?im)(\b(?:api[_-]?key|token|secret|password|passwd|client_secret)\b\s*[:=]\s*)([^\s'\"`]{4,})"),
    re.compile(r"(?i)\b(bearer\s+)([a-z0-9._~+\-/=]{8,})"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"\bgsk_[A-Za-z0-9_-]{12,}\b"),
)
USER_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:[\\/](?:users|documents|desktop|appdata)[\\/][^\s'\"]+|[\\/](?:users|home)[\\/][^\s'\"]+)")
SENSITIVE_REFERENCE_PATTERN = re.compile(r"(?i)(?:\.env\b|audit\.jsonl\b|orchestrator\.log\b|bridge\.log\b)")
PUBLIC_QUERY_BLOCK_PATTERNS = (
    re.compile(r"(?i)(?:^|\s)(?:[a-z]:[\\/]|[\\/](?:users|home|appdata|desktop)[\\/])"),
    re.compile(r"(?i)\.(?:env|pem|key|pfx|p12|kdbx)\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|private key|credential)\b"),
    re.compile(r"(?i)\b(?:audit\.jsonl|orchestrator\.log|bridge\.log)\b"),
)


def is_sensitive_path(path: str) -> bool:
    normalized = str(PurePath(path)).replace("\\", "/").lower()
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in SENSITIVE_PATH_PATTERNS)


def redact_text(value: str) -> str:
    text = value or ""
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED_PRIVATE_KEY]", text)
    
    # Redact emails
    email_pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    text = email_pattern.sub("[REDACTED_EMAIL]", text)
    
    # Redact phones
    phone_pattern = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
    text = phone_pattern.sub("[REDACTED_PHONE]", text)

    text = USER_PATH_PATTERN.sub("[REDACTED_PATH]", text)
    return SENSITIVE_REFERENCE_PATTERN.sub("[REDACTED_SENSITIVE_REFERENCE]", text)



def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def redact_messages(messages: list[dict]) -> list[dict]:
    """Return a copy safe to send to an external provider.

    This is intentionally a redaction layer, not permission to upload arbitrary
    private files.  The policy engine separately blocks sensitive file tools.
    """

    return redact_value(deepcopy(messages))


def is_external_provider(provider: str) -> bool:
    return (provider or "").lower().strip() not in {"local", "local_model", "lmstudio", "ollama"}


def protect_workspace_context(contents: dict[str, str], provider: str) -> dict[str, str]:
    """Redact, or withhold, file contents before an external model call."""

    if is_external_provider(provider) and not config.ALLOW_EXTERNAL_WORKSPACE_CONTENT:
        return {
            path: "[WITHHELD: external workspace-content egress is disabled by policy]"
            for path in contents
        }
    return {path: redact_text(content) for path, content in contents.items()}


def validate_public_query(query: str) -> tuple[bool, str]:
    value = (query or "").strip()
    if not value:
        return False, "Public web query cannot be empty."
    if len(value) > 300:
        return False, "Public web query exceeds the 300 character limit."
    if redact_text(value) != value:
        return False, "Public web query contains redacted secret or personal-path content."
    if any(pattern.search(value) for pattern in PUBLIC_QUERY_BLOCK_PATTERNS):
        return False, "Public web query contains sensitive workspace, log, credential, or personal-path content."
    return True, ""
