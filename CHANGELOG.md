# Changelog

All notable changes to **GitHub Scout Agent** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [1.2.0] - 2026-10-29
### Added
- **Windows Installer and Application Icon**: added `packaging/installer.iss` (Inno Setup 7) producing a per-user installer, `GitHubScoutAgent-1.2.0-setup.exe`, that installs to `%LOCALAPPDATA%\Programs\GitHub Scout Agent` without an administrator prompt, creates Start Menu and optional desktop shortcuts, shares one app icon with the executable, and leaves `%APPDATA%\GitHubScoutAgent\` untouched on uninstall. `packaging/github_scout_agent.ico` is a seven-size icon (16-256 px) embedded in the frozen executable as well.
- **Release Engineering**: version metadata moved to 1.2.0 in `pyproject.toml`, the package, `packaging/version_info.txt` and the installer; the changelog now tracks `v1.2.0`, and the desktop build embeds the file/product version 1.2.0.0 so Explorer, the installer and the uninstall entry agree.
- **Graphical Model Configuration**: Added a `⚙ 设置` dialog to the Web UI with service presets (DeepSeek / Qwen / OpenAI / local Ollama / custom), a first-run callout on an unconfigured instance, inline HTTPS validation, and instant apply without restarting the process. Backed by new `GET /api/settings` and `PUT /api/settings` endpoints.
- **Save-Time Connectivity Check**: `PUT /api/settings` now probes the endpoint's `/models` route after persisting and returns a `verification` block (`ok` / `failed` / `unknown`); the dialog reports a rejected key, an unreachable host, or a model name the provider does not list immediately instead of waiting for the first task to fail. Settings are always saved first — a failed probe never discards input.
- **Model List Dropdown**: The settings dialog can now query the endpoint's OpenAI-compatible `/models` route (new `POST /api/settings/models`) and populate the model field with a dropdown. Providers that do not expose `/models` fall back to manual entry with an explicit hint; the request reuses the saved key when the field is left blank and never echoes the secret back.
- **Tool Calling Capability Filter**: `/models` advertises no capability metadata, so the dialog now probes every listed model with one tiny `tools`-bearing completion (new `POST /api/settings/models/probe`, concurrency 6, zero-token pre-filter skips embedding/speech/image models) and hides the ones that cannot call tools. Verdicts are cached per Base URL for 24 hours, a 401 aborts the batch instead of hammering a dead key, and the run can be stopped mid-flight. Without this, picking a plausible-looking model could only fail much later with `tool_calling_unsupported`.
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
- **Report Attribution and Zero-Result Fidelity (T66)**: A live capacity run marked an honest report `partial`
  four times over. A twelve-row comparison table has no blank line inside it, so one `Partial Match` cell turned
  every row into a verdict claim and named twelve repositories the report itself had labelled unverified; the
  fabrication gate read `README was **not fetched** in this session` and `readme.status = "unknown"` as claims
  about README content; `No candidate qualifies as a Verified Match` was read as an untested requirement although
  the report named the candidate it graded `Partial Match (metadata only)`; and `I recommend the following next
  steps` raised an alert naming the English word `following`. Table rows and top-level list items are now their
  own attribution domain, the README gate separates current-run assertions from status rows, manual-review advice
  and plans for a future run (`Re-run this investigation with explicit instructions to fetch READMEs`), a
  zero-result claim is credible once it names an inspected candidate, and the verification-level negation covers
  passive forms (`cannot be declared a Verified Match`). A repository recommended on search metadata alone is now
  reported through its own channel - `Evidence Level Alert` / `unverified_recommendation` - instead of
  masquerading as fabrication, and a candidate the report itself excludes is no longer charged for a missing
  README: reading a README and judging a candidate out are alternatives, not two obligations.
- **Evidence Policy Note Missed a Rephrased Refusal (T64.7)**: The task "不需要你调用任何工具" (with a pronoun
 and a verb between the negation and the noun) did not match the ordered-phrase refusal regex, so a run that
 spent 8 GitHub requests against an explicit "do not call any tools" produced no disclosure — the same
 transparency hole as before, in a different phrasing. Detection is now clause-level co-occurrence (a negation
 token and a tool/evidence noun in the same clause), which handles filler words while keeping "不要推荐低星的
 玩具项目" and plain selection tasks as non-triggers.
- **Candidate Evidence Standing and Known-Project Dead End (T64.6)**: A production-RAG run rendered a 1-star,
 five-month-old search hit (`Soliman2020/rag-cost-control-layer-II`) in the same "Candidate evidence" block, with
 the same fields, as the README-verified recommendation — while the task explicitly asked to exclude recently
 created projects, and the section carried no evidence level at all. Candidate rows now render
 `Evidence level: README <status> (fetched in this session)` or `metadata only — README not requested`;
 README-verified rows are listed first, and a row below 50 stars and 183 days old carries a deterministic
 `Maturity: immature` note. Separately, `get_known_repository` resolves only five curated coding-agent projects,
 so a RAG task asking for "llamaindex" burned a tool call on `unknown_known_project`; its description now states
 the complete five-name scope and the error message points at `search_public_repositories` with `repo:owner/name`.
- **Search-Loop Budget Burn and Metadata-Only Verdicts (T64.5)**: A live table-extraction run spent
 six of twelve model calls re-phrasing queries that returned zero candidates, then labelled
 PaddleOCR "Verified Match (based on metadata)" because no model call was left to fetch its README —
 the verdict contradicted the run's own gap "README not checked: paddlepaddle/paddleocr".
 (a) Two consecutive search batches that yield no candidates now emit `search_strategy_change_required`
 and a strategy-change notice (reduce the query, or look a specific project up by name) instead of
 letting the model keep rephrasing; (b) `audit_verdict_evidence` flags any "Verified Match" claim for an
 inspected repository whose README was never requested, adding a gap and a header `Verdict Evidence Alert`
 that downgrades Completeness to partial, and `_finalize` now instructs a metadata-only candidate to be
 labelled "Partial Match (metadata only)".
- **History Sidebar Scroll and Deletion (T64.4)**: The task list on the left could neither scroll nor be pruned. 
The sidebar is now sticky with `max-height: calc(100vh - 32px)` and the list itself owns `overflow-y:auto`, so a long history scrolls inside the panel instead of pushing the page out of reach. 
Each entry carries a `✕` button (visible on hover, always focusable) that calls the new `DELETE /api/history/{task_id}`; 
`HistoryStore.delete()` rewrites the JSONL atomically and unlinks the file when the last entry goes, deleting the loaded report when the active task is removed, and an unknown id answers 404. 
- **Fabricated README Claims and Maturity False Positives (T64.3)**: Two live-run defects are closed. 
(1) A run that never fetched a README but wrote "三个候选仓库的 README content 均显示为 null" was only downgraded after the fact; 
finalization now detects a README-content assertion without any get_public_readme result, emits readme_claim_required, 
and spends one bounded correction call that tells the model to remove the claim or record the missing README in Evidence Gaps. 
The main loop applies the same nudge when a draft appears before finalization. 
(2) audit_maturity treated any mention of a young, low-star repository as a recommendation, so a Zig run that listed immature 
repos under "证据缺口与未知项" with "Stars 仅 6，不满足成熟度标准" was demoted to partial for the correct report. 
Mentions under evidence-gap/unverified/pending headings and mentions carrying an explicit immaturity caveat no longer alert; 
a young low-star repository named in a recommendation section still does.
- **Evidence Claim Accuracy and Investigation Depth (T64.2)**: Live runs showed the fabrication gate misfiring in both directions, so it now separates assertions from disclosures. A README phrase counts as invented evidence only when it asserts content (`README 显示/表明/content/returns`, `the README states …`); deferrals, denials and next steps (`需 README 验证`, `尚未通过 README 验证`, `| README 获取与验证 |`, `对每个通过 README 验证的仓库，分别查询 /contributors`) no longer produce a false `Evidence Fabrication Alert`. A rate-limited stop now instructs the model to label unread candidates `Pending Verification (rate limited)` and forbids describing README contents it never received. Two policy rules close the depth gap: an empty answer must rest on at least two investigated candidates and a second query formulation when budget allows, and a candidate/comparison table may not contain a repository that tools never inspected.
- Authentication failures now say why: the model boundary appends the provider's own error message (for example `Your api key … is invalid`) to `authentication_error`/`model_http_error`, with key-like tokens masked as `sk-***`. Previously the UI only showed "model authentication or permission failed", leaving key-versus-endpoint mismatches undiagnosable.
- Keyless local endpoints (ollama, LM Studio, vLLM on `127.0.0.1`) are now usable: `model_ready` accepts a loopback Base URL without an API key, and the client omits the `Authorization` header instead of refusing to start. Previously the shipped Ollama preset saved successfully but left the app permanently `not_configured`.
- Added the missing favicon so every page load no longer logs a 404.
- Associated the four budget inputs with their labels (`for`/`id`) and gave the task textarea an accessible name.
- Submitting an empty task now shows an actionable hint in the event feed instead of silently doing nothing.
- The model list is no longer a filtered `<datalist>`: it is a dedicated `<select>` fed by the endpoint's `/models` answer, so an endpoint offering 183 models shows all 183 instead of only the ones matching the text already typed into the field. The fetched list is cached per Base URL in `localStorage`, so reloading the page no longer empties the picker.
- Queries no longer drift to a broader category: the research policy now requires every search to preserve the task's distinctive subject, and reports must say so instead of presenting a generic category as the answer.
- README downloads no longer exhaust the response-byte budget: READMEs are fetched as raw text with a byte cap instead of pulling the whole base64 JSON document into memory (233 KB → 27 KB per run on the reference task).
- A budget-stopped run is now visible: the report header prints `Research stopped by budget: <dimension>` whenever a fatal dimension was hit, and every task keeps a per-message `feedback_trace` (`source`/`label`/`bytes`/`used`) that is persisted in history and returned by `GET /api/report/{id}`. `tool_finished` events now carry `result_bytes`, `feedback_bytes` and `feedback_bytes_used`, and a clip emits `feedback_clipped` with original and kept sizes.
- Truncated READMEs now count as evidence, and `replace_candidate_with_readme` records README evidence for its target; candidates retired by a replacement or never admitted by the candidate budget no longer force a spurious `partial` verdict.
- **Grounding False Positive and Evidence-Free Finish (T64 follow-up)**: Two defects found by the first comparison runs against a second model (glm-5.2-fast-preview) are closed. (1) A brand name now resolves to the inspected repository: `audit_grounding` maps `LangChain` onto `langchain-ai/langchain` when that repository was actually fetched, so a fully evidenced RAG comparison no longer reports `Evidence Attribution Alert … (LangChain)` and no longer downgrades itself to `partial`; a brand name whose repository was never inspected still alerts. (2) A Chinese recommendation request now demands README evidence: `requires_native_tool_calling_evidence` recognises 推荐/建议/挑选/选一个/哪个/方案/适合/生产可用, so "推荐一个扫描件 PDF 表格结构化方案" can no longer reach finalization after two metadata-only searches with zero candidates investigated (previously 2 model calls, 2 GitHub requests, 0 READMEs, 14.7 s, with the report claiming the readme bodies it never requested were null).
- **Evidence Integrity Hardening (T64)**: Five real-task failures found in acceptance testing are now gated in code and prompt. (1) Response-byte accounting is cumulative: `record_response_chunk` compares the running total, so a run can no longer report 259/256 KB while `exhausted_dimensions` stays empty. (2) Two consecutive searches without any README fetch, and finishing with zero verified candidates while budget remains, now trigger a `verification_round_required` nudge instead of an early empty conclusion. (3) Policy requires capability queries to describe the capability rather than a library name the model already knows, and metadata-based rejection is explicitly not permission to stop investigating. (4) The grounding audit recognises imperative recommendations (`1. Use PaddleOCR …`, `采用 X`), which previously escaped because a numbered bullet was parsed as a heading. (5) A maturity audit flags any recommended repository with fewer than 50 stars created less than six months ago unless the report carries an explicit maturity caveat, renders a `Maturity Alert`, and demotes completeness to `partial`. Reports now also disclose when an "answer without evidence" request was overridden by the evidence-first policy, when the requested option count exceeds the candidate budget, and when the task references a year other than the evidence date; candidate evidence renders `created` and `last push` so a "recent commits" requirement is checkable.
- **Search Maturity and Anti-Hallucination Guard (T62)**: `search_public_repositories` description and agent policy now explicitly guide the model to use `sort="stars"` or qualifiers (`stars:>50`) to prioritize mature, community-vetted repositories over 0-star toy projects. The finalization prompt dynamically provides an `INVESTIGATED CANDIDATES WHITELIST` and strictly forbids claiming uninspected repositories as `Verified Match`. A deterministic grounding audit in `AgentLoop` and `render_mvp_report` flags uninspected candidates claimed as verified, renders an `Evidence Grounding Alert`, demotes completeness to `partial`, and logs unverified claims in `Evidence Gaps`.

### Changed
- **Completeness Grading (T67)**: the report header used to claim a complete run whenever no alert fired, even when every verdict rested on metadata alone or the task asked for more options than the candidate budget can compare. Completeness is now graded by assess_completeness() in report_validator.py from what the ledger actually covers: an unfinished run, an evidence gap, any finding, zero accepted candidates, requested options exceeding max_candidates, or an accepted set with no README fetched. The reasons are printed inline, for example Completeness: partial (the task asked for 12 options while the candidate budget compares at most 3 (system limit); no accepted candidate was checked against its README, so every verdict rests on metadata alone). Alerts answer whether the report exceeded the evidence; completeness answers whether the session covered the request, so an alarm-free run can be partial. A request to skip READMEs (Chinese metadata-only phrasing, or metadata only) is now disclosed as an Evidence Policy Note with the number of READMEs actually fetched, and a toolchain word in recommendation prose (for example adopting Nix as a dependency) is no longer reported as an uninspected repository.
- **Evidence Ledger and One Report Validator (T65)**: the anti-hallucination checks no longer re-derive facts from the model prose. A new `evidence_ledger.py` records what the tools actually returned (search hit vs. metadata vs. README, plus stars / created / pushed / archived / licence) while the run happens, and a new `report_validator.py` consumes only that ledger to produce typed `Finding` codes (`ungrounded_repository`, `unattributed_verdict`, `metadata_only_verdict`, `immature_recommendation`, `fabricated_readme`, `zero_result_without_readme`). `agent_loop.py` shrank from 1765 to 1124 lines and now emits `findings` plus the ledger evidence on the result; the renderer prints those findings instead of recomputing them, so the report and its completeness flag can no longer disagree. The finalization whitelist now carries each candidate evidence level (`octo/repo (readme=available, stars=…, created=…)`), and a search hit is explicitly *not* counted as an investigated candidate.
- **Grounding Audit Rewritten (T63)**: the anti-hallucination gate is now one entry point, `audit_grounding()`, shared by the agent loop and the report renderer instead of two independently derived whitelists. Repository identity comes only from `github.com` URLs (bare `GPU/CPU`/`CI/CD` prose is no longer mistaken for a candidate), recommendation sections are detected in any language through heading-based structure rather than an English-only regex, and a verdict that names no inspected repository is reported separately as an unattributed claim. `AgentRunResult.investigated_repositories` carries the inspected whitelist, so the report no longer recomputes it.
- `github-scout-agent web` now opens the default browser automatically; pass `--no-browser` (or set `GITHUB_SCOUT_NO_BROWSER=1`) to keep it headless.
- First run of the settings dialog pre-selects the DeepSeek preset and prefills its endpoint and model, so a new user only has to paste an API key.
- Web history now persists to `%APPDATA%\GitHubScoutAgent\web_history.jsonl` instead of depending on the current working directory, so an installation under `Program Files` stays writable.
- Test suite expanded to 268 cases (37 configuration/settings tests including 5 keyless-loopback cases and the model-probe routes, 4 frozen-launcher tests, 13 research-loop evidence/query/grounding/budget tests, and 4 report grounding audit tests, plus 13 T63 grounding cases covering Chinese headings, bare brand names, heading variants, slash prose and honest gap disclosure).
- Prompt-size budgeting is now split: `max_feedback_item_bytes` (default 64 KiB) clips a single oversized tool payload, `max_feedback_bytes` (512 KiB in the Web UI and CLI) bounds the cumulative prompt feedback. Previously one large README aborted an otherwise healthy run with `feedback_bytes`, and the 128 KiB cumulative ceiling sat only 1.3–2.8× above normal usage.
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
