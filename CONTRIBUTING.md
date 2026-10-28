# Contributing to GitHub Scout Agent

Thank you for your interest in contributing to **GitHub Scout Agent**! This document provides guidelines for local development, testing, branching strategy, and pull request submissions.

---

## 🛠️ Development Setup

### 1. Prerequisites
- Python 3.11, 3.12, or 3.13
- Git 2.40+

### 2. Environment Setup (Windows PowerShell)
```powershell
# Clone the repository
git clone https://github.com/opheliafarrant/github-scout-agent.git
cd "github-scout-agent"

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install in editable mode with test dependencies
pip install -e ".[test]"
```

*(Linux / macOS: use `source .venv/bin/activate`)*

---

## 🌿 Branching Strategy & Rollback Safety

We follow a strict, evidence-based branching model to ensure baseline stability:

1. **Frozen Release Baselines**:
   - `v1.0.0` (Commit `df3b4ce`): CLI MVP Baseline.
   - `v1.1.0` (Commit `71802a9`): V1.1 Production & Web UI Baseline.
2. **Feature Branching**:
   - Always branch new features or experimental refactoring from the latest stable release tag `v1.1.0`:
     ```powershell
     git checkout -b feat/my-new-feature v1.1.0
     ```
3. **Master Branch**:
   - The `master` branch tracks ongoing maintenance, post-release budget adjustments, documentation enhancements, and integrated releases.
   - **Note for Developers & Agents**: Do not confuse the latest commit on `master` with the frozen release tag `v1.1.0`.

---

## 🧪 Testing & Verification

Every pull request or commit must pass the full offline test suite:

```powershell
# Run the complete offline test suite (184 tests)
python -m pytest -m "not live_github" -q

# Test the FastAPI Web dashboard integration
python -m pytest tests/test_web_server.py -q
```

### Verification Standards (Protocol V3.1)
- **Physical Evidence Priority**: "Code ran without errors" does not equal "Verified". Tests must produce concrete assertions.
- **Do not weaken assertions**: Never delete existing test cases, weaken tolerances, or mock out failing core paths without architectural justification.
- **Strict Read-Only Guarantee**: Tools and agent loops must remain strictly GET-only and zero-execution. Never introduce shell execution, git clone, or write endpoints into the agent's toolset.

---

## 📝 Commit Guidelines

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
- `feat`: New user-facing features or tools.
- `fix`: Bug fixes, budget tuning, or error handling enhancements.
- `docs`: Documentation updates, changelogs, or task state synchronization.
- `test`: Adding or updating test cases.
- `refactor`: Internal refactoring that does not alter public behavior.

---

## 🛡️ Security & Privacy Guidelines

- **Zero Credential Leaks**: Never hardcode API keys, tokens, or personal secrets in code, tests, logs, or commit messages.
- **Untrusted Input Handling**: All remote README documents and repository descriptions must continue to be treated as untrusted text with strict size boundaries.
