# GitHub Scout Agent

S3-02 foundation for a read-only, evidence-backed GitHub research agent.

The current foundation provides:

- a Python package and CLI entry point;
- safe environment-based configuration status;
- a fixed registry for five schema-validated GitHub read-only tools;
- an HTTPX-based, fixed-host GitHub REST client with injectable MockTransport;
- deterministic request, response-size, and candidate budgets;
- offline tests for import, version, help, configuration, tools, errors, and security boundaries.

It still does not call GitHub, call a model, execute repository code, or implement the Agent Loop.

## Local commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
\.venv\Scripts\github-scout-agent.exe --help
\.venv\Scripts\github-scout-agent.exe version
\.venv\Scripts\github-scout-agent.exe config
\.venv\Scripts\python.exe -m pytest
```

Configuration status reads these environment variables without printing their values:

- `GITHUB_SCOUT_MODEL_BASE_URL`
- `GITHUB_SCOUT_MODEL`
- `GITHUB_SCOUT_MODEL_API_KEY`
- `GITHUB_TOKEN`

S3-01 does not use these values to make network requests.
