# 工单 01 验证记录：Herdr 与双运行时最小执行链路

- 日期：2026-09-11
- 执行：工单 01 worker
- 授权配置：Pi 与 OpenCode 均为 `opencode-go / deepseek-v4.1-flash`，thinking/variant `max`；最大 2 个活跃 worker
- 状态：已交付（delivered）

## 1. 结论摘要

- Herdr 0.8.2 可以完成 Pi 与 OpenCode TUI worker 的登记、显式配置启动、prompt 投递、状态观测、暂停与停止。
- 两种运行时的模型、推理配置均可用独立证据核对，未发现静默降级或默认模型回退。
- OpenCode 的推理配置可通过 `OPENCODE_CONFIG_CONTENT` 在新建 tab 时注入：业务仓库零改写、未覆盖权限配置。
- Pi、OpenCode 都能调用 `handoff` skill，且新会话能在同一 worktree／branch 读取交接文档继续执行，配置保持一致。
- `idle／done` 只是终端生命周期信号，不能证明工单完成；`wait` 超时也不代表 worker 失败。
- 关键限制：Pi 的 `ask_user_question` 对话框在 pane 中真实阻塞，但 Herdr 仍报告 `working`，`wait --until blocked` 会超时。需要人工决策的 Pi UI 不能只靠 `blocked` 信号发现；根因与处置选项见附录 A。

## 2. 环境与版本（只读发现）

| 组件 | 版本／状态 | 证据 |
| --- | --- | --- |
| Herdr | 0.8.2（协议 20，schema_version 1） | `logs/00-versions.txt` |
| Pi | 0.85.1 | `logs/00-versions.txt` |
| OpenCode | 1.18.30 | `logs/00-versions.txt` |
| Herdr Pi 官方集成 | current (v8) | `herdr integration status`，`logs/00-versions.txt` |
| Herdr OpenCode 官方集成 | current (v10) | `herdr integration status`，`logs/00-versions.txt` |

认证检查（只读）：`pi auth check --provider opencode-go --json` 返回 `ready`；`opencode providers list` 显示 OpenCode Go、OpenAI、Zhanlu 凭据齐全。模型清单：`pi --list-models`、`opencode models opencode-go --verbose`。本模型变体集合为 `low／high／max`。

## 3. 运行配置与生效证据

### Pi

启动命令（tab／pane 创建见 `logs/01-tab-create-pi.json`、`logs/02-start-pi.json`）：

```bash
herdr agent start exp01pi --kind pi --pane <pane> -- \
  --provider opencode-go --model deepseek-v4.1-flash --thinking max
```

生效证据（`logs/05-pi-session-config.json`，来自 worker 会话 JSONL）：

```json
{"type":"model_change","provider":"opencode-go","modelId":"deepseek-v4.1-flash"}
{"type":"thinking_level_change","thinkingLevel":"max"}
```

Pi 会话 JSONL 路径由 `herdr agent get <name>` 的 `agent_session.value` 直接给出；会话文件在首条消息后落盘。TUI 状态栏同时显示 `(opencode-go) deepseek-v4.1-flash • max`。

### OpenCode

注入命令（不写业务仓库配置）：

```bash
CFG='{"agent":{"build":{"model":"opencode-go/deepseek-v4.1-flash","variant":"max"}}}'
herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd <worktree> \
  --label exp01-oc --env "OPENCODE_CONFIG_CONTENT=$CFG" --no-focus
herdr agent start exp01oc --kind opencode --pane <pane>
```

生效证据（`logs/05-oc-session-messages.json`，来自 `~/.local/share/opencode/opencode.db` 的 `message` 表，只读查询）：

```json
{"role":"assistant","providerID":"opencode-go","modelID":"deepseek-v4.1-flash","variant":"max","agent":"build"}
```

验证后一次性仓库内没有生成 `opencode.json`，也没有覆盖全局或项目权限配置。TUI 状态栏显示 `Build · DeepSeek V4.1 Flash · OpenCode Go · max` 与 `repo:main`。

## 4. Herdr 生命周期语义（实测）

