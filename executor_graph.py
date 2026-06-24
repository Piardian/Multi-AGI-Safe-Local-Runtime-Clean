# -*- coding: utf-8 -*-
"""Planning/observation graph.

Direct local-tool execution was retired in favour of TaskRuntime. Graphs can
still coordinate model or callback observations, but cannot acquire local
machine capability on their own.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Any

from agents import chat_model, orchestrator_brain_chat, web_query_chat
from critic_node import evaluate_step
from security_policy import action_requires_approval


@dataclass
class GraphNode:
    id: str
    description: str
    executor: str
    tool: str = ""
    input: dict | None = None
    requires_approval: bool = False
    status: str = "pending"
    output: Any = None
    error: str | None = None
    retry_count: int = 0
    critic: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GraphResult:
    nodes: list[GraphNode]
    status: str
    summary: str

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "summary": self.summary,
            "nodes": [node.as_dict() for node in self.nodes],
        }


def graph_from_plan(task_plan: dict) -> list[GraphNode]:
    nodes: list[GraphNode] = []
    for index, step in enumerate(task_plan.get("steps", []) or [], start=1):
        executor = str(step.get("executor", "api_worker"))
        nodes.append(
            GraphNode(
                id=f"step_{step.get('step') or step.get('id') or index}",
                description=str(step.get("description") or step.get("action") or ""),
                executor=_normalize_executor(executor),
                tool=str(step.get("tool", "")),
                input=step.get("input") or {},
                requires_approval=bool(step.get("requires_approval", False)),
            )
        )
    if not nodes:
        nodes.append(GraphNode("step_1", "Gorevi tamamla.", "api_worker", input={"prompt": task_plan.get("goal", "")}))
    return nodes


def run_executor_graph(
    task_plan: dict,
    callbacks: dict[str, Callable[[GraphNode], Any]] | None = None,
    auto_approve: bool = False,
    max_retries: int = 1,
) -> GraphResult:
    callbacks = callbacks or {}
    nodes = graph_from_plan(task_plan)
    for node in nodes:
        approval_needed, approval_reason = action_requires_approval(
            {"tool": node.tool, **(node.input or {})},
            "medium" if node.requires_approval else "low",
        )
        if (node.requires_approval or approval_needed) and not auto_approve:
            node.status = "skipped"
            node.error = approval_reason or "Kullanici onayi gerekiyor."
            return GraphResult(nodes, "needs_approval", node.error)

        while node.retry_count <= max_retries:
            node.status = "running"
            try:
                node.output = _execute_node(node, callbacks)
                ok, reason = observe_node(node)
                if ok:
                    if node.executor == "local_tool":
                        node.critic = {"passed": True, "retry_needed": False, "critique": "Bypassed critic for local tool."}
                    else:
                        node.critic = evaluate_step(str(task_plan.get("goal", "")), node.as_dict(), node.output)
                    if node.critic.get("retry_needed") or not node.critic.get("passed", True):
                        ok = False
                        reason = node.critic.get("suggested_fix") or "; ".join(node.critic.get("issues", [])) or "Critic retry istedi."
                if ok:
                    node.status = "success"
                    node.error = None
                    break
                node.retry_count += 1
                node.error = reason
                if node.retry_count > max_retries:
                    node.status = "failed"
                    return GraphResult(nodes, "failed", reason)
            except Exception as exc:
                node.retry_count += 1
                node.error = str(exc)
                if node.retry_count > max_retries:
                    node.status = "failed"
                    return GraphResult(nodes, "failed", str(exc))

    return GraphResult(nodes, "success", "Graph adimlari tamamlandi.")


def observe_node(node: GraphNode) -> tuple[bool, str]:
    if node.output is None:
        return False, "Adim cikti uretmedi."
    if isinstance(node.output, dict) and node.output.get("ok") is False:
        return False, str(node.output.get("message") or "Tool basarisiz.")
    if isinstance(node.output, str) and not node.output.strip():
        return False, "Bos metin ciktisi."
    return True, "ok"


def _execute_node(node: GraphNode, callbacks: dict[str, Callable[[GraphNode], Any]]) -> Any:
    if node.executor in callbacks:
        return callbacks[node.executor](node)
    if node.executor == "local_tool":
        return {
            "ok": False,
            "message": "Direct local_tool execution is disabled. Submit typed actions to TaskRuntime.",
        }
    if node.executor == "browser_brain":
        prompt = str((node.input or {}).get("prompt") or node.description)
        return orchestrator_brain_chat([{"role": "user", "content": prompt}], 0.2)
    if node.executor == "api_worker":
        prompt = str((node.input or {}).get("prompt") or node.description)
        return chat_model([{"role": "user", "content": prompt}], 0.2)
    if node.executor == "web_provider":
        prompt = str((node.input or {}).get("prompt") or node.description)
        return web_query_chat([{"role": "user", "content": prompt}], 0.2)
    if node.executor == "workspace_agent":
        return {"ok": False, "message": "workspace_agent callback bagli degil."}
    return {"ok": False, "message": f"Bilinmeyen executor: {node.executor}"}


def _normalize_executor(executor: str) -> str:
    mapping = {
        "browser_model": "browser_brain",
        "api_model": "api_worker",
        "local_tool_executor": "local_tool",
        "web_research_provider": "web_provider",
    }
    return mapping.get(executor, executor)
