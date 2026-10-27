# TASK_STATE

## 1. Task Foundation
- True Goal: 建立只读、有证据支持的 GitHub Scout Agent，并通过受限 Agent Loop 完成公开仓库候选发现、证据补充、透明对比与可核验推荐报告。
- Current Stage: S4 验收与归档 / V1.0 稳定版本正式冻结
- Active Mainline: CLI MVP V1.0 最终收尾与版本冻结完成，交付稳定基线。

## 2. Frozen Baselines and Inline Evidence
- F1 Fact Baseline (客观状态与实测结论):
  - F1-01: 工作目录为 C:\Users\Ophelia\Desktop\GitHub Scout Agent；已初始化本地 Git 仓库；未配置或推送 GitHub remote。
  - F1-02: 运行环境确认：Python 3.13.5、pip 25.1.1、Git 2.53.0；核心依赖 httpx 0.28.1、pydantic 2.14.1、typer 0.27.3、pytest 8.4.2。
  - F1-03: 五大 GitHub 只读工具固定：`search_public_repositories`、`get_public_repository`、`get_public_readme`、`get_repository_license`、`get_repository_languages_and_topics`，限制官方 `api.github.com`、公开仓库、GET-only 与 64 KiB 流式截断保护。
  - F1-04: 多模型端点兼容性已实测 [E3]:
    - DeepSeek (`deepseek-flash` @ `api.deepseek.com`): T35 真实链路在线验收通过，完成多 Tool Calls 预算前阻断与 `tool_choice=none` Finalization 收尾。
    - Qwen-Max (`qwen-max` @ `maas.qianwenaiapi.com`): 真实链路在线验收通过，3 次模型请求、4 次 GitHub GET 完整跑通，生成逐候选 README 证据分析推荐报告。
  - F1-05: 离线测试与自动化套件：完整自动化回归 `158 passed, 1 deselected in 0.73s` [E3: pytest]，compileall 零错误，语法与协议全面自洽。
  - F1-06: 缺陷与时序修复已固化：B1 闭包晚绑定、B2 Callable 类型、B3 None 守卫、B4 Gate A dict 纯函数构建、M1 thinking_mode 字面量校验、P0 提示词时序引导、P0 预算放宽可配。
  - F1-07: 候选发现与采纳机制完全解耦（Commit 822c616）：搜索结果不再自动锁定候选容量；仅在模型调用详情/README 工具显式调查时才消耗 candidates 配额；新增证据门控拦截模型在证据不足时的过早停步。
  - F1-08: Q1~Q3 深度修复与意图穿透固化（Commit 045b4b0, 2a147eb）：空结果促发更宽泛查询策略（Q1）；Finalization 彻底摆脱空候选提前触发（Q2）；任务明确声明 README/documentation 时强制开启 require_candidate_readme 门控，未查 README 时顶层 Completeness 真实降级为 partial，与模型诚实说明完全同步（Q3）；159 项测试全绿。
- F2 Decision Baseline (已批准架构与决策边界):
  - F2-01 (D1): MVP 采用 CLI 入口；本地 Web UI 为后续阶段；不采用桌面端安装包。
  - F2-02 (D2): 严格只读与公开边界；支持无 Token (60次/h) 与用户只读 Token (5000次/h)；Token 不进模型/日志/报告/Git。
  - F2-03 (D3): 确定性程序预算强约束（模型轮次、GitHub 请求数、任务时长、响应字节、候选数量）；CLI 支持参数动态调优。
  - F2-04 (D4): 聚焦少量技术候选比对、证据收集与透明推荐报告；不执行第三方代码，不承担完整供应链审计。
  - F2-05 (D5): OpenAI-compatible Tool Calling 标准协议适配；不支持 Tool Calling 时明确失败，不采用非结构化正则替代。
- F3 Version Baseline and Rollback Anchor:
  - F3-22: 修复与预算优化提交：`99dd6d4cb5501026dbb231c9618c63303ffe5a7c` (`fix: resolve bugs B1-B4, relax budgets, add search-first policy`)。
  - F3-23: 文档状态机同步提交：`5950fa7` (`docs: synchronize TASK_STATE with T36 and commit 99dd6d4`)。
  - F3-24 (Current Rollback Anchor): Git Tag `v1.0.0` (CLI MVP V1.0 正式发布冻结版本)。
  - F3-25: 候选解耦与证据门控提交：`822c616a602007008f2e31420ea75b4c182854d4` (`fix: candidate discovery decoupled from acceptance; evidence-gated model-stop`)。
  - F3-26: Q1~Q3 提示词拓宽与空候选完整度修复提交：`045b4b035fabd4e47d722700ebe9d637a7e2a89c`。
  - F3-27: README 关键词意图穿透提交：`2a147eb` (`fix: enforce require_candidate_readme on explicit readme and documentation keywords`)。

## 3. Current Engineering Flow and Impediments
- Current Impediment / Stop Point: 无阻碍；CLI MVP V1.0 全部技术与业务指标通过，两份历史报告变更按指示保留未暂存删除状态。
- [x] T39: 完成 Q1~Q3 深度修复与 README 显式意图门控穿透 -> Target Artifact: src/ & tests/ | Verification: 159 pytest 全部通过，模型空搜索重试与 README 未查降级完全一致。
- [x] T38: 完成候选发现与采纳解耦，并通过坏候选压力测试与 158 项回归测试 -> Target Artifact: src/ & tests/ | Verification: `candidates_used` 在搜索阶段保持 0；空搜索下模型诚实输出 0 推荐，拒绝虚假凑数。
- [x] T37: 完成 GitHub Scout Agent CLI MVP V1.0 最终收尾与版本冻结 -> Target Artifact: README.md, pyproject.toml, src/github_scout_agent/__init__.py, Git Tag `v1.0.0` | Verification: 158 pytest 全部通过，文档与代码完整同步，版本号升级 1.0.0。
- [x] T36: 完成 B1~B4/M1 修复、预算放宽可配与 qwen-max 真实链路闭环 -> Target Artifact: src/ & tests/ | Verification: 158 passed，真实端点 3 模型轮次 + 4 GitHub GET 跑通。
- [x] T35: 完成 DeepSeek 真实在线端到端验收 -> Target Artifact: .scout/t35_live_output.txt | Verification: 4 模型请求、7 GitHub GET、3 个 Python 候选收尾。

## 4. Ruled-Out Approaches and Parking Lot
- Ruled Out: MVP 不采用 GitHub 写操作、私有仓库、Git Clone、第三方代码执行、任意 Shell 工具、复杂多 Agent 重型框架、非结构化正则提取。
- P-01: 评估基于 FastAPI / Streamlit 构建本地 Web UI 交互界面 | Reason for Deferral: 核心 CLI MVP 已稳定解耦，留作后续功能阶段 | Wake-up Timing: V1.0 交付后。
- P-02: 评估多模型端点自动测速与智能路由切换 | Reason for Deferral: 当前单模型配置稳定支持 DeepSeek/Qwen 等 OpenAI 兼容端点 | Wake-up Timing: 后续模型扩展阶段。