| 信号 | 实测行为 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| `agent start` | 阻塞到 Herdr 认为可交互，返回 `agent_status:"idle"` 与 `interactive_ready:true`（`logs/02-*.json`） | agent 已登记、TUI 可接受输入 | 工单是否完成 |
| `agent prompt`（无 `--wait`） | 立即返回 `agent_prompted`；状态在 5 秒内变化 | 投递已被接受 | 回合质量与结果 |
| `agent prompt --wait` | 等待首个 settled 状态（`idle`／`done`／`blocked`） | 回合已停止输出 | 业务完成与交付正确性 |
| `working` | prompt 后约 2 秒 `agent get` 可见（`logs/06-*.txt`） | 正在执行 | — |
| `done`／`idle` | 未聚焦 tab 完成后为 `done`；`herdr agent focus` 后变 `idle`；CLI 读取不标记 seen（`logs/07-done-idle-focus.txt`） | 终端空闲 | 工单完成、结果有效 |
| `blocked` | OpenCode `read *.env` 触发权限询问，`wait --until blocked` 立即返回（`logs/08-oc-blocked.txt`） | 存在被 Herdr 覆盖的阻塞 UI | 阻塞原因是权限还是运行错误 |
| 等待超时 | working 时 `wait --until blocked --timeout 5000` 返回 `{"error":{"code":"timeout"}}`，exit 1；随后同一回合正常完成（`logs/06-*.txt`） | 本次等待窗口内未出现目标状态 | worker 失败；不能据此重试或销毁 |
| `unknown` | 本实验未在双运行时出现（只读清单中其他 agent 出现过） | agent 存在但无法分类 | 完成 |

明确结论：`idle／done` 与 `wait` 超时都不能作为工单完成或失败的判据；交付必须依赖 worker 结构化结果与主脑判断。

## 5. 暂停与中断（实测）

| 运行时 | 暂停当前回合 | 退出会话／停止 | 证据 |
| --- | --- | --- | --- |
| Pi | `herdr agent send-keys <agent> escape`（`app.interrupt`）；回合中止为 `Command aborted`，TUI 存活 | `ctrl+c ctrl+c`（第一次清空编辑器，第二次退出）；退出后 agent 注销、pane 回到 shell | `logs/10-pi-pause.txt`、`logs/12-pi-restart.txt` |
| OpenCode | `herdr agent send-keys <agent> escape escape`（双击）；回合中止为 `User aborted the command`，TUI 存活 | `ctrl+c` 一次退出 TUI；退出后 agent 注销 | `logs/10-oc-pause.txt`、`logs/12-oc-restart.txt` |

OpenCode 在 `blocked` 状态下发送一次 `escape` 会清除阻塞并终止该回合（`logs/09-oc-unblock.txt`）。

## 6. handoff 调用与跨会话续接（实测）

| 运行时 | skill 调用方式 | 交接文档 | 续接结果 |
| --- | --- | --- | --- |
| Pi | 字面命令 `/skill:handoff <参数>` 提交成功；skill 未从系统提示暴露时仍可用命令强制加载 | `handoff/pi-handoff.md` | 旧会话 `ctrl+c ×2` 退出后，同一 pane 重启同配置 Pi；新会话读取文档、创建文件并提交；新会话 JSONL 证明 cwd／provider／model／thinking 不变 |
| OpenCode | 不支持字面 `/handoff`；以 prompt 要求模型调用 `skill` 工具加载 `handoff` 成功 | `handoff/oc-handoff.md` | 旧会话 `ctrl+c` 退出后，同一 pane 重启同配置 OpenCode；新会话读取文档完成任务；message 表证明 cwd／provider／model／variant 不变 |

续接证据：`logs/13-*-continue-prompt.txt`、`logs/14-continuation-verify.txt`、`logs/14-oc-continuation-messages.json`、`handoff/*.md`。提交记录：`exp01: add pi continue marker`、`exp01: add oc continue note`，分支 `main` 与 base `83f4279` 一致。

适配建议：
- 交接文档使用明确绝对路径，并在替换会话前做可读性与结构校验。
- 旧会话确认停止（`agent_not_found`）后才允许新会话写同一 worktree。
- Pi 与 OpenCode 的 skill 引导方式不同，适配器需分别构造：Pi 用 `/skill:handoff`，OpenCode 用 `skill` 工具。

## 7. 后台监督

- 以 `nohup bash <script> >/dev/null 2>&1 &` 启动循环，父 shell 持有后 `disown`；进程在工具调用返回后继续运行，跨多个工具调用采样 `herdr agent get`，日志连续写入 6 个 tick 后自行退出（`logs/15-supervisor-heartbeat.log`、`logs/15-supervisor.pid`）。
- 适用方式：主脑 pane 内的持久 shell 上使用 `nohup`／`disown`；需要完全脱离终端会话树时用 `setsid`。必须重定向标准输出并登记 pid 与日志路径。
- 监督进程自身退出后，`status／wait`（后续工单实现）应能发现监督缺失；本次只验证了进程可跨调用存活与结果可读。

