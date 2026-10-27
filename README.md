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
- **测试通过率**：**176 passed, 1 deselected**（耗时 ~5 秒）。
- **覆盖范围**：
  - 工具注册、参数 Pydantic 校验与只读 GET 调度；
  - 预算硬性拦截、候选容量满载与非致命降级；
  - 多轮反馈循环、重复调用熔断、协议重试耗尽保护；
  - 搜索响应裁剪（将 GitHub 60+ 字段精简至 18 个核心消费字段，响应预算消耗降低 90%+）；
  - OpenAI-compatible Tool Calling 序列化、错误安全映射与网络重试退避；
  - 模型传输层网络异常分类（TLS、Timeout、ConnectError 安全脱敏与重试）；
  - 多语言意图解析、语言元数据过滤与候选证据充足度判定；
  - FastAPI Web 服务路由、SSE 实时事件流、任务历史存储与报告导出。

### 2. 真实多模型端点验证 [E3]
- **DeepSeek (`deepseek-flash` @ `api.deepseek.com`)**：通过 Gate A 协议预检与真实在线验收（CLI 实测与 Web UI 6 轮交互端到端跑通，自动吸收首轮 TLS 握手抖动，四段式对比决策报告）。
- **Qwen-Max (`qwen-max` @ `maas.qianwenaiapi.com`)**：实测 3 次模型调用、4 次 GitHub GET 请求跑通端到端技术链路，自动检索并针对真实候选抓取 README 补充证据，生成结构化推荐报告。

---

## 🌐 Web UI 交互界面 (V1.1)

GitHub Scout Agent 内置了轻量级本地 Web 仪表盘，支持实时 SSE 事件流、结构化报告可视化与一键导出：

```powershell
# 启动本地 Web 服务器（默认端口 8080）
github-scout-agent web

# 或指定绑定地址与端口
github-scout-agent web --host 127.0.0.1 --port 8080
```

- **访问地址**：打开浏览器访问 `http://127.0.0.1:8080`。
- **特性**：
  - **实时 SSE 进度流**：模型调用、工具调用、README 获取、预算消耗状态即时更新，红点显性展示异常原因；
  - **决策报告渲染**：候选卡片、横向对比表格、证据缺口提醒与预算使用率仪表盘；
  - **成果保存与导出**：支持 `📋 Copy Markdown` 一键复制完整报告，以及 `📥 Export .md` 一键下载本地 Markdown 文件；
  - **历史任务记录**：侧边栏自动留存当次运行的历史调研任务，支持一键切换复查；
  - **防翻译干扰设计**：原生内置 `notranslate` 保护，杜绝浏览器翻译插件篡改按钮状态或卡死 DOM。

---

## 🛡️ 系统安全设计与能力边界

本项目在架构设计上坚持“受限安全环境”原则：
1. **只读公开边界**：严格限制官方 `api.github.com`、公开仓库、GET-only 请求，不执行任何写操作，不克隆仓库到本地，不执行任何第三方代码；
2. **速率与凭据隔离**：支持无 Token（60次/小时）与用户只读 Token（5000次/小时）；GitHub Token 和模型 API Key 仅存在于本地环境变量与内存，绝不进入模型上下文、日志、报告或 Git 仓库；
3. **不可信输入防护**：第三方仓库的 README 与描述均标记为 `untrusted_remote_text`，由 64 KiB 流式截断保护，防止恶意超长文本或注入攻击；
4. **确定性预算约束**：模型调用轮次、GitHub 请求数、任务耗时、响应字节与采纳候选数均受硬性预算控制器限制，达到边界时自动平滑收敛为最终报告。

---

## 📄 许可证与免责声明

本项目仅用于合法的公开技术研究与选型对比：
- 不得用于任何违背 GitHub 服务条款的大规模自动化爬取或滥用；
- 远程仓库的 README 与描述属于作者自身声明（标记为不可信输入），Agent 不承担供应链代码审计保证。
