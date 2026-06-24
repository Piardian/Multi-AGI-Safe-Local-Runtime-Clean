# -*- coding: utf-8 -*-
"""Provider selection rules for the orchestrator pipeline."""

from __future__ import annotations

from cost_aware_provider_selector import select_cost_aware_provider, ProviderSelection


def select_provider(
    task_plan: dict,
    goal: str,
    cli_provider: str | None = None,
    cli_browser_target: str | None = None
) -> ProviderSelection:
    return select_cost_aware_provider(task_plan, goal, cli_provider, cli_browser_target)