## 8. 可重复冒烟步骤

以下命令中的 `<slug>`、`<repo>`、`<pane>` 为占位符；运行前确认 `HERDR_ENV=1` 并在目标 workspace 内。

1. 建一次性仓库：`git init -b main <repo>`，写入一个文件并提交，另留一个未跟踪的 `.env` 作为阻塞探针。
2. 建 Pi tab 并启动（显式配置）：
   ```bash
   herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd <repo> --label <slug>-pi --no-focus
   herdr agent start <slug>pi --kind pi --pane <pane> -- \
     --provider opencode-go --model deepseek-v4.1-flash --thinking max
   ```
3. 建 OpenCode tab（注入推理配置）并启动：
   ```bash
   CFG='{"agent":{"build":{"model":"opencode-go/deepseek-v4.1-flash","variant":"max"}}}'
   herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd <repo> --label <slug>-oc --env "OPENCODE_CONFIG_CONTENT=$CFG" --no-focus
   herdr agent start <slug>oc --kind opencode --pane <pane>
   ```
4. 各发一条最小 prompt 并等待：`herdr agent prompt <name> "Reply READY" --wait --timeout 180000`。
5. 核对生效配置：
   - Pi：读 `herdr agent get <name>` 的 `agent_session.value` 指向的 JSONL，检查 `model_change`、`thinking_level_change`、`session.cwd`。
   - OpenCode：只读查询 `~/.local/share/opencode/opencode.db`：
     ```sql
     SELECT data FROM message WHERE session_id='<session-id>' ORDER BY time_created DESC LIMIT 3;
     ```
     检查 `providerID`、`modelID`、`variant`、`path.cwd`。
6. 生命周期：发一个 `sleep 30` 任务，`agent get` 观察 `working`；`agent wait --until blocked --timeout 5000` 验证超时错误；`agent wait --timeout 120000` 等待 settled；未聚焦时状态应为 `done`。
7. 暂停：Pi 发 `escape`；OpenCode 发 `escape escape`；确认状态回落且 TUI 仍在。
8. 交接：向旧会话提交 handoff 请求并给出文档绝对路径；校验文档可读；停止旧会话；在同一 pane 启动同配置新会话并令其读取文档完成任务。
9. 清理：`ctrl+c`（Pi 两次、OpenCode 一次）停止 agent，`herdr tab close <tab>`，确认 `herdr agent list` 不再包含实验 agent；删除一次性仓库；保留证据副本。

## 9. 推荐适配策略

- 配置：Pi 用 `--provider／--model／--thinking`；OpenCode 用 `OPENCODE_CONFIG_CONTENT` 注入 `agent.build.model／variant`，不改写仓库、不覆盖权限；启动前用模型元数据校验 provider／model／variant 组合（例如 `opencode models <provider> --verbose` 查 `variants`）。
- 观测：Pi 读 session JSONL；OpenCode 只读 `message` 表。两者都能给出实际生效的 provider／model／variant 与会话身份。
- 交互：不要把 `blocked` 当作唯一的人工介入信号；Pi 问题对话框需由监督层兜底（周期 pane 读取或超时策略），OpenCode 权限询问可靠映射为 `blocked`。
- 停止：OpenCode 的 `escape` 是暂停、`ctrl+c` 是退出；Pi 的 `escape` 是暂停、`ctrl+c ctrl+c` 是退出。不要用 `ctrl+c` 暂停 OpenCode。
- 上下文采样与自动交接阈值属于工单 04，不在本次验证范围。

## 10. 已知限制与未验证假设

- 已验证事实：Pi `ask_user_question` UI 显示时 Herdr 状态为 `working`，`wait --until blocked` 超时；OpenCode 权限询问为 `blocked`。根因见附录 A。
- 未验证：无人值守下自动应答 OpenCode 权限询问的流程（工单 01 只验证语义）。
- 未验证：上下文用量读取、300K／80% 阈值与自动交接（工单 04）。
- 未验证：多 worker 并发额度、wait-any 与 ack（工单 05）。
- 未验证：`agent start` 返回后立即 prompt 的竞态（本次首次 prompt 距启动约一分钟；启动响应含 `interactive_ready:true`）；工单 02／04 应覆盖。
- 未发现需要历史版本兼容补丁的情况。

