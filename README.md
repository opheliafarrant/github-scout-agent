# GitHub Scout Agent

S3-03 foundation for a read-only, evidence-backed GitHub research agent.

The current foundation provides:

- a Python package and CLI entry point;
- safe environment-based configuration status;
- a fixed registry for five schema-validated GitHub read-only tools;
- an HTTPX-based, fixed-host GitHub REST client with injectable MockTransport;
- deterministic request, response-size, and candidate budgets;
- a synchronous, injectable Mock-LLM Agent Loop with redacted execution events;
- a narrow, offline-tested OpenAI-compatible Chat Completions Tool Calling adapter;
- an offline `mock-demo` CLI command showing feedback-driven query rewriting;
- a confirmed production `run <task> --confirm-live` path using the model adapter, Agent Loop, fixed GitHub tools, budgets, and evidence report;
- T24 batch-safe Finalization handling for multiple Tool Calls;
- offline tests for import, version, help, configuration, tools, errors, Agent Loop, reports, and security boundaries.

The production `run` path can call real GitHub and model endpoints only with explicit `--confirm-live`; it is never used by default. It does not execute repository code, provide arbitrary Shell access, or perform GitHub write operations.

## Local commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
\.venv\Scripts\github-scout-agent.exe --help
\.venv\Scripts\github-scout-agent.exe version
\.venv\Scripts\github-scout-agent.exe config
\.venv\Scripts\github-scout-agent.exe mock-demo
\.venv\Scripts\github-scout-agent.exe run "find small Python agent repositories" --confirm-live
# Default offline regression; excludes the explicitly marked live GitHub test.
\.venv\Scripts\python.exe -m pytest -m "not live_github" --tb=short
\.venv\Scripts\python.exe -m pytest
# Explicit real GitHub public REST verification; never runs by default.
\.venv\Scripts\python.exe -m pytest -m live_github -s
# Explicit S4-02 real GitHub + Mock LLM scenarios, one at a time.
\.venv\Scripts\github-scout-agent.exe live-agent-demo --scenario search
\.venv\Scripts\github-scout-agent.exe live-agent-demo --scenario repository
# Offline-only DeepSeek Gate A protocol preflight.
\.venv\Scripts\github-scout-agent.exe gate-a --dry-run
# Online Gate A requires both flags and a local key; do not run without explicit authorization.
\.venv\Scripts\github-scout-agent.exe gate-a --online --confirm-live
# Offline-only Gate B compatibility chain.
\.venv\Scripts\github-scout-agent.exe gate-b --dry-run
```

Configuration status reads these environment variables without printing their values:

- `GITHUB_SCOUT_MODEL_BASE_URL`
- `GITHUB_SCOUT_MODEL`
- `GITHUB_SCOUT_MODEL_API_KEY`
- `GITHUB_TOKEN`
- `GITHUB_SCOUT_MODEL_THINKING_MODE` (`disabled`/`enabled`)
- `GITHUB_SCOUT_MODEL_STRICT_TOOLS` (`true`/`false`)
- `GITHUB_SCOUT_MODEL_MAX_TOKENS`

S3-01/S3-03 do not use these values to make real network requests. S4-01 live verification uses a bounded public-only budget; a configured `GITHUB_TOKEN` is optional and never printed.

## Current verification boundary

T24-P0 batch-budget and Finalization fixes are frozen locally. T25 completed the real DeepSeek + GitHub technical chain, but product acceptance was B because the original run lacked per-candidate language and README evidence. T29-P0 now enforces task-bound language qualification, preserves unknown language metadata, requires README evidence for native/function/tool-calling research tasks, and reports evidence gaps as partial. The current offline result is 126 passed, 1 deselected. A-level product acceptance is still pending an independent authorized run.

The next authorized online run keeps these hard limits: DeepSeek `deepseek-flash`, `https://api.deepseek.com`, at most 5 model calls including Finalization, 8 GitHub GET requests, 3 candidates, search `per_page <= 5`, `max_tokens=512`, 15-second HTTP timeout, 120-second task timeout, no retries, and no fallback model. In a network environment that permits direct access, PowerShell may set `$env:NO_PROXY='*'`; this bypass is not a universal network recommendation. Production `run` always requires `--confirm-live` and explicit user authorization. If the remaining cumulative RMB budget cannot be established from reliable records, do not run it.
