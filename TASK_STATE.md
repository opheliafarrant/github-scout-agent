# TASK_STATE

## 1. Task Foundation
- True Goal: 建立可回退、可测试的 GitHub Scout Agent Python 工程基础和只读工具系统，并按阶段推进受限 Agent Loop 开发。
- Current Stage: S4-02 Mock LLM + 真实 GitHub Agent 闭环（完成）
- Active Mainline: T08: 完成两个显式真实 GitHub + Mock LLM 多轮闭环场景；等待用户授权进入 S5。

## 2. Frozen Baselines and Inline Evidence
- F1 Fact Baseline:
  - F1-01: 工作目录为 C:\Users\Ophelia\Desktop\GitHub Scout Agent；已初始化本地 Git 仓库；未配置或推送 GitHub remote。
  - F1-02: V0.1 和 PRD V0.2 已实际读取；PRD V0.2 是产品基线，V0.1 保留背景、风险和官方资料依据。
  - F1-03: GitHub、OpenAI API、HTTPX、Typer、Pydantic、pytest 和 Python 官方文档已查阅；动态 API 限制、模型兼容性和依赖版本仍需实施前验证。
  - F1-04: S3-01 实际环境：Python 3.13.5、pip 25.1.1、Git 2.53.0.windows.3；Windows `py` launcher 不存在，但 `python -m venv` 成功。
  - F1-05: S3-01 虚拟环境实际依赖：httpx 0.28.1、pydantic 2.14.1、typer 0.27.3、pytest 8.4.2；安装首次因 Windows 临时文件占用失败，无缓存重试成功。
  - F1-06: S3-01 离线 pytest 实际结果为 6 passed in 0.61s；CLI help、version、config 均返回 exit code 0；未调用真实 GitHub API 或模型 API。
  - F1-07: S3-02 新增固定工具注册表、GitHub REST GET-only 客户端、Pydantic 数据/证据模型和确定性请求/响应/候选预算；所有网络测试使用 HTTPX MockTransport。
  - F1-08: S3-02 完整离线 pytest 实际结果为 23 passed in 0.43s；CLI help、version、config 和 compileall 均通过；未调用真实 GitHub API 或模型 API。
  - F1-09: S3-02-Fix01 完整离线 pytest 实际结果为 35 passed in 0.38s；CLI help、version、config 和 compileall 均通过；所有 HTTP 测试继续使用 MockTransport。
  - F1-10: Fix01 实际修复四项问题：限流支持 retry-after/x-ratelimit-reset 和安全 fallback；搜索与单仓库严格要求 private=false 且 visibility=public；README 增加 untrusted_remote_text；HTTPX 响应改为有界 stream 读取。
  - F1-11: S3-03 完整离线 pytest 实际结果为 52 passed in 0.45s；CLI help/version/config/mock-demo、compileall 均通过；未调用真实 GitHub 或模型 API。
  - F1-12: Mock CLI 实际轨迹：model calls=3；search_public_repositories 调用 2 次；首次空结果后改写查询；最终 model_completed；GitHub requests=2；candidate=1。
  - F1-13: S3-04-Fix01 完整离线 pytest 实际结果为 58 passed in 0.43s；候选容量满额后已接受 README/许可证补查通过；新候选与未接受候选被阻断；mock-demo、compileall、CLI 回归通过。
  - F1-14: S4-01 官方文档核验覆盖搜索、仓库、README/Contents、许可证、语言/Topics、认证、细粒度只读权限和限流；当前实现使用固定 api.github.com、GET、Accept 和 API version header。
  - F1-15: S4-01 显式 live_github 测试无 Token 实际通过：1 passed，6 次 GET，全部 api.github.com；搜索、仓库详情、README、许可证、Languages、Topics 均解析成功；许可证真实状态为 unknown，Languages/Topics 为空但类型正确，仓库公开且未归档。
  - F1-16: S4-01 Token 模式未执行：当前环境未配置 GITHUB_TOKEN；无权限创建/提升/修改，也未展示任何凭据。默认 pytest 为 58 passed、1 deselected，真实测试不默认运行。
  - F1-17: S4-02 默认离线 pytest 实际结果为 60 passed、1 deselected；新增 MockTransport 接线测试验证两个场景均通过 Agent Loop，不绕过工具注册表。
  - F1-18: S4-02 在线 search 场景无 Token：3 模型轮次、2 次 GET（search -> README）、model_completed；repository 场景无 Token：3 模型轮次、2 次 GET（repository -> README）、model_completed；均未调用真实模型 API。
