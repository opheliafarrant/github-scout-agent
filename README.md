# GitHub Scout Agent

<p align="center">
  <strong>A read-only, evidence-backed GitHub technical research & repository scouting agent.</strong><br>
  面向技术选型、开源候选调研与技术决策分析的只读证据驱动型自主智能体。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v1.2.0-blue.svg" alt="Version">
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg" alt="Python Version">
  <img src="https://img.shields.io/badge/tests-360%20passed-success.svg" alt="Test Status">
  <img src="https://img.shields.io/badge/architecture-Read--Only%20GET%20%7C%20Zero--Execution-brightgreen.svg" alt="Architecture">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-brightgreen.svg" alt="License"></a>
</p>

---

## 💡 核心价值与解决的痛点

在进行架构选型、技术评估或寻找优质开源库时，开发者通常面临以下挑战：
- **通用大模型的事实幻觉**：直接询问常规 LLM 经常会编造不存在的 GitHub 仓库名称、过时的 API 或虚构的功能特性；
- **人工调研繁重低效**：需要在 GitHub 逐个搜索、筛选候选、翻阅长篇 README、核实 SPDX 许可证、确认多语言与最近活跃度，再手工汇总整理对比；
- **决策缺乏真实依据**：许多技术选型报告停留在主观感受，缺乏真实的引用链接与一手文档佐证。

**GitHub Scout Agent** 通过“受限智能体循环 + 真实 GitHub REST API + 严格证据门控”，实现了全自动化的开源调研闭环：
1. **真实发现**：根据用户自然语言需求（例如 *“寻找三个支持本地私有化部署的 Python RAG 框架”*），自主规划并执行精准关键词搜索与多语言过滤；
2. **证据调查**：逐一抓取真实候选仓库的元数据、SPDX 许可证与完整 README 文档，提取核心特性、依赖环境与部署要求；
3. **横向对比**：自动过滤不相关的商业 SaaS 或闭源平台，横向对比切分策略、向量后端、LLM 支持与部署复杂度；
4. **决策报告**：输出带有真实仓库链接、多维比对表、证据充足度标记（`complete` / `partial`）与最终选型建议的结构化 Markdown 报告。

---

## 🌟 V1.1 核心特性一览

| 模块 | 特性说明 |
| :--- | :--- |
| **🌐 本地 Web 仪表盘** | 内置基于 FastAPI + SSE (Server-Sent Events) 的单页交互界面，毫秒级流式呈现思考过程、API 调用与预算消耗；支持本地 JSONL 持久化任务历史（服务重启不丢失）、线程安全互斥防护、结构化报告一键复制与 Markdown (.md) 导出。 |
| **⌨️ 功能完备的 CLI 引擎** | 支持生产级实时调研 (`run`)、纯离线 Mock 回路验证 (`mock-demo`)、真实 GitHub API 连通性探活 (`live-agent-demo`) 以及模型协议预检 (`gate-a` / `gate-b`)。 |
| **🔍 深度证据闭环** | 实施 Search-First 时序引导与证据门控；必须通过 README 调查获得一手证据才可完成决策，严禁仅凭搜索列表主观猜测。 |
| **⚡ 检索响应裁剪** | 将 GitHub 原始 60+ 字段裁剪至 18 个核心消费字段，单次检索响应字节缩减 90%+（~50KB 降至 ~3KB），大幅节省上下文与预算。 |
| **🛡️ 确定性预算护栏** | 模型调用轮次、GitHub 请求数、任务总耗时、响应字节与采纳候选数 5 维硬性拦截，杜绝死循环与意外账单，边界触发时平滑收敛为最终报告。 |
| **🔄 传输层抖动重试** | 针对 OpenAI 兼容端点内置指数退避重试机制（默认 2 次），有效吸收 TLS 握手波动、偶发超时与连接中断。 |
| **🔒 严格只读与凭证安全** | 仅允许官方 `api.github.com` 的 GET 请求；绝不执行 `git clone`，绝不运行任何第三方代码；所有 API Token 与 Key 均在内存中安全脱敏，绝不进入模型 Prompt、日志、报告或 Git 仓库。 |

---

## 🏗️ 系统架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                             User Entrypoints                             │
│           Web Dashboard (FastAPI + SSE)   │   CLI (Typer App)            │
│            http://127.0.0.1:8080       │   github-scout-agent            │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     Application Orchestration Layer                      │
│               run_research_task() & SSE / CLI Event Bridge               │
└────────────────────┬─────────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                     Constrained Agent Research Loop                      │
│   ┌──────────────────────────┐  ┌───────────────────────────────┐        │
│  │  Budget Guard (5 Dimensions)│◄─►│  Policy & Evidence Controller │     │
│  │  Models / Requests / Bytes  │  │  Search-First / Gate A / B     │     │
│  └──────────────────────────┘  └───────────────────────────────┘         │
└────────────────────┬──────────────────────────────────────┬──────────────┘
                     │                                      │
                     ▼                                      ▼
