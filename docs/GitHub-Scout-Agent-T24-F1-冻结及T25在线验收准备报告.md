# GitHub Scout Agent — T24-F1 冻结及 T25 在线验收准备报告

## 1. 冻结检查结论

- 原始基线 HEAD：`a037c1f6b7608d48fdc9008ec02de3e84ed725a2`。
- T24-F1 本地提交：`fix: finalize agent safely across tool call batches`；完整 SHA 以提交后 `git rev-parse HEAD` 为准。
- T24-P0 修改范围与《T24-P0 多 Tool Calls 批次收敛修复报告》一致。
- 未发现非 T24 修改、敏感文件、Token、API Key 或运行日志进入待提交范围。
- `git diff --check`：通过。
- `python -m compileall -q src tests`：通过。
- 提交仅包含下列已审查文件；提交后工作树已重新核对。

## 2. 已审查文件

- `TASK_STATE.md`
- `README.md`
- `docs/GitHub-Scout-Agent-T24-P0-多ToolCalls批次收敛修复报告.md`
- `src/github_scout_agent/agent_loop.py`
- `src/github_scout_agent/application.py`
- `src/github_scout_agent/budget.py`
- `src/github_scout_agent/tool_registry.py`
- `tests/test_agent_loop.py`
- `tests/test_application.py`
- `tests/test_tool_registry.py`

T24 安全边界保持不变：固定五个 GitHub 只读工具、GET-only、官方 GitHub host、公开仓库过滤、候选上限 3、GitHub GET 上限 8、模型上限 5、响应大小边界、README 不可信标记和凭据隔离均未放宽。

## 3. 离线验证

执行：

```powershell
git diff --check
python -m pytest -m "not live_github" --tb=short
python -m compileall -q src tests
```

结果：`118 passed, 1 deselected in 0.72s`。联网标记测试未执行；本轮真实 DeepSeek 请求 0，真实 GitHub 请求 0。

## 4. T25 唯一在线命令与约束

只有在累计人民币预算余额已经由可靠记录确认、且用户明确授权后，才执行以下单条 PowerShell 命令。API Key 必须已存在于本地环境变量，不得写入命令、文件或聊天：

```powershell
$env:GITHUB_SCOUT_MODEL_BASE_URL='https://api.deepseek.com'; $env:GITHUB_SCOUT_MODEL='deepseek-flash'; $env:GITHUB_SCOUT_MODEL_THINKING_MODE='disabled'; $env:GITHUB_SCOUT_MODEL_MAX_TOKENS='512'; .\.venv\Scripts\github-scout-agent.exe run "find small Python agent repositories with native tool calling" --confirm-live
```

运行时固定约束：

- 模型调用最多 5 次，包含 Finalization；GitHub GET 最多 8 次；候选最多 3 个；搜索 `per_page <= 5`。
- HTTP 单次超时 15 秒；任务总时限 120 秒；无重试、无备用模型。
- GitHub 仅公开 GET-only；Finalization 必须使用 `tool_choice=none`，不执行新工具。
- 检查同批次多 Tool Calls 的预算事前阻断、Tool Call ID 配对、真实来源、缺失字段和推荐理由。
- 历史估算费用与实际账单分开记录；若无法确认累计人民币预算余额，立即不执行。
- 真实运行失败或 PARTIAL 不得重试，不得修改历史授权记录。

## 5. 状态

**READY（仅指 T24-F1 本地冻结和 T25 命令准备）**；**BLOCKED（指 T25 真实执行，当前缺少用户新的在线授权及可靠剩余费用确认）**。

本轮不执行真实验收、不查询余额、不访问外网。
