# Multi AGI — Safe Local AI Runtime

> A policy-first local AI assistant for Windows diagnostics, controlled desktop tasks, and source-backed public research.

Multi AGI is a portfolio project exploring how an AI assistant can be useful on a personal computer **without** turning into an unrestricted shell agent. Its official execution path is deliberately narrow:

`UI / CLI → Task Runtime → typed plan → Policy Engine → approved tool execution → audit log`

The project began as an orchestration and quant-lab experiment. The current focus is a safer local technician runtime: deterministic Windows diagnosis, explicit approvals for consequential actions, and privacy-aware external research.

## Why this matters for finance technology

Financial systems need more than a capable model: they need traceability, defined permissions, controlled failure modes, and an operator in the loop. This repository applies those engineering ideas to a local AI runtime:

- Explicit, typed capabilities instead of free-form shell access
- Central policy decisions and user approval for medium/high-risk actions
- Audit events for plans, policy decisions, tool calls, and diagnostic reports
- Secret/path redaction before outside-model or public-web use
- A dry-run-only experimental Quant Lab kept outside the official runtime

It does **not** connect to broker accounts, place orders, provide investment advice, or execute model-generated code.

## What it can do

- Produce deterministic Windows diagnostic reports for unexpected restarts, crashes, slow PC symptoms, driver problems, and disk issues
- Read selected Windows sources through read-only capabilities: event logs, reliability history, crash records, update history, driver errors, startup items, processes, and disk health
- Correlate evidence into a structured report: summary, severity, confidence, timeline, possible causes, unavailable sources, and next steps
- Run source-backed public web search without browser automation, downloads, or workspace-data leakage
- Offer controlled file/application workflows where each capability has a declared risk and policy review
- Present plans, approvals, and reports through a local Tkinter UI or a localhost-only web UI

## Safety model

| Area | Design choice |
| --- | --- |
| Execution | Official calls pass through `TaskRuntime` and `PolicyEngine` |
| Shell access | No generic `shell=True` or free-form command capability in the official runtime |
| Code generation | Generated code is not executed; validation is syntax-only in an isolated process |
| Data egress | Sensitive paths, secrets, `.env` references, and log details are redacted/blocked by default |
| User control | Medium/high-risk actions require explicit approval; `DEV_MODE` is off by default |
| Observability | JSONL audit trail captures plans, decisions, calls, results, and diagnostic report generation |

For the detailed component map, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (Windows)

Prerequisites: Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Keep `DEV_MODE=false` and `SANDBOX_MODE=syntax_only` in `.env` for normal use.

Start the desktop UI:

```powershell
.\run_gui.bat
```

Or start the localhost-only web UI:

```powershell
.\run_web_ui.bat
```

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

## Example diagnostic requests

- `Bilgisayar neden beklenmedik şekilde yeniden başladı?`
- `Mavi ekran ve çökme kayıtlarını incele.`
- `Bilgisayar neden yavaş, teşhis raporu oluştur.`
- `Sürücü hatalarını kontrol et.`
- `Disk sağlığı ve boş alanla ilgili sorun var mı?`

Known diagnostic requests select fixed playbooks. They do not use an external model to decide which system commands to run.

## Repository layout

```text
task_runtime.py          Official plan → policy → tool execution runtime
policy_engine.py         Typed allowlist, risk, approval and preview decisions
tools/                   Registered diagnostic, research and controlled local tools
diagnostic_playbooks.py  Deterministic read-only diagnostic scenarios
evidence_correlator.py   Evidence/timeline/report construction
data_policy.py           Redaction and public-query protection
audit.py                 JSONL audit logging
gui.py / web_server.py   Local operator interfaces
tests/                   Regression, safety and UI smoke tests
```

## Experimental Quant Lab

`run_quant.bat` is intentionally separated from the official runtime. It enables an experimental worker only in `--dry-run` mode. It is included as an architectural experiment, not as a trading system and not as an execution engine.

## Scope and limitations

- Designed for Windows; several diagnostics rely on Windows-native sources.
- Some sources (for example Reliability Monitor or SMART data) may be unavailable. The report records this as an unavailable source rather than treating it as a successful diagnosis.
- Public web search is intentionally limited to public queries and returns sourced snippets. It does not browse authenticated sites, download files, or control a browser.
- This is a portfolio project, not production security software.

## License

Released under the [MIT License](LICENSE).
