# Changelog

All notable changes to **GitHub Scout Agent** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]
### Added
- Standardized project documentation: upgraded `README.md` for V1.1, added `CHANGELOG.md` and `CONTRIBUTING.md`.
- Calibrated Git commit topology and `TASK_STATE.md` baseline tracking for seamless multi-agent collaboration.

---

## [1.1.0] - 2026-10-27
### Added
- **Local Web Dashboard (FastAPI + SSE)**: Added `github-scout-agent web` command launching a local research dashboard at `http://127.0.0.1:8080`.
- **Real-Time Streaming Events**: Server-Sent Events (SSE) bridge displaying model thinking rounds, tool calls, and budget progress.
- **Report Export**: Added one-click Markdown clipboard copy and `.md` file download in the Web UI.
- **Search Response Trimming**: Pruned GitHub search results from 60+ fields down to 18 core fields, reducing payload bytes by >90% (~50KB to ~3KB per search).
- **Network Resilience**: Added exponential backoff retry mechanism (default 2 retries) to `OpenAICompatibleClient` to absorb TLS handshake jitter and transient network drops.
- **UI Hardening**: Added `notranslate` guards to prevent browser translation extensions from corrupting DOM button state; ensured reset in `finally` blocks.
- **Expanded Test Suite**: Extended automated offline test suite to 176 test cases (100% pass rate).

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
