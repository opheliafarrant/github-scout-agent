# TASK_STATE

## 1. Task Foundation
- True Goal: 建立只读、有证据支持的 GitHub Scout Agent，并通过受限 Agent Loop 完成公开仓库候选发现、证据补充、透明对比与可核验推荐报告。
- Current Stage: S4 验收与归档
- Active Mainline: T56 Inno Setup 安装包（Phase C）；Phase B PyInstaller --onedir 已交付（T55）。

## 2. Frozen Baselines and Inline Evidence
- F1 Fact Baseline (客观状态与实测结论):
  - F1-01: 工作目录 C:\Users\Ophelia\Desktop\GitHub Scout Agent；已配置 GitHub 私有远程仓库 opheliafarrant/github-scout-agent，双向同步正常。
  - F1-02: 运行环境确认：Python 3.13.5、pip 25.1.1、Git 2.53.0；核心依赖 httpx 0.28.1、pydantic 2.14.1、typer 0.27.3、fastapi 0.115+、uvicorn 0.30+、pytest 8.4.2。
  - F1-03: 七大 GitHub 只读工具固定：`search_public_repositories`、`get_public_repository`、`get_known_repository`、`get_public_readme`、`replace_candidate_with_readme`、`get_repository_license`、`get_repository_languages_and_topics`，限制官方 `api.github.com`、公开仓库、GET-only 与 64 KiB 流式截断保护。
  - F1-04: 多模型端点兼容性已实测 [E3]: DeepSeek (`deepseek-flash`) 与 Qwen-Max (`qwen-max`) 真实链路在线验收全部通过。
  - F1-05: 离线测试与自动化套件：完整自动化回归 `209 passed, 1 deselected, 1 warning in 7.79s` [E3: pytest]，compileall 零错误，语法与协议全面自洽。
  - F1-06: 缺陷与时序修复已固化：B1 闭包晚绑定、B2 Callable 类型、B3 None 守卫、B4 Gate A dict 纯函数构建、M1 thinking_mode 字面量校验、P0 提示词时序引导、P0 预算放宽可配。
  - F1-07: 候选发现与采纳机制完全解耦（Commit 822c616）：搜索结果不再自动锁定候选容量；仅在显式调查时消耗配额；证据门控拦截过早停步。
  - F1-08: Q1~Q3 深度修复与意图穿透固化：空结果促发更宽泛查询；消除空候选提前收尾；README 关键词强制 require_candidate_readme 门控。
  - F1-09: 自适应研究收敛与限流优雅降级固化：候选优先引导；连续 2 次 403 快速阻断；多 Tool Calls 边界平滑回退 Finalization。
  - F1-10: 产品级质量修复固化：常态注入核心研究策略；丰富工具描述；--require-readme CLI 标志；_report_is_complete 要求 accepted_candidate_count > 0。
  - F1-11: 线上验收通过 [E3: DeepSeek]：4 搜索 + 3 README + 排除无关平台 + 决策级报告；response_bytes 256KB 裕量充分。
  - F1-12: V1.1 Web UI 上线：FastAPI 9 条路由；单页 SPA；web 命令注册成功；实时 SSE 事件流。
  - F1-13: Web UI SSE 流式事件与后台线程桥接固化：run_research_task 暴露 events 参数；SSEEventRecorder 保证脱敏；asyncio.to_thread 解耦同步网络阻塞。
  - F1-14: 模型网络重试与 Web UI 异常显性化固化：OpenAICompatibleClient 支持 max_retries 指数退避重试；Web UI / CLI 启用 2 次重试吸收握手抖动；前端补齐 task_terminated 红点详情与 notranslate 保护，finally 保障按钮必复位 [E3: 184 passed]。
  - F1-15: V1.1 Web UI 真实端到端验收通过 [E3: DeepSeek + Web 实时流]：实测 Python CLI 选型对比（Click vs Typer vs Fire）；6 轮模型、20 次 GitHub API、8 轮检索排除噪声、3 个 README 深度调查；请求预算边界自动触发平滑 Finalization 收尾；结构化产出四段式对比决策报告。
  - F1-16: V1.1 正式版本能力固化：搜索响应精简至 18 字段使单次搜索预算从 ~50KB 降至 ~3KB；模型请求具备指数退避重试；Web UI 支持 SSE 实时事件流与一键导出 Markdown (.md)；全部 184 项测试通过 [E3: 184 passed]。
  - F1-17: 远程仓库与版本拓扑校验 [E3]: opheliafarrant/github-scout-agent (Private)；Tag `v1.0.0` 锚定 `df3b4ce`，Tag `v1.1.0` 锚定 `71802a9` (含功能冻结 `c32d17f`)；主分支 `master` 持续演进维保提交 (`8641de5` 预算调优、`359c050` 部署同步、`13773de` T50 文档)；全量审计零凭据泄露。
  - F1-18: 开源发布准备就绪 [E3]: 根目录配置标准 Apache-2.0 LICENSE 与 NOTICE；pyproject.toml 补齐打包元数据；README 相对路径及徽章修复完成；docs/RELEASE_NOTES_v1.1.0.md 就绪；184 项离线测试全绿。
  - F1-19: KNOWN_PROJECTS 兜底与文档漂移修复固化 [E3: 184 passed, 1 deselected, 1 warning in 5.24s]：新增 `get_known_repository` 工具（白名单 cline/aider/openhands/swe-agent/mini-swe-agent，规范化小写无分隔符、不区分大小写，绕过搜索直达规范仓库并占用一个候选）；工具注册增至 7 个；修复 4 处文档漂移。
  - F1-21: Web UI 图形化模型配置固化 [E3: 205 passed, 1 deselected, 1 warning in 5.53s；真实 HTTP 冒烟：GET /api/settings 掩码 sk-…3456、PUT 后 /api/config 转 ready、外部 http 端点 422、磁盘文件无明文]：新增设置弹窗（5 预设 + 高级选项 + 内联校验）与 GET/PUT /api/settings；配置二级来源 %APPDATA%\GitHubScoutAgent\config.json（GITHUB_SCOUT_SETTINGS_FILE 可覆盖），环境变量优先级不变；密钥经 Windows DPAPI 加密为 dpapi: 前缀落盘，接口仅回显掩码；历史文件路径改由 default_history_path() 统一至 %APPDATA%。
  - F1-22: 桌面分发 Phase B 交付 [E3: `dist/GitHubScoutAgent/GitHubScoutAgent.exe` 实测]：PyInstaller 6.22.3 onedir 构建，PE Subsystem=2（无控制台窗口），产物 8.5 MB exe / 41 MB 目录；`templates/index.html` 已随包（GET / → 200，28962 字节，含 ⚙ 设置入口）；实测 `web --port 8091` 与**双击式无参启动**均正常，无参启动自动占用 8080 并唤起浏览器（日志可见浏览器 GET / 与 /api/history）；系统无控制台时 stdout 重定向至 `%APPDATA%\GitHubScoutAgent\logs\web-ui.log`（686 字节实测写入）；二次双击检测到既有实例 → 仅唤浏览器，进程数保持 1、无冗余监听；运行后防火墙规则新增数 = 0（仅监听 127.0.0.1 未触发提示框）。打包资产集中于 `packaging/`（launcher.py / github_scout_agent.spec / version_info.txt），build/ 与 dist/ 已 gitignore，源码零重复。
  - F1-20: Web UI 历史记录本地持久化与并发安全固化 [E3: 188 passed, 1 deselected in 5.34s]：引入 HistoryStore 抽象，支持本地 JSONL 原子持久化（.scout/web_history.jsonl），threading.Lock 互斥保护多工作线程读写，uuid4 随机熵防止 task_id 碰撞，FIFO 淘汰上限 100 条杜绝内存泄漏；覆盖重启重载、容量限制、并发多线程与损坏行自愈单测。
