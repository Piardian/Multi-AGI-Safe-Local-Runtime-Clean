# -*- coding: utf-8 -*-
"""Runtime-only authorization context for local tool execution.

Tools are never trusted to decide their own authorization.  The central task
runtime installs a short-lived context immediately before calling a tool;
calling the registry outside that context fails closed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthorizedToolCall:
    task_id: str
    tool: str
    approval_mode: str


_active_call: ContextVar[AuthorizedToolCall | None] = ContextVar("active_tool_call", default=None)


@contextmanager
def authorized_tool_call(task_id: str, tool: str, approval_mode: str):
    token = _active_call.set(AuthorizedToolCall(task_id, tool, approval_mode))
    try:
        yield
    finally:
        _active_call.reset(token)


def require_authorized_tool(tool: str) -> AuthorizedToolCall:
    active = _active_call.get()
    if active is None:
        raise PermissionError("Tool execution is only available through TaskRuntime.")
    if active.tool != tool:
        raise PermissionError(f"Authorized tool mismatch: expected {active.tool}, received {tool}.")
    return active
