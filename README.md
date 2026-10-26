# GitHub Scout Agent

S3-01 foundation for a read-only, evidence-backed GitHub research agent.

This stage provides only:

- a Python package and CLI entry point;
- safe environment-based configuration status;
- offline tests for import, version, help, and configuration validation.

It does not call GitHub, call a model, execute repository code, or implement the Agent Loop.

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
