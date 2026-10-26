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
- offline tests for import, version, help, configuration, tools, errors, and security boundaries.

It still does not call real GitHub or model endpoints, execute repository code, or provide a production model client.

## Local commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
\.venv\Scripts\github-scout-agent.exe --help
\.venv\Scripts\github-scout-agent.exe version
\.venv\Scripts\github-scout-agent.exe config
\.venv\Scripts\github-scout-agent.exe mock-demo
\.venv\Scripts\python.exe -m pytest
# Explicit real GitHub public REST verification; never runs by default.
\.venv\Scripts\python.exe -m pytest -m live_github -s
# Explicit S4-02 real GitHub + Mock LLM scenarios, one at a time.
\.venv\Scripts\github-scout-agent.exe live-agent-demo --scenario search
\.venv\Scripts\github-scout-agent.exe live-agent-demo --scenario repository
# Offline-only DeepSeek Gate A protocol preflight.
\.venv\Scripts\github-scout-agent.exe gate-a --dry-run
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
