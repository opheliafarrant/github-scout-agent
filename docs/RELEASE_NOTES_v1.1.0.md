# GitHub Scout Agent v1.1.0 Release Notes & GitHub Metadata

> **文档说明**：本文件为 GitHub Scout Agent V1.1.0 正式发布准备材料，包含 GitHub 仓库建议元数据配置以及可在 GitHub 界面创建 Release 时直接复制使用的 Release Notes。

---

## 🏷️ GitHub 仓库设置建议 (Repository Settings & Metadata)

当您准备将仓库切换为公开（Public）时，建议在 GitHub 仓库主页右侧的 **About** 区域配置以下信息：

| 字段 | 推荐配置内容 |
| :--- | :--- |
| **Description** | `A read-only, evidence-backed GitHub technical research & repository scouting agent with local Web UI and Typer CLI.` |
| **Website** | *(可留空，或配置为您未来部署的在线演示/文档站点)* |
| **Topics** | `agent`, `github-api`, `research-agent`, `llm`, `fastapi`, `cli`, `tool-calling`, `read-only`, `python`, `pydantic`, `deepseek`, `qwen` |
| **Include in home** | ✅ Releases, ✅ Packages |
| **License** | `Apache-2.0` (由根目录 `LICENSE` 文件自动识别) |

---

## 🚀 GitHub Release 正式发布文本草案 (Release Draft)

在 GitHub 仓库页面点击 **Releases** -> **Draft a new release** 时，直接填入以下内容：

### Release Title
`v1.1.0 — Production-Ready Web UI, Streaming Feedback & Robust Research Loop`

### Tag & Branch
- **Tag**: `v1.1.0` (选择已存在的 Tag `v1.1.0`)
- **Target**: Commit `71802a9`

### Release Description

```markdown
## What's Changed in GitHub Scout Agent v1.1.0

GitHub Scout Agent v1.1.0 marks the transition from our initial CLI MVP into a robust, production-ready research platform featuring a local Web UI, real-time streaming feedback, and significant operational hardening.

### 🌟 Key Highlights

1. **Local Web Dashboard (FastAPI + SSE)**:
   - Run `github-scout-agent web` to launch an interactive browser dashboard at `http://127.0.0.1:8080`.
   - Real-time Server-Sent Events (SSE) stream model thinking steps, GitHub API calls, and budget progress.
   - Interactive candidate cards, side-by-side comparison tables, and evidence gap indicators.
   - One-click **Markdown Export (`.md`)** and clipboard copy.

2. **90%+ Search Payload Trimming**:
   - Pruned GitHub repository search responses from 60+ fields down to 18 essential fields.
   - Slashed single-search response size from ~50 KB to ~3 KB, drastically reducing token consumption and task budget footprint.

3. **Transport-Layer Network Resilience**:
   - Added exponential backoff retry mechanism (up to 2 retries) to `OpenAICompatibleClient`.
   - Seamlessly absorbs transient TLS handshake jitter and connection drops common with remote LLM endpoints.

4. **Deep Evidence Gating & Candidate Decoupling**:
   - Model is strictly required to inspect repository READMEs before claiming completion.
   - Query intent penetration automatically enforces README inspection when keywords like `readme` or `documentation` are present.
   - Decoupled candidate discovery from acceptance to prevent premature candidate capacity saturation.

5. **Formal Open-Source Licensing (Apache-2.0)**:
   - Standard Apache License, Version 2.0 adopted with comprehensive third-party dependency attribution (`NOTICE`).

---

### 🔬 Test & Empirical Verification Matrix [E3]

- **Offline Automated Tests**: 184 passed (100% pass rate in ~5 seconds).
- **Static Code Quality**: Brooks-Lint Health Score 90/100 (0 Critical, 0 Warning).
- **Live Model Endpoint Verification**:
  - DeepSeek (`deepseek-flash` @ `api.deepseek.com`): Verified end-to-end multi-turn research loop.
  - Alibaba Qwen (`qwen-max` @ `maas.qianwenaiapi.com`): Verified candidate discovery, README inspection, and structured recommendations.

---

### 📦 Quick Start (Windows / Linux / macOS)

```bash
# Clone & install
git clone https://github.com/opheliafarrant/github-scout-agent.git
cd github-scout-agent
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[test]"

# Configure your OpenAI-compatible endpoint
export GITHUB_SCOUT_MODEL_BASE_URL="https://api.deepseek.com"
export GITHUB_SCOUT_MODEL="deepseek-flash"
export GITHUB_SCOUT_MODEL_API_KEY="sk-your-model-api-key"

# Launch the Web Dashboard
github-scout-agent web
```

---

### 🛡️ Security Model & Guarantees

- **Strictly Read-Only (GET-only)**: Locked to `api.github.com`. Zero POST/PUT/DELETE operations.
- **Zero Local Code Execution**: No `git clone`, no external build scripts, no arbitrary shell tools.
- **Credential Isolation**: GitHub tokens and LLM API keys are memory-only; never logged, never pushed to Git, and never sent to LLMs.
- **Deterministic Budget Guard**: Hard enforcement across 5 dimensions (model calls, GitHub requests, elapsed time, response bytes, and candidate count).

---

### 🗺️ Evolution from v1.0.0

| Feature | v1.0.0 (CLI MVP) | v1.1.0 (Current Release) |
| :--- | :--- | :--- |
| **User Interface** | Typer CLI only | Typer CLI + FastAPI Local Web UI (SSE Streaming) |
| **Result Export** | Terminal Markdown print | Terminal + Web Clipboard Copy + Direct `.md` Download |
| **Search Payload** | Full raw GitHub payload (~50KB) | Trimmed 18 fields (~3KB, 90%+ reduction) |
| **Network Retries** | None (failed on handshake jitter) | Exponential backoff retry for OpenAI-compatible APIs |
| **Evidence Gating**| Basic heuristic | Strict `--require-readme` & intent-driven keyword gating |
| **Test Suite** | 120+ tests | 184 automated tests |
| **License** | Evaluation phase | Apache License 2.0 |
```