┌────────────────────────────────────────┐  ┌──────────────────────────────┐
│          GitHub Tool Registry          │  │   OpenAI-Compatible Client   │
│  - search_public_repositories          │  │  - Standard Tool Calling     │
│  - get_public_repository               │  │  - Exponential Backoff Retry │
│  - get_known_repository                │  │  - DeepSeek / Qwen / OpenAI  │
│  - get_public_readme (64KB Cut)        │  └──────────────────────────────┘
│  - replace_candidate_with_readme       │                                  
│  - get_repository_license              │                                  
│  - get_repository_languages_and_topics │                                  
└────────────────────┬───────────────────┘                                  
                     │ (GET-Only / Safe Host)
                     ▼
  ┌──────────────────────────────────────┐
  │       Official api.github.com        │
  └──────────────────────────────────────┘
```

---

## 🚀 快速上手

### 1. 环境准备
项目要求 **Python ≥ 3.11**（已在 Python 3.13 验证）：

```powershell
# 1. 克隆仓库并进入目录
git clone https://github.com/opheliafarrant/github-scout-agent.git
cd "github-scout-agent"

# 2. 创建并激活虚拟环境 (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装项目依赖与测试套件
pip install -e ".[test]"
```

*(Linux / macOS 用户使用 `source .venv/bin/activate` 激活)*

### 2. 配置模型与服务端点
GitHub Scout Agent 采用标准的 OpenAI-compatible Tool Calling 协议，兼容 **DeepSeek**、**通义千问 (Qwen)**、**OpenAI**、**Ollama** 等主流服务。

**方式一：图形界面配置（推荐，保存即生效，无需重启）**

启动 `github-scout-agent web` 后，点击右上角 **⚙ 设置**，或点击页面顶部「尚未配置模型接口」提示条中的「去配置」按钮：

1. 选择服务预设（DeepSeek / 通义千问 / OpenAI / 本地 Ollama / 自定义），自动填入对应的 Base URL 与模型名；
2. 填入 API Key 后点「**获取模型列表**」，面板会向该端点请求 `/models` 并把**全部**可用模型（实测某中转服务返回 183 个）装进一个独立的下拉选择框——不用再猜模型名。随后面板会对每个模型发一次极短探测请求，**按 Tool Calling 能力过滤**：本 Agent 是严格的 Tool Calling 客户端，不支持 tools 参数的模型根本跑不了任务，所以默认只把已确认支持的模型留在下拉框里（打 ✓ 标记），不支持的打 ✗、未判定的打 ?，取消勾选「只显示通过 Tool Calling 检测的模型」即可看到全部；检测结论同样按地址缓存在本机 24 小时，可随时点「停止检测」中断。该列表按 Base URL 缓存在本机，**刷新页面或重开设置后仍然可选**；地址一改，下拉自动切换到该地址自己的缓存；
3. 点击「保存」，徽章即刻变为 `✓ <model> ready`。保存时会顺带做一次连通性验证：Key 被拒、地址不通、模型名不在该服务的清单里，都会当场红字提示，不必等到跑任务才暴露。**本地 Ollama 等回环端点不需要 Key**，选好预设直接保存即可。

配置文件位于 `%APPDATA%\GitHubScoutAgent\config.json`：

- API Key 与 GitHub Token 使用 **Windows DPAPI** 加密落盘，磁盘上不存明文；
- `/api/settings` 与页面均不回显密钥，仅返回 `sk-…abcd` 形式的掩码；
- 已保存的 Key 留空即保持原值，不会因再次保存而被清空；
- 地址强制 HTTPS，仅 `localhost` / `127.0.0.1` / `[::1]` 允许 HTTP（用于本地 Ollama）。

**方式二：环境变量（可选，优先级高于配置文件）**

环境变量始终覆盖图形界面配置，适合 CI 与脚本环境注入凭证（Windows PowerShell 示例）：

```powershell
# 必需：模型服务 Base URL 与 API Key
$env:GITHUB_SCOUT_MODEL_BASE_URL="https://api.deepseek.com"     # 或通义千问等兼容端点
$env:GITHUB_SCOUT_MODEL="deepseek-chat"                         # 或 qwen-max, gpt-4o 等
$env:GITHUB_SCOUT_MODEL_API_KEY="sk-your-model-api-key"

# 可选：模型行为微调
$env:GITHUB_SCOUT_MODEL_THINKING_MODE="disabled"               # disabled (默认) 或 enabled
$env:GITHUB_SCOUT_MODEL_STRICT_TOOLS="false"                   # false (默认) 或 true
$env:GITHUB_SCOUT_MODEL_MAX_TOKENS="1024"                      # 最大单轮输出 Token

