# TASK_STATE

## 1. Task Foundation
- True Goal: 建立只读、有证据支持的 GitHub Scout Agent，并通过受限 Agent Loop 完成公开仓库候选发现、证据补充、透明对比与可核验推荐报告。
- Current Stage: S4 验收与归档
- Active Mainline: V1.1 正式版本冻结与 GitHub 私有仓库首次部署归档；Git Tag v1.1.0；176 项测试全绿。

## 2. Frozen Baselines and Inline Evidence
- F1 Fact Baseline (客观状态与实测结论):
  - F1-01: 工作目录为 C:\Users\Ophelia\Desktop\GitHub Scout Agent；已初始化本地 Git 仓库；未配置或推送 GitHub remote。
  - F1-02: 运行环境确认：Python 3.13.5、pip 25.1.1、Git 2.53.0；核心依赖 httpx 0.28.1、pydantic 2.14.1、typer 0.27.3、fastapi 0.115+、uvicorn 0.30+、pytest 8.4.2。
  - F1-03: 五大 GitHub 只读工具固定：`search_public_repositories`、`get_public_repository`、`get_public_readme`、`get_repository_license`、`get_repository_languages_and_topics`，限制官方 `api.github.com`、公开仓库、GET-only 与 64 KiB 流式截断保护。
  - F1-04: 多模型端点兼容性已实测 [E3]: DeepSeek (`deepseek-flash`) 与 Qwen-Max (`qwen-max`) 真实链路在线验收全部通过。
  - F1-05: 离线测试与自动化套件：完整自动化回归 `176 passed, 1 deselected in 5.25s` [E3: pytest]，compileall 零错误，语法与协议全面自洽。
  - F1-06: 缺陷与时序修复已固化：B1 闭包晚绑定、B2 Callable 类型、B3 None 守卫、B4 Gate A dict 纯函数构建、M1 thinking_mode 字面量校验、P0 提示词时序引导、P0 预算放宽可配。
  - F1-07: 候选发现与采纳机制完全解耦（Commit 822c616）：搜索结果不再自动锁定候选容量；仅在显式调查时消耗配额；证据门控拦截过早停步。
  - F1-08: Q1~Q3 深度修复与意图穿透固化：空结果促发更宽泛查询；消除空候选提前收尾；README 关键词强制 require_candidate_readme 门控。
  - F1-09: 自适应研究收敛与限流优雅降级固化：候选优先引导；连续 2 次 403 快速阻断；多 Tool Calls 边界平滑回退 Finalization。
  - F1-10: 产品级质量修复固化：常态注入核心研究策略；丰富工具描述；--require-readme CLI 标志；_report_is_complete 要求 accepted_candidate_count > 0。
  - F1-11: 线上验收通过 [E3: DeepSeek]：4 搜索 + 3 README + 排除无关平台 + 决策级报告；response_bytes 256KB 裕量充分。
  - F1-12: V1.1 Web UI 上线：FastAPI 9 条路由；单页 SPA；web 命令注册成功；实时 SSE 事件流。
  - F1-13: Web UI SSE 流式事件与后台线程桥接固化：run_research_task 暴露 events 参数；SSEEventRecorder 保证脱敏；asyncio.to_thread 解耦同步网络阻塞。
  - F1-14: 模型网络重试与 Web UI 异常显性化固化：OpenAICompatibleClient 支持 max_retries 指数退避重试；Web UI / CLI 启用 2 次重试吸收握手抖动；前端补齐 task_terminated 红点详情与 notranslate 保护，finally 保障按钮必复位 [E3: 176 passed]。
  - F1-15: V1.1 Web UI 真实端到端验收通过 [E3: DeepSeek + Web 实时流]：实测 Python CLI 选型对比（Click vs Typer vs Fire）；6 轮模型、20 次 GitHub API、8 轮检索排除噪声、3 个 README 深度调查；请求预算边界自动触发平滑 Finalization 收尾；结构化产出四段式对比决策报告。
  - F1-16: V1.1 正式版本能力固化：搜索响应精简至 18 字段使单次搜索预算从 ~50KB 降至 ~3KB；模型请求具备指数退避重试；Web UI 支持 SSE 实时事件流与一键导出 Markdown (.md)；全部 176 项测试通过 [E3: 176 passed]。
  - F1-17: 远程私有仓库部署完成 [E3]: opheliafarrant/github-scout-agent (Private, default branch `master` @ `8641de5`, tags `v1.0.0` @ `df3b4ce`, `v1.1.0` @ `71802a9`)；零密钥泄露。
