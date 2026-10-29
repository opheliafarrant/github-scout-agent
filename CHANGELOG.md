# Changelog

All notable changes to **GitHub Scout Agent** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]
### Added
- **Graphical Model Configuration**: Added a `⚙ 设置` dialog to the Web UI with service presets (DeepSeek / Qwen / OpenAI / local Ollama / custom), a first-run callout on an unconfigured instance, inline HTTPS validation, and instant apply without restarting the process. Backed by new `GET /api/settings` and `PUT /api/settings` endpoints.
- **Layered Configuration Store**: Settings now load from `%APPDATA%\GitHubScoutAgent\config.json` (override with `GITHUB_SCOUT_SETTINGS_FILE`); environment variables keep higher priority, so CLI and CI behaviour is unchanged.
- **Desktop Build (Windows)**: Added a PyInstaller one-directory spec under `packaging/` producing a console-free `GitHubScoutAgent.exe` that starts the local Web UI and opens the default browser. The frozen launcher implies the `web` command, redirects logging to `%APPDATA%\GitHubScoutAgent\logs\web-ui.log` (windowed builds have no stdout), picks a free port when 8080 is taken, and focuses the running instance instead of starting a second server.
- **Hermetic Test Fixture**: Added `tests/conftest.py` pinning settings and history paths to a temporary directory, so the suite never touches the developer's real `%APPDATA%` state.
- **Web UI History Persistence & Concurrency Guard**: Replaced in-memory store with `HistoryStore`, adding atomic JSONL persistence (`web_history.jsonl`), threading lock (`threading.Lock`) for safe concurrent multi-worker writes, collision-free task IDs (`uuid4` entropy), and bounded FIFO memory capacity.
- **KNOWN_PROJECTS Fallback**: Added `get_known_repository` tool resolving a known project name (cline, aider, openhands, swe-agent, mini-swe-agent; case-insensitive, normalized) directly to its canonical repository, bypassing search while consuming one candidate slot; registry now exposes 7 read-only tools.
- **Apache License 2.0**: Formally adopted Apache-2.0 open-source license; added full `LICENSE` text and `NOTICE` file with third-party dependency attributions.
- **Project Metadata & Packaging**: Configured standard packaging metadata in `pyproject.toml` including Apache-2.0 license, author, keywords, and classifiers.
- **GitHub Release Materials**: Prepared comprehensive release notes and repository topic metadata in `docs/RELEASE_NOTES_v1.1.0.md`.

### Security
- API keys and GitHub tokens are encrypted at rest with Windows DPAPI (stored as `dpapi:<ciphertext>`); all API responses return only masked hints (`sk-…abcd`) and never the plaintext secret.

### Fixed
- Keyless local endpoints (ollama, LM Studio, vLLM on `127.0.0.1`) are now usable: `model_ready` accepts a loopback Base URL without an API key, and the client omits the `Authorization` header instead of refusing to start. Previously the shipped Ollama preset saved successfully but left the app permanently `not_configured`.
- Added the missing favicon so every page load no longer logs a 404.
- Associated the four budget inputs with their labels (`for`/`id`) and gave the task textarea an accessible name.
- Submitting an empty task now shows an actionable hint in the event feed instead of silently doing nothing.

### Changed
- `github-scout-agent web` now opens the default browser automatically; pass `--no-browser` (or set `GITHUB_SCOUT_NO_BROWSER=1`) to keep it headless.
- First run of the settings dialog pre-selects the DeepSeek preset and prefills its endpoint and model, so a new user only has to paste an API key.
- Web history now persists to `%APPDATA%\GitHubScoutAgent\web_history.jsonl` instead of depending on the current working directory, so an installation under `Program Files` stays writable.
- Test suite expanded to 214 cases (22 configuration/settings tests including 5 keyless-loopback cases, plus 4 frozen-launcher tests).
- Build assets are isolated in `packaging/`; `build/` and `dist/` remain git-ignored, so the source tree keeps a single implementation.
- Converted all internal documentation links in `README.md` to clean repository-relative paths.
- Updated `README.md` header badge to official `Apache-2.0`.
- Synchronized `TASK_STATE.md` with baseline decision F2-06.

---

## [1.1.0] - 2026-10-27
### Added
- **Local Web Dashboard (FastAPI + SSE)**: Added `github-scout-agent web` command launching a local research dashboard at `http://127.0.0.1:8080`.
- **Real-Time Streaming Events**: Server-Sent Events (SSE) bridge displaying model thinking rounds, tool calls, and budget progress.
- **Report Export**: Added one-click Markdown clipboard copy and `.md` file download in the Web UI.
- **Search Response Trimming**: Pruned GitHub search results from 60+ fields down to 18 core fields, reducing payload bytes by >90% (~50KB to ~3KB per search).
- **Network Resilience**: Added exponential backoff retry mechanism (default 2 retries) to `OpenAICompatibleClient` to absorb TLS handshake jitter and transient network drops.
- **UI Hardening**: Added `notranslate` guards to prevent browser translation extensions from corrupting DOM button state; ensured reset in `finally` blocks.
- **Expanded Test Suite**: Extended automated offline test suite to 184 test cases (100% pass rate).

### Changed
- Increased CLI default `max_response_bytes` from 64 KiB to 256 KiB to ensure sufficient budget for multi-candidate README inspections.
- Enforced `--require-readme` gating when user query explicitly mentions README or documentation keywords.
- Decoupled candidate discovery from acceptance to prevent premature candidate capacity saturation.

### Fixed
- Fixed empty candidate early finalization bug where model claimed completion with zero valid candidates.
- Fixed closure late-binding issue in dynamic tool handler dispatch.
- Fixed budget overflow on large GitHub repository searches (`max_feedback_bytes` expansion).

---

## [1.0.0] - 2026-10-27
### Added
- **Core CLI MVP**: Added Typer CLI entrypoint `github-scout-agent` with commands: `run`, `config`, `version`, `mock-demo`, `live-agent-demo`, `gate-a`, `gate-b`.
- **Five Read-Only GitHub Tools**:
  - `search_public_repositories`
  - `get_public_repository`
  - `get_public_readme` (with 64 KiB safe truncation)
  - `get_repository_license`
  - `get_repository_languages_and_topics`
- **Deterministic 5-Dimensional Budget Guard**: Hard limits for model calls, GitHub requests, task duration, response bytes, and candidate count.
- **OpenAI-Compatible Tool Calling**: Structured JSON tool calling support tested across DeepSeek and Alibaba Qwen endpoints.
- **Zero-Execution Security Boundary**: Enforced GET-only API traffic, strictly locked to `api.github.com`, with zero local clone or shell execution.