## 11. 证据索引

| 文件 | 内容 |
| --- | --- |
| `logs/00-versions.txt` | 版本、官方集成状态、API 协议 |
| `logs/01-tab-create-*.json`、`logs/02-start-*.json` | tab／agent 启动响应与 argv |
| `logs/04-prompt-*.json` | 首次 prompt 投递响应与会话登记 |
| `logs/05-pi-session-config.json`、`logs/05-oc-session-messages.json` | 生效配置证据 |
| `logs/06-*-lifecycle.txt` | working／超时／settled 时间线 |
| `logs/07-done-idle-focus.txt` | done→idle 的 focus 语义 |
| `logs/08-oc-blocked.txt`、`logs/09-oc-unblock.txt` | OpenCode blocked 与 escape 解除 |
| `logs/09-pi-blocked*.txt` | Pi 问题 UI 显示但状态仍为 working |
| `logs/10-*-pause.txt` | 暂停／中断行为 |
| `logs/11-*-handoff-prompt.txt` | 两种 skill 调用方式 |
| `logs/12-*-restart.txt`、`logs/13-*-continue-prompt.txt`、`logs/14-*` | 停止、重启、续接与配置一致性 |
| `logs/15-supervisor-*` | 后台监督跨调用存活 |
| `handoff/pi-handoff.md`、`handoff/oc-handoff.md` | 两个运行时生成的交接文档 |

## 12. 清理记录

- 2026-09-11 17:33（本地）：停止实验 agent（`exp01pi2`、`exp01oc2` 及此前实例），关闭实验 tab（`wJ:t4`、`wJ:t5`），删除一次性仓库与 `/tmp/opencode/hpm-exp01`。
- 未操作其他 tab、workspace 或用户会话；实验证据已复制到本目录后删除原始临时目录。
- 清理后 `herdr agent list` 中无 `exp01*` agent，`wJ` workspace 仅剩原有 tab。

## 附录 A：Pi `blocked` 分类的根因与处置选项（2026-09-11 补充）

- 状态权威：Herdr 0.8.2 对 Pi 的官方集成（`~/.pi/agent/extensions/herdr-agent-state.ts`，v8）在运行且报活时是完整生命周期权威，Herdr 明确不再为其运行屏幕 manifest 检测（官方文档 `agents.mdx` 的 Status authority；实测 `agent get` 含 `screen_detection_skipped: true`）。
- 集成逻辑：`agent_start` 置 `working`，`agent_settled` 置 `idle`；只有收到 `herdr:blocked` 事件时才上报 `blocked`（herdr-agent-state.ts:207 的 `pi.events.on("herdr:blocked", …)`）。
- 本次现象根因：`@juicesharp/rpiv-ask-user-question` 只发出自身命名空间的 `rpiv:ask-user:blocked`（events.ts:33），没有转发 `herdr:blocked`，所以问卷等待回答期间 Herdr 一直是 `working`，`wait --until blocked` 超时。
- 对照：`pi-subagents` 在 `src/integrations/herdr-status.ts:267-280` 显式桥接了 `herdr:blocked`，其等待人工的异步子任务会被正确上报为 blocked。
- 用户问题的结论：卸载 `rpiv-ask-user-question` 本身不改变分类行为——只是该问卷不再出现；其他 Pi 内置或第三方阻塞 UI 同样只有在发出 `herdr:blocked` 时才变为 `blocked`。只有把 Herdr 的 Pi 集成也卸载、回退到屏幕 manifest 检测时，Herdr 才可能把可见的审批／提问 UI 判为 `blocked`，但会丢失精确生命周期与会话身份。
- 处置选项（工单 02／04 需选定并验证）：
  - A. 桥接扩展：在 `herdr-agent-state.ts` 旁增加扩展，监听 `rpiv:ask-user:blocked` 并转发 `herdr:blocked`（该集成文件注明可在旁新增自定义 hook／plugin）。
  - B. 监督层兜底：对 Pi 不依赖 `blocked`，用 `working` 长时间无输出加 pane 读取识别等待人工。
  - C. 卸载 Pi 集成回退屏幕检测：不推荐，丢失会话身份与精确状态。
- 该事项已登记为工单总览 README 的 E01，待主脑决定处置方案。