- F2 Decision Baseline:
  - F2-01 (D1): MVP 采用 CLI；本地 Web UI 后置；Windows 桌面应用不属于 MVP。
  - F2-02 (D2): 支持无 Token 和用户自配只读 Token；只读公开仓库；不支持私有仓库、账户管理和任何 GitHub 写操作；Token 不进入模型、报告、普通日志或版本控制。
  - F2-03 (D3): 模型轮次、GitHub 请求次数、总时长、单次返回内容大小、候选数量由确定性程序硬性控制；具体阈值后续实测确定。
  - F2-04 (D4): MVP 聚焦自然语言技术需求下的少量候选检索、证据补充、比较和可核验推荐报告；不承担完整代码/安全/供应链审计。
  - F2-05 (D5): 配置 Base URL、API Key、Model；先验收一个真实支持 Tool Calling 的 OpenAI-compatible 端点；不支持 Tool Calling 时明确失败，不采用非结构化命令替代。
- F3 Version Baseline and Rollback Anchor:
  - F3-01: S3-01 首个本地基线提交：1fd89eacfdf0e9cdef022f705818b9d389c4ad6e (`chore: initialize project foundation`)。
  - F3-02: S3-02 实施提交：3fef06896bac4df62cade05586f54dbe772d5585 (`feat: add read-only github tool system`)；未执行破坏性回滚操作。
  - F3-03: S3-02-Fix01 修复提交：6f0bfdfe6233fd22db288d959b02a143cbe42f2d (`fix: harden github tool safety boundaries`)；未执行破坏性回滚操作。
  - F3-04: S3-03 实施提交：cfd4074bdf6850768758000c2581d306ff38142c (`feat: add constrained offline agent loop`)；未执行破坏性回滚操作。
  - F3-05: S3-04-Fix01 修复提交：344ccde4f44b4cd06ceb68dab128abbbe61de06f (`fix: keep candidate capacity separate from fatal budgets`)；未执行破坏性回滚操作。
  - F3-06: S4-01 实施提交：3544b6257c2abc8354d8fae632c26ab56a24c6a2 (`test: verify github public rest integration`)；未配置或推送 remote。
  - F3-07: S4-02 实施提交：c8bb2d1d72aa71f2836221202a9df655c90f6bbe (`feat: connect mock agent loop to live github rest`)；未配置或推送 remote。

