# GitHub Scout Agent (CLI MVP V1.0)

> **A read-only, evidence-backed GitHub research agent.**  
> 一个只读、有证据支持的 GitHub 开源技术选型与候选调研 Agent。

用户以自然语言输入技术需求，Agent 自主通过受控循环检索 GitHub 公开候选仓库，补充多维证据（README、License、语言与 Topics、Stars/Forks），进行事实核验与透明对比，最终输出带有真实来源链接与不确定性标注的决策推荐报告。

---

## 🎯 核心定位与安全边界

GitHub Scout Agent 遵循严格的防御性与可核验安全设计：

| 维度 | 安全约束与策略 |
| :--- | :--- |
| **只读操作** | 严格仅允许 GitHub REST `GET` 请求；禁止任何 `POST`、`PUT`、`PATCH`、`DELETE` 操作；不支持私有仓库与账户管理。 |
| **零代码执行** | 绝不执行 `git clone`，不拉取完整代码仓库，不执行外部构建脚本，绝不提供任意 Shell 或系统执行工具。 |
| **域名与宿主锁定** | 网络层严格白名单锁定为官方 `api.github.com`；模型层仅与用户显式配置的 OpenAI 兼容端点通信。 |
| **凭据绝对隔离** | 支持无 Token 模式与用户自配只读 `GITHUB_TOKEN`；任何 Token 与 API Key 绝不注入模型 Prompt、不输出日志、不进入报告与版本控制。 |
| **确定性程序预算** | 由程序硬性拦截并控制 5 类预算：模型调用轮次、GitHub 请求数、任务总耗时、响应字节大小、候选仓库数量，防止失控死循环与意外账单。 |
| **远程文本不可信标记** | 所有远程抓取的 README 文本均标注为 `untrusted_remote_text`，如遇超限则安全截断，且事实证据不足时如实降级标示 `partial`。 |
| **时序引导 (Search-First)** | 系统级提示词强制模型先检索确定候选，禁止在搜索结果返回前凭空臆造仓库并发抓取详情。 |

---

## 🏗️ 架构概览

```text
┌────────────────────────────────────────────────────────┐
│                   CLI Entry (Typer)                    │
│    run / mock-demo / live-agent-demo / gate-a / gate-b │
├────────────────────────────────────────────────────────┤
│                   Application Layer                    │
│      run_research_task() & render_mvp_report()         │
├────────────────────────────────────────────────────────┤
│                  Constrained Agent Loop                │
│    Feedback Loop / Budget Guard / Search-First Policy  │
├──────────────────────────┬─────────────────────────────┤
│   OpenAI-Compatible Client│     GitHub Tool Registry    │
│  Chat Completions + Tools│  5 Read-Only Fixed GitHub Tools
├──────────────────────────┴─────────────────────────────┤
│         GitHub REST Client (HTTPX GET-Only, Fixed Host) │
├────────────────────────────────────────────────────────┤
│           Budget / Event Recorder / Config             │
└────────────────────────────────────────────────────────┘
```

### 五大只读 GitHub 工具
1. `search_public_repositories`: 自然语言公开仓库搜索（支持语言与排序条件过滤）。
2. `get_public_repository`: 目标仓库基础元数据（描述、Star 数、分支、归档与可见性状态）。
3. `get_public_readme`: 仓库 README 文档提取（Base64 安全解码与长度保护截断）。
4. `get_repository_license`: SPDX 许可证信息提取。
5. `get_repository_languages_and_topics`: 仓库多语言占比与 Topics 标签组合查询。

---

## 🚀 快速上手

### 1. 环境准备
本项目要求 **Python ≥ 3.11**（已在 Python 3.13 验证）：

```powershell
# 1. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安装当前项目及测试依赖
python -m pip install -e ".[test]"
```

### 2. 环境配置
通过环境变量注入模型接入点与凭据（支持任何标准的 OpenAI-compatible Chat Completions 服务）：

```powershell
# 必需：模型服务 Base URL 与 API Key
$env:GITHUB_SCOUT_MODEL_BASE_URL="https://api.deepseek.com" # 或通义千问等端点
$env:GITHUB_SCOUT_MODEL="deepseek-flash"                    # 或 qwen-max, deepseek-chat 等
$env:GITHUB_SCOUT_MODEL_API_KEY="sk-your-model-api-key"

# 可选：模型行为调优
$env:GITHUB_SCOUT_MODEL_THINKING_MODE="disabled"           # disabled (默认) 或 enabled
$env:GITHUB_SCOUT_MODEL_STRICT_TOOLS="false"               # false (默认) 或 true
$env:GITHUB_SCOUT_MODEL_MAX_TOKENS="1024"                  # 响应最大 Token

# 可选：GitHub Token（未配置时默认使用公开未认证速率 60次/小时；配置后支持 5000次/小时）
$env:GITHUB_TOKEN="ghp_your_read_only_github_token"
```

