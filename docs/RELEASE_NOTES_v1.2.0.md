# GitHub Scout Agent v1.2.0 发行说明

> 发布对象：GitHub Release **v1.2.0**（Tag `v1.2.0`）  
> 发布附件：`GitHubScoutAgent-1.2.0-setup.exe`  
> SHA-256：`23ea362066427df990b07515d09d7d15049bea27d76d1664b218f96ce5077a15`  
> 文件大小：18,890,449 字节；版本资源：`1.2.0.0`

---

## 安装方式

**系统要求**：Windows 10/11 x64；运行期需要可用的 OpenAI 兼容模型端点（DeepSeek / Qwen / OpenAI / 本地 Ollama 均可）。

1. 下载 `GitHubScoutAgent-1.2.0-setup.exe`；
2. 双击安装（每用户安装，不需要管理员权限），默认目录 `%LOCALAPPDATA%\Programs\GitHub Scout Agent`；
3. 启动后打开右上角 ⚙ 设置，粘贴 API Key 并选择模型（支持从端点 `/models` 拉取列表并按 Tool Calling 能力自动筛选）；
4. 静默安装：`GitHubScoutAgent-1.2.0-setup.exe /VERYSILENT /CURRENTUSER`。

卸载只移除程序本体，`%APPDATA%\GitHubScoutAgent\` 中的配置与历史记录会保留。

---

## 本版核心变更

### 1. 证据治理从“事后正则审计”升级为“数据层治理”（T63–T67）

- **运行期证据台账**：新增 `evidence_ledger.py`，在工具真正返回数据时记录每个仓库的身份与证据等级（仅搜索命中 / 抓过元数据 / 抓过 README，以及 stars、created、pushed、archived、licence）。搜索命中不再被当作“已调查候选”。
- **单一报告校验器**：新增 `report_validator.py`，只消费台账与结构化的 `Finding` 代码（`ungrounded_repository`、`unattributed_verdict`、`metadata_only_verdict`、`immature_recommendation`、`fabricated_readme`、`zero_result_without_readme`、`unverified_recommendation`），取代原先分散在 Agent 循环与渲染层的五套启发式；`agent_loop.py` 由 1765 行降至 1124 行。
- **覆盖度分级**：`Completeness` 不再等同于“没有警报”。未完成的运行、证据缺口、任何 Finding、零候选、请求数量超过候选预算、以及“候选全部只有元数据证据”都会让报告显式降级为 `partial` 并**打印理由**，例如 `partial (the task asked for 12 options while the candidate budget compares at most 3 (system limit); no accepted candidate was checked against its README...)`。
- **归属与诚实度修复**：表格行、列表项、标题层级、中英文标题各自拥有独立归属域；`1. Use <repo>` 这类祈使句推荐纳入审计；`README was not fetched` 这类披露与“下一步建议”不再被误判为编造；已在报告中排除的候选不再被强收 README gap；点名了本会话调查过的候选时不再误发零结果警报。
- **成熟度门控**：低于 50★ 且创建不足 183 天的仓库，若被推荐且没有成熟度说明，会触发 Maturity Alert 并强制 `partial`；已排除段落与“证据缺口”段落中的提及不再误伤。

### 2. 检索与预算的确定性约束

- `per_page` 等参数超出范围时自动收敛而不是整轮崩掉；参数类错误独立计数并给出可接受参数清单；
- 响应字节改为**累计**口径，单仓 229 KB 的超长 description 会被裁剪到 400 字符，候选页 topics 也做限长；
- 连续两轮搜索零候选时强制改变检索策略（压缩查询或按名直查），而不是继续重写查询；
- 证据不足时的收尾会被拦回：零 README 且预算尚余时先要求真实调查；最终稿里出现“读过 README”的口径却从未抓取时，在收尾阶段被纠正。

### 3. 报告透明度

- 用户要求“不要证据 / 不要调用工具 / 只看元数据”时，报告会写明**实际执行了证据优先策略**以及消耗的请求数或 README 份数；
- 任务年份与证据时点不一致时标注当前证据时间；
- 请求数量超过候选预算时，显式声明这是系统上限而非检索失败。

### 4. 分发工程

- 新增 Inno Setup 安装脚本 `packaging/installer.iss`（每用户安装、开始菜单 + 可选桌面图标、卸载保留用户数据）；
- 新增七尺寸应用图标 `packaging/github_scout_agent.ico`（16–256 px），同时内嵌进可执行文件；
- 版本元数据统一到 1.2.0（`pyproject.toml`、包 `__version__`、`packaging/version_info.txt`、安装包）；
- 回归测试 360 项全绿（`pytest`，离线套件，1 deselected 为真实网络用例）。

---

## 已知限制

- **只读公开边界**：仅对 `api.github.com` 发 GET，不涉及私有仓库与写操作；
- **匿名配额**：未携带 GitHub Token 时每小时 60 次 core / 10 次 search，配额耗尽时报告会降级为 `partial` 并标注 rate limited；
- **未做代码签名**：安装包与可执行文件未签名，Windows SmartScreen 可能提示“未知发布者”，选择“仍要运行”即可；
- **仅 Windows x64**：macOS / Linux 请从源码运行（`github-scout-agent web`）。

---

## 回滚与追溯

| 版本 | 提交 / Tag | 说明 |
| :--- | :--- | :--- |
| v1.2.0 | `v1.2.0` | 本版（证据治理 + 安装包） |
| v1.1.0 | `71802a9` | Web UI 与生产级稳健性 |
| v1.0.0 | `df3b4ce` | CLI MVP |

完整变更见 [CHANGELOG.md](../CHANGELOG.md)。