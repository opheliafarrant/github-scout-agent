# GitHub Scout Agent — T24-P0 多 Tool Calls 批次收敛修复报告

## 基线与范围

- 基线 HEAD：`a037c1f6b7608d48fdc9008ec02de3e84ed725a2`。
- 本轮只做源码、MockTransport 和离线 pytest；真实 DeepSeek 请求 0，真实 GitHub 请求 0。
- 未修改 PRD、技术架构、Gate A/B 及 T17/T19/T21/T23 历史授权或验收记录；未 commit、push、reset、clean。
- 项目目录未发现额外 `AGENTS.md`；本轮遵循用户提供的复杂任务控制协议。

## A. T23 未触发 Finalization 的实际原因

1. `src/github_scout_agent/agent_loop.py:78-88` 原先只在一轮 Tool Call 集合全部处理完、回到外层 `while` 后判断 Finalization。
2. `agent_loop.py:113-183` 按顺序执行同批次调用；某个调用的 GitHub 预算错误或 fatal 预算出现后，立即把剩余调用标记为未执行并返回 terminated 结果。
3. `src/github_scout_agent/github_api.py:220-315` 在每个实际 GET 前逐次扣减预算；`get_repository_languages_and_topics` 在 `:480-499` 内部执行两个 GET。原 Agent Loop 没有在批次内预估该组合工具的 2 次请求成本。
4. 因此 T23 在同批次中达到 `github_requests` 后直接走 fatal 返回，跳过外层 Finalization 判断。不是 GitHub 预算计数器允许超额，也不是 Tool Call ID 缺失；原逻辑已经为剩余 ID 生成 tool message，但没有机会进入收尾模型轮次。
5. T23 历史事实保持不变：模型 2 次、GitHub GET 8 次、候选 3 个、Finalization 未启动、结果 PARTIAL。

## B. 修改文件与最小实现

- `src/github_scout_agent/budget.py:93-95`：增加只读 `github_requests_remaining`，不改变任何预算上限或扣减语义。
- `src/github_scout_agent/tool_registry.py:86-112`：对已通过参数校验的工具调用提供请求成本预估；普通 GitHub 工具为 1，Languages/Topics 组合工具为 2；未知或非法调用返回 0，仍交给原有协议校验处理。
- `src/github_scout_agent/agent_loop.py:78-88,145-214,253-278`：
  - 每个批次调用执行前检查候选访问、参数有效性和剩余 GitHub 请求预算。
  - 已有证据且当前调用成本超过剩余预算时，使用 `github_requests_insufficient` 事前阻断，不发起网络请求。
  - 当前调用和剩余调用均保留对应 `tool_call_id` 的受控 tool message；未执行项标记为 `not_executed_before_finalization` 或 `not_executed_after_stop`，不伪造工具数据。
  - GitHub 预算正常达到边界且已有证据时，在下一模型请求前进入保留的 Finalization；Finalization 仍使用 `tool_choice="none"`，不执行工具。
  - Finalization 的 `ModelProtocolError` 现在安全终止，不重试、不触发 GitHub 请求。
- `src/github_scout_agent/application.py:52-65`：若 Finalization 成功但存在预算不足或收尾前跳过的证据调用，报告仍标记 `Completeness: partial`，避免把部分证据写成完整结论。
- `tests/test_agent_loop.py:252-352`、`tests/test_tool_registry.py:30-50`、`tests/test_application.py`：新增批次预算、组合工具成本、边界收尾、正常路径和报告完整性反例测试；既有预算断言改为区分事前 `github_requests_insufficient`。

## C. T23-like 离线复现与修复证据

使用现有 `MockTransport`，没有外网连接：

- T23-like 批次：7 次 GitHub GET 后，下一项 2-GET 组合工具因只剩 1 次预算被阻断；所有 7 个 Tool Call ID 均保留 tool message；Finalization 启动并使用 `tool_choice="none"`；总请求数为 7，不超过 8。
- 大量 Tool Calls：只执行预算允许的搜索请求；组合工具未发网络请求，剩余调用均有受控结果；无证据时不启动 Finalization，安全返回 PARTIAL。
- GitHub 边界：恰好使用 2/2 次 GET 后直接进入 Finalization；Finalization 返回 Tool Call 时安全终止，未执行该 Tool Call，也未增加 GitHub 请求。
- 正常研究路径：搜索后模型直接返回文本时只使用 `auto`，不强制产生多余 Finalization 请求。

对应测试：

- `test_t24_batch_budget_stop_preserves_ids_and_reaches_finalization`
- `test_t24_large_batch_stops_without_network_overrun_or_finalization_without_evidence`
- `test_t24_github_boundary_finalization_tool_call_never_reaches_network`
- `test_t24_normal_research_path_does_not_force_finalization`

## D. Tool Call ID、预算与 Finalization 协议

- 固定工具仍为五个 GitHub 只读工具；GET-only、公开仓库过滤、`per_page <= 5`、64 KiB 响应边界、候选上限 3、GitHub 上限 8、模型上限 5 均未放宽。
- 事前预算不足使用 `github_requests_insufficient`，与已经实际尝试越过预算后由预算控制器产生的 `github_requests` fatal 路径区分。
- 已返回的 assistant 多 Tool Calls 不被删除；已执行调用保留真实结果，未执行调用保留明确的受控错误 tool message；下一次模型请求的消息上下文中不存在缺失 Tool Call ID。
- Finalization 不执行任何工具。报告阶段 Tool Call、协议异常、超时或无内容均安全终止，不自动重试。
- README 仍为 `untrusted_remote_text`；Token/API Key 不进入模型消息、报告、事件或测试输出。

## E. 完整离线验证与剩余风险

执行命令：

```powershell
python -m pytest -m "not live_github" --tb=short
```

结果：`118 passed, 1 deselected in 0.76s`。另行执行 `python -m compileall -q src tests` 和 `git diff --check`，均通过。被排除的 1 项为联网标记测试；本轮没有真实模型或 GitHub 请求。

### 最终结论

1. T23 的阻塞是否针对性修复：**是，离线已复现并修复同批次预算耗尽跳过 Finalization 的控制流缺陷；T23 本身没有重试或改写。**
2. 同批次多 Tool Calls 是否保证不超出请求预算：**是。有效工具调用先检查有界请求成本，实际 GET 仍由原预算控制器逐次扣减；Mock 反例未超过上限。**
3. Finalization 是否能在预算内启动：**是。在已有证据、剩余模型额度可用且研究调用将超过剩余 GitHub 预算时启动；使用 `tool_choice="none"`，不增加 GitHub 请求额度。**
4. 是否仍存在实际 P0/P1 阻塞：**本轮已验证的 T23 P0 批次阻塞已解除。真实 DeepSeek 兼容性、真实推荐质量和动态 API 行为仍未重新验收，属于未授权的后续验证风险，不得据此宣称真实 MVP 已通过。**