配置检查命令（安全检查配置状态，绝不回显密钥本身）：
```powershell
github-scout-agent config
github-scout-agent version
```

---

## 💻 命令行使用指南

### 1. 真实自然语言调研（核心生产路径）
运行技术调研任务。为了防止意外产生网络消耗，生产命令要求显式附带 `--confirm-live`：

```powershell
# 基础运行（默认使用 10 次模型调用、20 次 GitHub GET、3 个候选仓库预算）
github-scout-agent run "find small Python agent repositories with native tool calling" --confirm-live

# 深度调研（自定义放宽预算）
github-scout-agent run "find high performance C++ actor model frameworks" --confirm-live `
  --max-model-calls 15 `
  --max-github-requests 30 `
  --max-candidates 5 `
  --max-task-seconds 300
```

### 2. 离线模拟与集成演示
在没有模型 Key 或网络受限时，使用内置 Mock 工具链体验完整的 Agent 反馈回路：

```powershell
# 1. 纯离线 Agent Loop 闭环演示（展示空结果查询自动改写与预算扣减）
github-scout-agent mock-demo

# 2. 真实 GitHub REST + Mock LLM 接线演示（仅验证公开 GET 链路，不消耗模型 API）
github-scout-agent live-agent-demo --scenario search
github-scout-agent live-agent-demo --scenario repository

# 3. DeepSeek / OpenAI 协议离线预检（验证 Tool Calling 请求体、Schema 与响应解析）
github-scout-agent gate-a --dry-run
github-scout-agent gate-b --dry-run
```

---

## 🔬 验证矩阵与实测凭据 [E3]

项目坚持**“物理证据优先”**与**“运行不报错 ≠ 验证通过”**的工程原则，拥有完整的双层验证网络：

### 1. 离线自动化测试套件
```powershell
pytest -m "not live_github" --tb=short
```
- **测试通过率**：**158 passed, 1 deselected**（耗时 < 1 秒）。
- **覆盖范围**：
  - 工具注册、参数 Pydantic 校验与只读 GET 调度；
  - 预算硬性拦截、候选容量满载与非致命降级；
  - 多轮反馈循环、重复调用熔断、协议重试耗尽保护；
  - OpenAI-compatible Tool Calling 序列化与错误安全映射；
  - 模型传输层网络异常分类（TLS、Timeout、ConnectError 安全脱敏）；
  - 多语言意图解析、语言元数据过滤与候选证据充足度判定。

### 2. 真实多模型端点验证 [E3]
- **DeepSeek (`deepseek-flash` @ `api.deepseek.com`)**：通过 Gate A 协议预检与 T35 在线实测验收，成功验证工具描述下发与参数安全解析。
- **Qwen-Max (`qwen-max` @ `maas.qianwenaiapi.com`)**：实测 3 次模型调用、4 次 GitHub GET 请求跑通端到端技术链路，自动检索并针对真实候选抓取 README 补充证据，生成结构化推荐报告。

---

## 🔮 扩展 Web UI 的接入基础

GitHub Scout Agent 在架构设计之初即完成了展示层与业务内核的完全解耦：

- **业务内核入口**：`src/github_scout_agent/application.py` 中的 `run_research_task()` 是完全无状态、纯 Python 对象的函数入口。
- **输入**：标准自然语言任务 `task: str` 与装配好的 `OpenAICompatibleClient`、`GitHubRestClient`、`RequestBudget`。
- **输出**：结构化对象 `AgentRunResult`，内含：
  - `status`: 最终执行状态（`completed` / `terminated`）；
  - `termination_reason`: 终止归因（`model_completed` / `model_finalized` / 预算维度）；
  - `final_text`: 模型的 Markdown 结论；
  - `tool_results`: 完整的数据与证据快照；
  - `events`: 结构化时间线事件流（便于前端渲染进度条与调用轨迹）；
  - `evidence_complete`: 证据充分性布尔标记；
  - `evidence_gaps`: 缺失证据说明。
- **Web UI 适配建议**：可使用 **FastAPI** 包装 `run_research_task()` 为 SSE (Server-Sent Events) 流式接口，或者直接通过 **Streamlit** / **Gradio** 在几十行代码内快速构建交互式桌面/Web 界面。

---

## 📄 许可证与免责声明

本项目仅用于合法的公开技术研究与选型对比：
- 不得用于任何违背 GitHub 服务条款的大规模自动化爬取或滥用；
- 远程仓库的 README 与描述属于作者自身声明（标记为不可信输入），Agent 不承担供应链代码审计保证。