- F2 Decision Baseline (已批准架构与决策边界):
  - F2-01 (D1): CLI 与本地 Web UI (FastAPI+SSE) 双入口；采用 PyInstaller + Inno Setup 的轻量桌面分发（用户 2026-10-29 授权修订，原条款「不采用桌面端安装包」废止；仍不采用 Electron/Tauri 等重型桌面框架）。
  - F2-02 (D2): 严格只读与公开边界；支持无 Token (60次/h) 与用户只读 Token (5000次/h)；Token 不进模型/日志/报告/Git。
  - F2-03 (D3): 确定性程序预算强约束（模型轮次、GitHub 请求数、任务时长、响应字节、候选数量）；CLI/Web 参数动态调优。
  - F2-04 (D4): 聚焦少量技术候选比对、证据收集与透明推荐报告；不执行第三方代码，不承担完整供应链审计。
  - F2-05 (D5): OpenAI-compatible Tool Calling 标准协议适配；不支持 Tool Calling 时明确失败，不采用非结构化正则替代。
  - F2-06 (D6): 确定采用 Apache License, Version 2.0 作为唯一正式开源许可证，明确专利授权与商标保护边界。
- F3 Version Baseline and Rollback Anchor:
  - F3-Release-1.0: `df3b4ce` (Git Tag `v1.0.0` CLI MVP 冻结基线)。
  - F3-Release-1.1 (Current Release Anchor): `71802a9` (Git Tag `v1.1.0` 正式版基线，含功能冻结 `c32d17f`)。
  - F3-Master-Dev: `master` 分支头，承载后发布阶段维保提交与文档迭代；后续 Agent 严禁将 master 提交与已发布的冻结 Tag 混淆。