# 可选：GitHub Token（未配置时默认使用公开匿名速率 60次/小时；配置后支持 5000次/小时）
$env:GITHUB_TOKEN="ghp_your_read_only_github_token"
```

### 3. 配置自检
通过只读命令快速验证当前环境与端点配置，绝不回显密钥明文：

```powershell
github-scout-agent config
github-scout-agent version
```

---

## 💻 两种使用方式

### 方式 A：本地 Web 仪表盘（推荐日常交互）

启动本地轻量 Web 服务器，在浏览器中直观追踪调研全流程：

```powershell
# 启动 Web 服务（默认监听 127.0.0.1:8080）
github-scout-agent web

# 或指定自定义端口
github-scout-agent web --host 127.0.0.1 --port 8080
```

打开浏览器访问 `http://127.0.0.1:8080`：
- **实时流程观察**：实时展现模型思考轮次、GitHub 工具调用记录、README 抓取进度与 5 维预算动态水位；
- **结构化结果**：渲染候选项目卡片（Stars、License、主要语言）、横向对比表格与决策建议；
- **一键导出**：支持 `📋 Copy Markdown` 一键复制完整报告，或 `📥 Export .md` 直接下载为本地 Markdown 文件；
- **历史记录复查**：左侧边栏自动记录本地运行历史，随时点击复查历史调研成果；
- **图形化配置**：右上角 ⚙ 设置面板支持预设端点选择、模型列表全量下拉选择（读取端点 `/models`，按地址缓存、刷新不丢）、**按 Tool Calling 能力自动筛选模型**（逐个探测，结论缓存 24 小时）、保存即做连通性验证、API Key 加密保存与即时生效，无需手工设置环境变量或重启进程。

#### 🖥️ 桌面版（Windows .exe，分发给他人时使用）

不需要对方安装 Python，双击即用：

```powershell
# 由开发者打包（产物不入库）
pip install "pyinstaller>=6.11,<7"
python -m PyInstaller packaging/github_scout_agent.spec --noconfirm
```

产物为 `dist/GitHubScoutAgent/GitHubScoutAgent.exe`（无控制台窗口）。双击后：自动监听 `127.0.0.1:8080` → 自动打开默认浏览器 → 显示设置引导，对方只需粘贴 API Key 即可开始调研。若已有实例在运行，再次双击只会唤出浏览器而不会重复起服务。配置与历史落盘在 `%APPDATA%\GitHubScoutAgent\`，卸载程序不会带走个人配置。

#### 📦 Windows 安装包（推荐分发给非开发用户）

从 [Releases](https://github.com/opheliafarrant/github-scout-agent/releases) 下载 `GitHubScoutAgent-1.2.0-setup.exe`，双击按向导安装（每用户安装，不需要管理员权限）：

- 默认安装到 `%LOCALAPPDATA%\Programs\GitHub Scout Agent`，自动创建开始菜单快捷方式，桌面图标可选；
- 安装包、快捷方式与卸载项统一使用 `packaging/github_scout_agent.ico` 应用图标；
- 卸载（设置 → 应用 → GitHub Scout Agent）只移除程序本体，`%APPDATA%\GitHubScoutAgent\` 内的配置与历史保留；
- 静默安装：`GitHubScoutAgent-1.2.0-setup.exe /VERYSILENT /CURRENTUSER`。

安装包由 `packaging/installer.iss` 经 Inno Setup 编译生成（`ISCC.exe packaging\installer.iss`，产物输出到 `dist/installer/`，均为构建产物、不入库）。

### 方式 B：CLI 命令行调研（适合无图形界面与脚本批处理）

直接在终端执行端到端调研。为防止误触产生网络流量，线上真实运行要求显式附带 `--confirm-live`：

```powershell
# 1. 基础自然语言调研
github-scout-agent run "find three open-source Python RAG frameworks supporting local deployment" --confirm-live

# 2. 深度定制调研预算
github-scout-agent run "find high performance C++ actor model frameworks" --confirm-live `
  --max-model-calls 15 `
  --max-github-requests 30 `
  --max-candidates 5 `
  --max-task-seconds 300
```

### 离线演示与无损探活（0 API 消耗）
在没有配置模型 Key 或处于离线环境时，可使用内置 Mock 套件安全体验 Agent 交互循环：

```powershell
# 1. 离线 Agent Loop 闭环演练（演示空结果自动改写与预算消耗）
github-scout-agent mock-demo

# 2. 真实 GitHub REST + Mock LLM 链路探活（仅调用公开 GET 验证网络）
github-scout-agent live-agent-demo --scenario search
github-scout-agent live-agent-demo --scenario repository