## 3. Current Engineering Flow and Impediments
- Current Impediment / Stop Point: None；S4-02 已完成；S5 真实模型验收必须等待用户明确授权。
- [x] T01: 完成 S0 产品需求分析 -> Target Artifact: docs/GitHub-Scout-Agent-产品需求分析-V0.1.md | Verification: 文件已实际读取；V0.1 保留。
- [x] T02: 完成 S1 产品需求冻结 -> Target Artifact: docs/GitHub-Scout-Agent-PRD-V0.2.md | Verification: 五项决策、MVP、Agent Loop、只读边界、F/S 验收和 S0-S6 路线已写入；文档一致性检查通过。
- [x] T03: 完成 S2 技术架构设计 -> Target Artifact: docs/GitHub-Scout-Agent-技术架构设计-V0.1.md | Verification: 总体架构、Agent Loop、工具/API、数据、报告、安全、预算、错误、技术栈、测试和 S3-S5 依赖已写入；文档一致性检查通过；待用户验收。
- [x] T04: 完成 S3-01 工程初始化与基础验证 -> Target Artifact: .git、.gitignore、pyproject.toml、src/github_scout_agent、tests、README.md | Verification: Python 3.13.5；4 个依赖版本已记录；6 个离线测试通过；CLI help/version/config 通过；基线提交 SHA 已记录。
- [x] T05: 完成 S3-02 只读工具系统与 Mock 验证 -> Target Artifact: src/github_scout_agent/{budget,github_api,github_models,tool_registry}.py、tests/test_github_tools.py、tests/test_tool_registry.py | Verification: 五项工具可注册/校验/调度；GET-only、固定 host、参数安全、错误分类、重试、响应/候选/请求预算和 Token 边界均有 Mock 测试；23 tests passed。
- [x] T05-Fix01: 完成 S3-02 安全缺陷修复 -> Target Artifact: src/github_scout_agent/{budget,github_api,github_models}.py、tests/test_github_tools.py | Verification: P1-01/P1-02/P2-01/P2-02 已修复；35 tests passed；未调用真实 GitHub 或模型 API。
- [x] T06: 完成 S3-03 受限 Agent Loop 与 Mock Tool Calling -> Target Artifact: src/github_scout_agent/{agent_loop,agent_models,mock_llm,events,demo}.py、tests/test_agent_loop.py | Verification: 多轮反馈、多个 Tool Call、协议错误、重复行动、五类预算、事件脱敏和 Mock CLI 演示均已验证；52 tests passed。
- [x] T06-Fix01: 完成 S3-04 候选容量修复 -> Target Artifact: src/github_scout_agent/{agent_loop,budget,github_api,tool_registry}.py、tests/test_agent_loop.py、tests/test_github_tools.py | Verification: 唯一候选身份、容量非 fatal、已接受候选补查、未接受候选阻断和其他预算终止均已验证；58 tests passed。
- [x] T07: 完成 S4-01 真实 GitHub REST 只读接口验收 -> Target Artifact: tests/test_live_github.py、src/github_scout_agent/github_api.py、pyproject.toml、README.md | Verification: 显式 live_github 无 Token 测试通过；6 GET、5 类工具路径、公开过滤、缺失许可证、README trust label、来源/抓取字段和 host/method 约束已验证；Token 模式待凭据验证。
- [x] T08: 完成 S4-02 Mock LLM + 真实 GitHub Agent 闭环 -> Target Artifact: src/github_scout_agent/live_agent_demo.py、src/github_scout_agent/mock_llm.py、tests/test_live_agent_wiring.py、CLI live-agent-demo | Verification: search 与 repository 两场景均真实经过 Agent Loop；各 2 GET、3 Mock 模型轮次、真实来源/缺失状态/预算输出已记录；60 offline tests passed。
- [ ] T09: 进入 S5 真实 Tool Calling 模型验收 -> Target Artifact: 待授权 | Acceptance: 用户明确授权后再开始；当前不得调用真实模型 API。

## 4. Ruled-Out Approaches and Side Items
- Ruled Out: MVP 不采用 GitHub 写操作、私有仓库、Clone、第三方代码执行、任意 Shell、用户真实文件管理、复杂多 Provider 框架、非结构化伪工具调用、多 Agent、RAG、GraphQL 强制迁移和 Windows 桌面应用；S4-02 未调用真实模型、未访问私有仓库、未执行写操作，Token 模式仍待凭据验证 | Credentials: 用户 D1-D5、PRD V0.2、S2 设计约束、S4-01/S4-02 真实与 Mock 证据 | Revert Scope: None | Retained Net Assets: V0.1、PRD V0.2、架构设计 V0.1、基础 CLI、只读工具注册表、Agent Loop、Mock LLM、真实 REST 显式入口、预算与事件记录和离线测试。
- P-01: MVP 完成后评估本地 Web UI、更多模型端点和历史报告/本地索引 | Reason for Deferral: 不影响当前 CLI Agent 闭环和安全验收 | Wake-up Timing: S5 MVP 验收后。