## 3. Current Engineering Flow and Impediments
- Current Impediment / Stop Point: 无阻碍。T55 Phase B 已交付（双击即用、无控制台、单实例唤浏览器，209 通过）；Phase C Inno Setup 安装包待执行；代码签名（Phase D）为可选项。
- [ ] T56: Inno Setup 安装包（Phase C）-> 目标产物: packaging/installer.iss + setup.exe；含开始菜单/桌面快捷方式、卸载项、安装时静默写入仅限 127.0.0.1 的入站防火墙规则（避免首次运行弹窗）。
- [x] T55: 桌面分发 Phase B：PyInstaller onedir 无控制台构建、packaging/ 资产隔离（launcher/spec/version_info）、无参启动隐式 web、端口占用自动顺延、单实例唤浏览器、windowed 下 stdout 重定向日志；新增 4 个 launcher 单测 [E3: 209 passed；双击实测 200 OK]。
- [x] T54: Web UI 图形化模型配置：新增设置弹窗（预设端点/高级选项/内联校验）、GET+PUT /api/settings、%APPDATA% 二级配置来源、DPAPI 密钥加密与掩码回显、历史路径统一；新增 tests/conftest.py 与 17 个单测 [E3: 205 passed]。
- [x] T53: Web UI 历史记录本地持久化与并发安全治理：实现 HistoryStore、互斥锁、UUID task_id 与 JSONL 存储，新增 4 个单测，回归通过 [E3: 188 passed]。
- [x] T52: KNOWN_PROJECTS 兜底与文档漂移修复：新增 get_known_repository 工具、工具注册 5→7、README 架构图与 docstring 漂移修复 [E3: 184 passed]。
- [x] T51: 正式开源发布准备、Apache License 2.0 落地、README 链接与徽章修正、Release Notes 编制与元数据梳理。
- [x] T50: 完成正式发布文档规范化、Git版本记录校准、README/CHANGELOG/CONTRIBUTING 建设与远程同步。
- [x] T49: GitHub 私有仓库首次部署、凭证审计与远程一致性核验完成 -> Remote `origin` @ `opheliafarrant/github-scout-agent` (Private, tags `v1.0.0`, `v1.1.0`)。
- [x] T48: 完成 V1.1 正式版本冻结、Web 成果导出功能与稳定基线归档 -> Git Tag `v1.1.0`。
- [x] T37..T47: CLI MVP、深度证据闭环、Web UI 与搜索裁剪等已全部完成验收 [E3: 184 passed]。

## 4. Ruled-Out Approaches and Parking Lot
- Ruled Out: MVP 不采用 GitHub 写操作、私有仓库、Git Clone、第三方代码执行、任意 Shell 工具、复杂多 Agent 重型框架、非结构化正则提取。
- ~~P-01: 评估基于 FastAPI / Streamlit 构建本地 Web UI 交互界面~~ (V1.1 已交付)
- P-02: 评估多模型端点自动测速与智能路由切换 | Reason for Deferral: 当前单模型配置稳定支持 DeepSeek/Qwen 等 OpenAI 兼容端点 | Wake-up Timing: 后续模型扩展阶段。