# 3. OpenAI-compatible Tool Calling 协议离线预检
github-scout-agent gate-a --dry-run
github-scout-agent gate-b --dry-run
```

---

## 🔬 验证矩阵与实测凭据 [E3]

项目遵循**“物理证据优先”**与**“运行不报错 ≠ 验证通过”**的质量原则：

### 1. 离线自动化测试套件
```powershell
pytest -m "not live_github" --tb=short
```
- **测试结果**：**248 passed, 1 deselected**（耗时 ~8 秒，100% 通过）。
- **覆盖核心机制**：
  - 七大 GitHub 工具参数 Pydantic 校验与 GET-only 拦截；
  - 预算硬性拦截、候选容量满载与平滑降级；
  - 搜索结果精简裁剪（60+ 字段裁剪至 18 字段，减少 90%+ 报文大小）；
  - 多轮反馈循环、重复调用熔断、异常分类与指数退避重试；
  - FastAPI 路由、SSE 事件流、任务历史本地持久化与并发互斥保护、Markdown 成果导出；
  - 静态架构健康度 Brooks-Lint 90/100 分（0 Critical, 0 Warning）。

### 2. 真实多模型线上端到端闭环 [E3]
- **DeepSeek (`deepseek-flash` @ `api.deepseek.com`)**：通过 CLI 与 Web UI 实测 6 轮交互端到端跑通，自动吸收首轮握手抖动，排除商业平台，生成规范四段式对比决策报告。
- **通义千问 (`qwen-max` @ `maas.qianwenaiapi.com`)**：实测 3 次模型调用、4 次 GitHub GET 跑通技术选型链路，自动抓取 README 补充证据并完成对比。

---

## 🗺️ 版本演进历史

| 版本 | 发布日期 | 定位与核心变更 | Git Tag | 状态 |
| :--- | :--- | :--- | :--- | :--- |
| **V1.2.0** | 2026-10-29 | **证据治理与发布工程**：证据台账 + 单一报告校验器重构、覆盖度分级（completeness 依实际证据分级并给出 partial 理由）、成熟度与元数据裁决门控、容量不可满足显式降级、Windows 安装包（Inno Setup）与自定义图标、360 项全自动化回归测试。 | `v1.2.0` | **当前正式版** |
| **V1.1.0** | 2026-10-27 | **Web UI 与生产级稳健性**：FastAPI + SSE 本地研究仪表盘、一键 Markdown 导出、搜索响应裁剪 90%+、模型网络指数退避重试、README 门控强化、184 项全自动化回归测试。 | `v1.1.0` (Commit `71802a9`) | 历史冻结 |
| **V1.0.0** | 2026-10-27 | **CLI MVP 核心引擎**：五大只读 GitHub 工具、受控 Agent 反馈循环、确定性 5 维预算、OpenAI 兼容 Tool Calling、Gate A/B 协议预检。 | `v1.0.0` (Commit `df3b4ce`) | 历史冻结 |

> 📌 **版本追溯提示**：正式版本标签 `v1.2.0` 永久锚定于发布提交（即 `v1.2.0` tag 指向的 commit，安装包 `GitHubScoutAgent-1.2.0-setup.exe` 与该 tag 一一对应）；上一正式版 `v1.1.0` 锚定于 Commit `71802a9`。主分支 `master` 作为持续演进分支，包含后续工程配置调优、文档完善与发布维护提交。

---

## 🛡️ 安全模型与设计约束

1. **只读公开边界**：仅允许向官方 `api.github.com` 发送 GET 请求，禁止任何 POST/PUT/DELETE 操作；不支持私有仓库与账号管理；
2. **零本地代码执行**：绝不执行 `git clone`，不拉取完整仓库，不运行任何第三方 Python/Shell 脚本；
3. **凭证脱敏与隔离**：GitHub Token 与模型 API Key 仅保存在内存，严格禁止注入 LLM 上下文、打印在控制台、记录进日志或提交到 Git 仓库；
4. **不可信输入防护**：所有远程抓取的第三方 README 均标记为 `untrusted_remote_text`，设有 64 KiB 流式截断保护；
5. **合规使用声明**：本项目仅用于开源技术选型与学术技术调研，不得用于违背 GitHub 服务条款的大规模恶意抓取或滥用。

---

## 📄 开源许可证与社区规范

- **开源许可证**：本项目采用 [Apache License, Version 2.0](LICENSE) 开源许可证。允许自由使用、修改、分发及商业应用，并享有明确的专利授权保护。
- **第三方依赖兼容性**：项目核心依赖（`httpx`、`pydantic`、`typer`、`fastapi`、`uvicorn`、`pytest`）均采用极宽松的 MIT 或 BSD-3 商业友好许可证，无任何传染性 Copyleft 风险。
- **版权声明**：Copyright 2026 Ophelia Farrant and contributors。详见 [NOTICE](NOTICE)。
- **变更日志**：详见 [CHANGELOG.md](CHANGELOG.md)。
- **贡献与开发规范**：详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
