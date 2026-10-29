# TASK_STATE

## 1. Task Foundation
- True Goal: 建立只读、有证据支持的 GitHub Scout Agent，并通过受限 Agent Loop 完成公开仓库候选发现、证据补充、透明对比与可核验推荐报告。
- Current Stage: S4 验收与归档
- Active Mainline: T62 提升检索质量与防臆造门控已交付（255 测试全绿）；T56 Inno Setup 安装包（Phase C）待执行。

## 2. Frozen Baselines and Inline Evidence
- F1 Fact Baseline (客观状态与实测结论):
  - F1-01..F1-20: 工作目录、Python 3.13、七大只读工具、多模型端点兼容、188 项离线测试全绿、开源许可证 Apache-2.0、HistoryStore JSONL 持久化均已固化。
  - F1-21..F1-27: 桌面分发 Phase B、UI 审计、真实 DeepSeek 驱动 E2E、设置面板模型下拉（`POST /api/settings/models`）、Tool Calling 能力探测（`POST /api/settings/models/probe`）均已通过验收 [E3: 244 passed]。
  - F1-28: 预算语义分层与逐次遥测固化 [E3: 248 passed]：单条 64 KiB 截断降级、累计 512 KiB 优雅收尾、逐次遥测 feedback_trace 持久化。
  - F1-29: 检索成熟度引导与证据防臆造硬门控固化 [E3: 255 passed, 0 failed, 1 deselected in 8.29s]：(1) 工具描述与 policy 明确指引成熟选型使用 `sort="stars"` 与 `stars:>50` 规避 0-1★ 玩具项目；(2) `_finalize()` 动态注入 `INVESTIGATED CANDIDATES WHITELIST` 硬约束，严禁未调查仓库声明 Verified Match；(3) `AgentLoop` 与 `render_mvp_report` 审计候选裁决，臆造声明自动触发 `Evidence Grounding Alert`、强降级 `partial` 并记入 Evidence Gaps。
- F2 Decision Baseline (已批准架构与决策边界):
  - F2-01 (D1): CLI 与本地 Web UI (FastAPI+SSE) 双入口；PyInstaller + Inno Setup 轻量桌面分发。
  - F2-02 (D2): 严格只读与公开边界；官方 api.github.com GET-only；凭据脱敏不入日志报告模型。
  - F2-03 (D3): 确定性程序预算强约束（模型轮次、GitHub 请求数、任务时长、响应字节、候选数量、反馈字节）。
  - F2-04 (D4): 聚焦技术候选比对与证据透明报告；不执行第三方代码，不凭臆造主观推荐。
  - F2-05 (D5): OpenAI-compatible Tool Calling 标准协议适配；DPAPI 本地加密存储。
  - F2-06 (D6): Apache License, Version 2.0 唯一正式开源许可证。
- F3 Version Baseline and Rollback Anchor:
  - F3-Release-1.0: `df3b4ce` (Git Tag `v1.0.0` CLI MVP 冻结基线)。
  - F3-Release-1.1 (Current Release Anchor): `71802a9` (Git Tag `v1.1.0` 正式版基线，含功能冻结 `c32d17f`)。
  - F3-Master-Dev: `master` 分支头，承载后发布阶段维保提交与功能强化（T54-T62）。

## 3. Current Engineering Flow and Impediments
- Current Impediment / Stop Point: 无阻碍。T62 检索成熟度引导与证据防臆造已交付（255 测试通过）；Phase C Inno Setup 待执行。
- [x] T62: 检索成熟度引导与防臆造硬门控：搜索工具与 policy 引导 sort=stars/stars:>50、_finalize 注入 INVESTIGATED CANDIDATES WHITELIST、AgentLoop 与 render_mvp_report 审计臆造声明并触发 Evidence Grounding Alert 强降级 partial [E3: 255 passed]。
- [x] T61: 预算语义与遥测修复：单条超限改截断降级（max_feedback_item_bytes=64 KiB）、累计上限 512 KiB、逐次字节遥测 + feedback_trace 持久化 [E3: 248 passed]。
- [x] T60: 模型下拉按 Tool Calling 能力筛选：新增 probe_tool_calling 与前端支持筛选 [E3: 244 passed]。
- [x] T59: 设置面板模型列表下拉 + 鉴权诊断与保存连通性验证 [E3: 235 passed]。
- [ ] T56: Inno Setup 安装包（Phase C）-> 目标产物: packaging/installer.iss + setup.exe。
- [x] T55..T58: 桌面分发 Phase B、桌面 UI 审计、P-03 检索与大 README 预算修复。
- [x] T48..T54: V1.1 冻结归档、GitHub 私有仓库部署、开源协议规范化、已知工具兜底、本地持久化与图形化设置。

## 4. Ruled-Out Approaches and Parking Lot
- Ruled Out: MVP 不采用 GitHub 写操作、私有仓库、Git Clone、第三方代码执行、模型凭记忆臆造候选标为 Verified。
- P-02: 评估多模型端点自动测速与智能路由切换 | Reason for Deferral: 当前单模型配置稳定支持 DeepSeek/Qwen 等 OpenAI 兼容端点 | Wake-up Timing: 后续模型扩展阶段。
