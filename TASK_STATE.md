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
  - F1-05: 离线测试与自动化套件：完整自动化回归 `248 passed, 0 failed, 0 skipped`（junitxml 计数，pytest exit=0，7.97s） [E3: pytest]，compileall 零错误，语法与协议全面自洽。
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
- F1-23: 桌面成品 UI/功能审计修复固化 [E3: 214 passed；重建 exe 后 CDP 真实浏览器复测：console 零报错、Ollama 无 Key → ready:true 且 run 越过 not_configured 进入 network_error、空任务给出提示]：修复 4 项真实缺陷——(1) 回环端点（Ollama/LM Studio/vLLM）无 Key 时被判定 not_configured，预设可选但永远跑不起来（config.model_ready 与 OpenAICompatibleClient 双门控，现允许 loopback 无 Key 且省略 Authorization 头）；(2) 页面缺 favicon 导致每次加载 404；(3) 四个预算输入框 label 无 for/id 关联、任务输入框缺可访问名；(4) 空任务点击开始静默无反馈。新增 5 个回归单测覆盖密钥无关回环放行与远端仍强制 Key。
  - F1-24: 桌面成品真实模型端到端验收 [E3: 冻结 exe + DeepSeek `deepseek-chat`，真实无头 Chrome/CDP 驱动 127.0.0.1:8080]：设置面板配置 DeepSeek 后 ready:true（密钥 DPAPI 落盘，磁盘 grep 明文 0 命中）；单条 143 字符查询 57.5 秒完成（status=completed / termination=model_finalized），产出 25,209 字符报告、页面渲染 23,555 字符、console 零报错；预算 3/3 候选、14/20 GitHub、11/12 模型、response 233,350/262,144 字节（89%）；历史写入 `%APPDATA%\GitHubScoutAgent\web_history.jsonl`（1 条 completed）。诚实边界：末个候选 README 因总响应字节预算耗尽报 response_too_large（已实测复现），报告据此自标 Completeness: partial；模型自选查询词泛化为 “multi-agent python framework”，偏离任务意图（见 P-03）。
  - F1-25: P-03 检索策略与预算分配修复固化 [E3: 220 passed；冻结 exe + DeepSeek 复跑同一基线任务：36.8 秒 completed/model_finalized，报告 19,569 字符、Completeness: complete，response_bytes 27,416（修复前 233,350，−88%），model 10/12、GitHub 11/20，查询词 github repository search agent / github readme analyzer agent / code search agent python / github repo recommender 全部对题]：三根因三修复——(1) 查询漂移：policy 注入 Query fidelity / Query coverage 两条硬约束，禁止把需求替换为更宽泛类别；(2) README 撑爆响应预算：改 application/vnd.github.raw 流式下载 + 字节上限（min(剩余预算, 12000*4+4096)），大 README 仅截断不再 response_too_large；(3) 证据闸门误判：接受 truncated 状态、把 replace_candidate_with_readme 的成功读取计为替代目标证据、并把「已替换掉的候选」与「候选容量未接纳（candidate_capacity_full）的候选」排除出闸门，消除 stale gap。报告第 3 节如实列出两个候选 README 因容量未取到、仅据元数据排除。
  - F1-26: 设置面板模型列表下拉、保存即验证连通性与鉴权失败可诊断化固化 [E3: 235 passed；重建 exe 后对真实中转实测：POST /api/settings/models（复用已存 Key）返回 183 个模型、PUT /api/settings 返回 verification={result:ok,count:183,model_listed:true}]：新增 `POST /api/settings/models`（POST 而非 GET 以免密钥进 URL/日志；请求体缺 Key 时复用已保存 Key，返回 `used_saved_key`；密钥永不回传）；`model_client.list_model_ids` 走 OpenAI 兼容 `/models`，按 401/403→auth、404/405/501→unsupported、超时/网络/协议分别归类，上限 200 个模型；前端模型框改 `<input list>` + datalist，点「获取模型列表」即下拉选择，失败按 reason 给中文可执行提示（含"该服务未提供 /models，请手动填写"）；另把 provider 原文（如 "Incorrect API key provided"）附加到 authentication_error/model_http_error 消息并对 `sk-*` 掩码。实测：桩服务 good key → `{ok:true,count:3}` 且 UI datalist 出现 3 项、状态"已获取 3 个可用模型"；坏 key → `{ok:false,reason:"auth",status_code:401}` 且 UI 红字提示；真实端点 `https://maas.qianwenaiapi.com/compatible-mode/v1` + 现存 Key → 401 "Incorrect API key provided. For details, see: https://help.aliyun.com/zh/model-studio/error-code#apikey-error"（判定为 Key 无效，非地址错误）。
  - 追加（同一轮用户反馈）：
    (1) 下拉"只剩几个模型"根因是浏览器 datalist **按输入框已有文字过滤**（框内已有 deepseek-v4.1-flash-1017 时只剩 2 条），非后端过滤——后端一次返回 183 个（max_models 500）。二次修复（用户刷新后又只剩两个）：彻底弃用 datalist，改独立 `<select id="model-select">` 全量渲染（183 项全部可见、不受输入框文字过滤），选中即回填输入框；列表按 Base URL 归一化键（小写+去尾斜杠）存入 `localStorage`（前缀 `gsa.models.v1:`，不含 Key），刷新/重开设置后仍可用，换地址自动切换到该地址自己的缓存；旧值不在清单时只红字提醒、不再擅自清空。新增 1 个模板回归测试（断言无 datalist、有 model-select 与缓存键）。
    (2) "保存不验证连通性"修复：`PUT /api/settings` 落盘后调用 `_verify_saved_endpoint`（复刻 /models 探针，8s 超时），响应新增 `verification`：ok{count,model_listed} / failed{reason,message,status_code} / unknown（端点无 /models）/ skipped；设置**先保存再验证**，探测失败绝不回滚用户输入；UI 依结果给红字（Key 被拒 / 地址不通 / 模型名不在清单）。新增 4 个路由测试（成功验证、Key 被拒仍保存、模型名未列出、无 /models 端点）+ 一个 autouse 离线 fixture，测试总数 230→234。
  - F1-27: 模型下拉按 Tool Calling 能力筛选固化 [E3: 244 passed, 0 failed；重建 exe 后真实无头 Chrome/CDP 对能力桩端点 11/11 项通过]：/models 不返回任何能力元数据，故新增 model_client.probe_tool_calling（每模型 1 次 max_tokens=8 的 tools 探针，零 token 前置过滤 embedding/rerank/tts/whisper/dall-e/moderation/sdxl/flux/realtime 等，结果归 tools_accepted | tool_calls_returned | tools_rejected | no_access(403) | non_chat_model；auth(401)/not_found/rate_limited/timeout/network/upstream/invalid_response 记为未判定）与 POST /api/settings/models/probe（payload 上限 200 模型、Semaphore(6) 并发、任一 401 即 key_lost 并对剩余模型返回 skipped、响应含 ok/count/supported/rejected/unknown/aborted/results）；前端 select 默认只列 supported===true 的模型（✓/✗/? 标记，可取消勾选看全部），判定按 Base URL 归一化键存 localStorage（前缀 `gsa.toolcall.v1:`，24h TTL），刷新后零请求复用；探测可「停止检测」中断，状态栏逐步显示 "检测中 done/total（已确认支持 N 个）"。真实中转实测 4 个模型：deepseek-v4.1-flash-1017 → supported=True（tools_accepted）、cmaas-glm-5.3 → 403 no_access、qwen-audio-3.1-realtime-flash → invalid_response、text-embedding-v3 → non_chat_model 且零请求。CDP 验证：6 个桩模型（2 支持 / 1 拒绝 tools / 1 403 / 1 embedding / 1 realtime）→ 默认只显示 2 个、取消勾选显示 6 个并带标记、刷新重开设置后仍过滤且不再发请求。新增 9 个回归测试（5 客户端探针 + 4 路由/模板）。诚实边界：探测 183 个模型意味着 183 次极短请求（约 1-2 分钟、少量 token），会被端点限流并可能留下"未判定"，故提供停止按钮与全量视图。
  - F1-28: 预算语义分层与逐次遥测固化 [E3: 248 passed；源码起 8099 服务真实中转跑同一 RAG 任务 → feedback_bytes_used 27,520（旧同任务 173,840）、fatal_exhausted_dimensions=[]、feedback_trace 7 条、39.1s、6 GitHub 请求、8 次模型调用、termination=model_finalized]：
    (1) 单条超限只截断不致命：budget.record_feedback_size 不再 raise，新增 `max_feedback_item_bytes`（默认 64 KiB，Web/CLI 同值）；agent_loop._clip_feedback_text 把超限 payload 截到上限并追加 `…[truncated: original N bytes, limit M]`，同时 emit `feedback_clipped`（source/label/original_bytes/kept_bytes）。此前一条大 README 就让健康运行以 feedback_bytes 直接终止。
    (2) 累计超限优雅收尾：只有 `feedback_bytes_used > max_feedback_bytes` 才记为 fatal；while 循环顶部检查 feedback_exhausted，有候选且留有 finalization 预留时先走 _finalize（stop_reason=feedback_bytes，提示在 Evidence Gaps 声明截断），否则 emit task_terminated 正常收尾。
    (3) 累计上限与真实用量脱钩修复：Web/CLI 的 max_feedback_bytes 由 128 KiB 提升到 512 KiB（旧值只比历史正常用量 40,895→173,840 高 1.3–2.8 倍，等于贴着正常水位设闸）。
    (4) 逐次遥测：tool_finished 事件新增 `result_bytes`/`feedback_bytes`/`feedback_bytes_used`；AgentRunResult.feedback_trace（source/label/bytes/used 逐条）经 TaskStatus 持久化进 web_history.jsonl 并由 SSE done 与 GET /api/report/{id} 返回；budget.snapshot() 只多一个 `max_feedback_item_bytes`，不塞 feedback_records（避免每条 tool 消息的 _budget 字段撑爆 prompt）。
    (5) 可读性：render_mvp_report 头部在存在 fatal 维度时追加 `- Research stopped by budget: <dimension>`，不再让『被预算掐断』伪装成『模型自己收尾的 partial』。
    (6) 测试调整与新增：38 处原有 loop 测试的 `max_feedback_bytes=20_000`（旧语义是单条上限）改为 512 KiB 以保持『反馈预算不是被测对象』的原意；新增 5 个测试（超大单条截断后仍 completed、累计耗尽终止并记录 trace、tool_finished 遥测字段、报告 budget 行、HistoryStore 持久化 feedback_trace）。
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
- Current Impediment / Stop Point: 无阻碍。T55 Phase B 已交付（双击即用、无控制台、单实例唤浏览器）；T58 P-03 修复已闭环（220 通过，E2E complete）；T59 设置面板模型下拉与鉴权诊断已交付（235 通过）；T60 Tool Calling 能力筛选已交付（244 通过）；T61 预算语义/遥测已交付（248 通过）；P-03 遗留的输出质量问题（检索命中 0–1★ 玩具仓、报告信息密度不足）待下一轮；Phase C Inno Setup 安装包待执行；代码签名（Phase D）为可选项。
- [x] T61: 预算语义与遥测修复：单条超限改截断降级（max_feedback_item_bytes=64 KiB）、累计上限 128 KiB→512 KiB、feedback_exhausted 优雅收尾、tool_finished 逐次字节遥测 + feedback_trace 持久化、报告标注 stopped-by-budget；新增 5 个回归测试 + 38 处旧测试预算值随语义更新 [E3: 248 passed；真实中转同任务 feedback_bytes_used 173,840→27,520、fatal=[]]。
- [x] T60: 模型下拉按 Tool Calling 能力筛选：新增 probe_tool_calling + POST /api/settings/models/probe（并发 6、401 中止、可中断）、前端默认只列已确认支持的模型并缓存判定 24h；新增 9 个回归测试 [E3: 244 passed；CDP 11/11、真实中转 4 模型语义正确]。
- [x] T59: 设置面板模型列表下拉 + 鉴权失败可诊断：新增 POST /api/settings/models 与 list_model_ids、`<select>` 全量下拉（按地址 localStorage 缓存）+ reason 映射提示、上游错误原文透出（掩码）、保存即连通性验证；新增 11 个回归测试（6 路由 + 4 客户端 + 1 模板）[E3: 235 passed；exe 重建后 curl + CDP 真实浏览器 8/8 项实测（含刷新后仍 184 项）]。
- [ ] T56: Inno Setup 安装包（Phase C）-> 目标产物: packaging/installer.iss + setup.exe；含开始菜单/桌面快捷方式、卸载项、安装时静默写入仅限 127.0.0.1 的入站防火墙规则（避免首次运行弹窗）。
- [x] T58: P-03 修复（检索策略 + README 预算 + 证据闸门）：policy 增 query fidelity/coverage，禁止泛化替代；README 改 raw 流式字节上限下载；闸门接受 truncated、认 replace 票据、排除被替换与容量未接纳候选；新增 4 个回归测试（大 README 字节上限、raw 文本、预算耗尽、truncated 计为证据、替换/容量剔除）[E3: 220 passed；E2E Completeness: complete]。
- [x] T57: 桌面成品 UI/功能审计与缺陷修复：用真实无头 Chrome 走查首屏/设置/预设/校验/保存/空任务全链路，修复回环端点无 Key、favicon 404、label 关联、空任务提示四项，重建 exe 复测零 console 报错 [E3: 214 passed]。
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
- ~~P-03: E2E 暴露的两项输出质量项——模型自选查询词偏离任务意图；单次大 README 可耗尽 256KB 总响应字节预算使候选证据降级为 partial~~ (T58 已修复，E2E 复测 complete + 对题)
- P-02: 评估多模型端点自动测速与智能路由切换 | Reason for Deferral: 当前单模型配置稳定支持 DeepSeek/Qwen 等 OpenAI 兼容端点 | Wake-up Timing: 后续模型扩展阶段。
