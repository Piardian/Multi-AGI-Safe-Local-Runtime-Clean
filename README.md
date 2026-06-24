# Multi AGI — Secure Local AI Orchestrator

> A policy-first, general-purpose local AI orchestration runtime for Windows diagnostics, controlled desktop work, and source-backed public research.

Multi AGI is a portfolio prototype for exploring a practical local AI system: a capable primary model coordinates smaller specialist models and tightly scoped tools, while the operator keeps control of consequential actions.

It is **not** presented as AGI. The goal is a transparent, extensible orchestration architecture that can break down work, route it to the most suitable capability, and execute only through explicit safety boundaries.

## Core idea

The intended architecture combines:

- A primary model for task understanding, decomposition, and high-level planning
- Smaller specialist models or deterministic components for routing, critique, local reasoning, and focused sub-tasks
- Typed local capabilities for diagnostics, file workflows, application workflows, and public research
- A central policy layer that evaluates every official tool call
- Human approval for medium- and high-risk actions

The official runtime path is deliberately explicit: `UI / CLI → Task Runtime → Typed Plan → Policy Engine → Approved Tool Execution → Audit Log`.

## Current capabilities

- Deterministic Windows diagnostic reports for unexpected restarts, crashes, blue screens, slow-PC symptoms, driver issues, and disk issues
- Read-only inspection of selected Windows sources: event logs, reliability history, crash records, updates, driver errors, startup applications, processes, and disk health
- Evidence correlation into structured reports with a summary, severity, confidence, timeline, possible causes, unavailable sources, and recommended next steps
- Source-backed public web research without browser automation, downloads, or workspace-data leakage
- Controlled local file and application workflows using typed capabilities, risk levels, policy decisions, and operator approval
- Local Tkinter and localhost-only web interfaces for plans, approvals, and reports

## Safety and governance model

| Area | Design choice |
| --- | --- |
| Execution | Official calls pass through `TaskRuntime` and `PolicyEngine`. |
| Tool access | Typed, allowlisted capabilities replace generic shell execution. |
| Code execution | Model-generated code is not executed; available validation is syntax-only. |
| Data egress | Sensitive paths, secrets, `.env` references, and log details are redacted or blocked by default. |
| User control | Medium- and high-risk actions require explicit approval; `DEV_MODE` is off by default. |
| Observability | Plans, policy decisions, tool calls, results, and diagnostic reports are written to JSONL audit logs. |

This approach is relevant to high-trust automation contexts, including financial technology: strong capability boundaries, auditability, controlled failure modes, and an operator in the loop matter as much as model quality.

## Vibe-coding note

This is a **vibe-coding portfolio prototype**: it was developed through iterative, human-directed AI-assisted coding. The architecture, safety boundaries, requirements, and acceptance criteria are intentionally documented so the work can be inspected, discussed, and improved rather than treated as a black box.

## Quick start (Windows)

Prerequisite: Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For normal use, keep `DEV_MODE=false` and `SANDBOX_MODE=syntax_only`.

Start the desktop interface with `run_gui.bat`, or the localhost-only web interface with `run_web_ui.bat`.

Run tests with:

```powershell
python -m unittest discover -s tests -v
```

## Example diagnostic requests

- `Why did my PC restart unexpectedly?`
- `Review blue-screen and crash records.`
- `Why is my computer slow? Create a diagnostic report.`
- `Check for driver errors.`
- `Is there a disk-health or disk-space issue?`

Recognized diagnostic requests use fixed playbooks. An external model does not decide which system actions to run.

## Repository layout

- `task_runtime.py`: official plan → policy → tool execution runtime
- `policy_engine.py`: typed allowlist, risk, approval, and preview decisions
- `tools/`: diagnostic, research, and controlled local capabilities
- `diagnostic_playbooks.py`: deterministic diagnostic scenarios
- `evidence_correlator.py`: evidence correlation and report generation
- `data_policy.py`: redaction and external-query safeguards
- `audit.py`: JSONL audit logging
- `gui.py` and `web_server.py`: local operator interfaces
- `tests/`: regression, safety, and UI tests

## Scope and limitations

- The current implementation is Windows-focused.
- Some Windows sources, such as Reliability Monitor or SMART data, can be unavailable; this is reported explicitly rather than treated as a successful diagnosis.
- Public research is limited to public queries and sourced results. It does not browse authenticated sites, download files, or control a browser.
- This is a portfolio and research prototype, not a production security product.

## License

Released under the [MIT License](LICENSE).