- F2 Decision Baseline (已批准架构与决策边界):
  - F2-01 (D1): CLI 与本地 Web UI (FastAPI+SSE) 双入口；不采用重型框架与桌面端安装包。
  - F2-02 (D2): 严格只读与公开边界；支持无 Token (60次/h) 与用户只读 Token (5000次/h)；Token 不进模型/日志/报告/Git。
  - F2-03 (D3): 确定性程序预算强约束（模型轮次、GitHub 请求数、任务时长、响应字节、候选数量）；CLI/Web 参数动态调优。
  - F2-04 (D4): 聚焦少量技术候选比对、证据收集与透明推荐报告；不执行第三方代码，不承担完整供应链审计。
  - F2-05 (D5): OpenAI-compatible Tool Calling 标准协议适配；不支持 Tool Calling 时明确失败，不采用非结构化正则替代。
- F3 Version Baseline and Rollback Anchor:
  - F3-38: `16c5957` (`docs: sync TASK_STATE with T45 Web UI live acceptance verification`)。
  - F3-39: `cf97b02` (`fix: trim GitHub search results to 18 used fields...`)。
  - F3-40: `e3ffc6e` (`docs: sync TASK_STATE F3-39 + T47`)。
  - F3-41 (Current Rollback Anchor): `c32d17f` (Git Tag `v1.1.0`) | Dirty Tree Status: Clean (仅 2 份用户已删除的 doc 文件未暂存)。
## 3. Current Engineering Flow and Impediments
- Current Impediment / Stop Point: 无阻碍。V1.1 正式版本交付、基线冻结与 GitHub 私有仓库首次部署全部完成。
- [x] T49: GitHub 私有仓库首次部署、凭证审计与远程一致性核验完成 -> Remote `origin` @ `opheliafarrant/github-scout-agent` (Private, tags `v1.0.0`, `v1.1.0`)。
- [x] T48: 完成 V1.1 正式版本冻结、Web 成果导出功能与稳定基线归档 -> Git Tag `v1.1.0`。
- [x] T47: 搜索响应裁剪（18 消费字段，响应字节降低 90%+）已完成 -> Target Artifact: src/github_scout_agent/github_api.py | Verification: 176 项全绿。
- [x] T45: V1.1 Web UI 浏览器端到端验收（启动 web → 输入需求 → SSE 实时观察 → 阅读报告）已成功跑通 [E3: Python CLI 选型实测]。
- [x] T46: 完成模型网络抖动重试机制与 Web UI 异常状态显性化 -> Target Artifact: src/ & tests/ | Verification: 176 项 pytest 全通过。
- [x] T44: 线上真实端点验收（deepseek-flash + GitHub API） → Target Artifact: 线上运行报告 | Verification: 4 搜索 + 3 README + 1 排除 + 2 条件推荐 + 四段报告。
- [x] T43: 修复 CLI response_bytes 预算过低导致 README 阶段被跳过 → Target Artifact: src/github_scout_agent/cli.py | Verification: 167 全通过；线上 response_bytes 60KB/256KB。
- [x] T42: 完成深度证据研究、相关性筛选与完整推荐闭环 -> Target Artifact: src/ & tests/ | Verification: 167 pytest 全部通过，包含核心验收场景端到端测试。
- [x] T37: 完成 GitHub Scout Agent CLI MVP V1.0 最终收尾与版本冻结 -> Git Tag `v1.0.0`。

## 4. Ruled-Out Approaches and Parking Lot
- Ruled Out: MVP 不采用 GitHub 写操作、私有仓库、Git Clone、第三方代码执行、任意 Shell 工具、复杂多 Agent 重型框架、非结构化正则提取。
- ~~P-01: 评估基于 FastAPI / Streamlit 构建本地 Web UI 交互界面~~ (V1.1 已交付)
- P-02: 评估多模型端点自动测速与智能路由切换 | Reason for Deferral: 当前单模型配置稳定支持 DeepSeek/Qwen 等 OpenAI 兼容端点 | Wake-up Timing: 后续模型扩展阶段。
