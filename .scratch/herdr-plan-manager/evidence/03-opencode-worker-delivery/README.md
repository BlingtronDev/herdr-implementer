# 工单 03 执行记录：让 OpenCode 使用同一套单工单执行接口

- 日期：2026-09-11
- 执行：工单 03 worker
- 授权配置（沿用工单 01／02 已确认值）：`kind` 二选一、`provider=opencode-go`、`model=deepseek-v4.1-flash`、`thinking=max`，最大 2 个活跃 worker
- 状态：已交付（delivered）
- 代码基线：工单 02 提交 `9224d83`

## 1. 结论摘要

- `bin/plan_manager.py` 的同一套 `start`／`status`／`read`／`stop` 接口现在接受 `--kind opencode`；除运行时配置本身，主脑不需要学习第二套流程。
- OpenCode 显式配置采用工单 01 验证过的映射：`herdr tab create --env OPENCODE_CONFIG_CONTENT='{"agent":{"build":{"model":"<provider>/<model>","variant":"<thinking>"}}}'`。不改写业务仓库配置文件，不覆盖权限设置。
- 启动前用 `opencode models <provider> --verbose` 校验 provider／model 存在且该模型包含请求的推理 variant；不支持、无 variant 或 provider 不存在的组合在创建任何资源前失败（退出码 2），无默认模型回退。
- 实际生效证据：冒烟会话的 `message` 表中所有 assistant 行均为 `opencode-go / deepseek-v4.1-flash / variant=max / agent=build`；`opencode debug config` 显示注入后 `agent.build.model`／`variant` 生效，仓库自身的 `permission: {webfetch: deny}` 与 `agent.build.description` 原样保留。
- 中断、blocked、错误、未知投递状态与保留现场语义与 Pi 相同：OpenCode 用 `escape escape` 暂停，stop 保留分支／worktree／记录；`blocked`（权限询问）只形成待处理异常，绝不视为交付。
- **发现并修复计划外缺陷 E02：** OpenCode TUI 在 `interactive_ready` 后的一段时间内会丢弃 `herdr agent prompt` 的键入，提示返回成功但从未提交；旧行为下 worker 实际是被监督层 30 秒后的“结果补报”提示意外启动的。本工单加入投递确认与有界重投，修复后 worker 从合同提示开始工作，监督层不再依赖补报作为主投递路径。
- 确定性测试从 51 增至 64 个；关键单工单语义（交付、非代码交付、结果缺失、错误 HEAD、停止保留、不确定投递）对 Pi／OpenCode 双运行时参数化通过。

## 2. 改动

| 文件 | 说明 |
| --- | --- |
| `bin/plan_manager.py` | 新增 `OpenCodeAdapter`（显式 env 映射、`escape escape` 中断、variant 校验）；`parse_opencode_models`；`Herdr.tab_create` 支持 `--env`；`agent_start` 在无运行时参数时不再追加空 `--`；`get_adapter` 接入 opencode；移除只看 Pi 枚举的全局 thinking 检查（改为适配器各自校验）；新增 `deliver_prompt_confirmed`／`wait_agent_started`（投递确认与有界重投）；`runtime.env` 持久化注入配置供后续交接复用；`state.prompt` 记录投递尝试次数；启动与补报路径都使用确认投递 |
| `prompts/plan-worker.md` | 未改动（合同本身与运行时无关） |
| `tests/fakes/bin/opencode` | 新增确定性 OpenCode CLI fake（`models <provider> --verbose` 目录与 variant 集合） |
| `tests/fakes/bin/herdr` | 新增 `drop_prompts` 场景（模拟 TUI 丢弃键入仍返回成功）；场景改为在首个“实际处理”的提示上启动 |
| `tests/fakes/scenario.py` | 新增 `blocked` 场景 |
| `tests/test_plan_manager.py` | 关键语义参数化到双运行时；新增 OpenCode 配置注入／非法配置／blocked 测试与投递丢失重投测试 |

未改动：旧 `bin/dispatcher.py`、`bin/get_context.py`、`SKILL.md`、`schema/`，未实现自动交接（04）、并发额度与 wait／ack（05）、cleanup（06）。

## 3. 接口与映射

```bash
# 与 Pi 完全相同的接口，仅 kind/thinking 语义不同
python3 bin/plan_manager.py start \
  --repo <repo> --ticket-id <id> --title <title> --base <full-sha> \
  --material <spec-or-ticket-path> [--material ...] \
  --kind opencode --provider opencode-go --model deepseek-v4.1-flash --thinking max \
  [--management-root <dir>] [--instructions "<附加任务说明>"]

python3 bin/plan_manager.py status|read|stop ... --kind 无关，按 worker 登记事实工作
```

- OpenCode 的 `thinking` 取值是模型元数据中的 variant 名（本模型为 `low/low、high、max`）。不是该模型 variant 的值在启动前报错并列出可用值；无 variant 的模型同样在启动前拒绝。这里不做 `off→none` 之类的隐式换算，避免静默替换推理配置。
- 注入配置写入 `state.json` 的 `runtime.env`，交接（工单 04）可在同一 worktree 用同一 env 重启新会话。
- `opencode` 的 variant 校验来源：`opencode models <provider> --verbose` 的 `variants` 键集合（真实输出见 `logs/deterministic/opencode-models-deepseek-v4.1-flash.txt`）。

## 4. 确定性测试

```bash
python3 -m pytest tests/test_plan_manager.py -q   # 25 passed
python3 -m pytest tests/ -q                       # 64 passed
```

新增／参数化覆盖：

| 测试 | 覆盖的验收点 |
| --- | --- |
| `test_opencode_injects_confirmed_config_without_touching_repo_config` | 同一启动接口支持 opencode；env 注入内容与确认配置一致；`--` 后无运行时参数；仓库 `opencode.json` 与工作区保持不变；`runtime.env` 已保存；`read` 可用 |
| `test_opencode_rejects_unsupported_configuration_before_delivery` | 未知模型、未知 variant、无 variant 模型均在资源创建前失败，无 Herdr 调用与 worker 记录 |
| `test_opencode_blocked_is_an_exception_not_delivery` | `blocked` 只形成异常事项，不标记交付；stop 保留现场 |
| `test_dropped_prompt_is_redelivered_until_confirmed`（双运行时） | 投递被运行时丢弃时确认失败并重投；成功次数入账 |
| `test_pi_start_passes_explicit_args_without_config_injection` | 既有 Pi 显式参数链路不变、无 env 注入 |
| `test_delivered_code_ticket`／`test_delivered_noncode_ticket`／`test_idle_without_result_is_not_delivery`／`test_wrong_head_is_protocol_failure`／`test_needs_decision_is_an_exception`／`test_stop_retains_scene_and_is_idempotent`／`test_uncertain_prompt_is_observed_not_resent`（双运行时参数化） | 工单／worker／branch HEAD／产物关联检查、idle 无结果不算完成、不确定投递不重发、停止保留等语义在两种运行时下一致 |

## 5. 真实 OpenCode 冒烟

环境：Herdr 0.8.2、OpenCode 1.18.30（`logs/00-versions.txt`）；一次性仓库位于 `/tmp/opencode/hpm-exp03b/repo`，管理目录 `/tmp/opencode/hpm-exp03b/manager`，base `4c209f2`。

### 5.1 正常交付（修复后）

- `start` 返回 `worker=w-03-smoke-2075cc`、`prompt_attempts=2`、tab `wJ:tC`；stderr 记录 `prompt attempt 1 ... was not picked up (status idle); retrying`（`logs/opencode-delivery/start.json`、`start.err`）。
- OpenCode 会话第一条 user message 就是投递的合同提示（`You are worker w-03-smoke-2075cc ... Read the contract at ...`），监督层日志没有出现补报：`supervisor started → terminal settled; result recorded`（`logs/opencode-delivery/oc-session-messages.jsonl`、`supervisor-delivered.log`）。
- 交付 `oc-smoke-b.txt`（内容 `OC-MATERIAL-B-8ae425b2`），HEAD `bc7f8722d4d5c0fab401ed529bfe6a997a396d57` 与工具核对的 branch HEAD 一致；worktree 内不存在 `.scratch` 材料，但 worker 从受控快照读到了内容（`logs/opencode-delivery/worktree-state.txt`、`result.json`、`materials-manifest.json`）。
- 生效配置（权威证据）：会话 `ses_f6fd3e14bffe3rAgOAV5Bmce7t` 的全部 assistant message 均为 `('opencode-go','deepseek-v4.1-flash','max','build')`（`oc-session-messages.jsonl`）。
- 注入与权限：`opencode debug config` 显示 `agent.build.model=opencode-go/deepseek-v4.1-flash`、`variant=max`，同时 `permission={"webfetch":"deny"}` 与仓库 `agent.build.description` 原样保留（`debug-config-with-injection.json`、`debug-config-without-injection.json`）；仓库 `git status` 干净，未生成或改写任何配置文件。
- 交付后 `status` 报 `delivered`、`supervisor.state=exited`；结果、分支、worktree 与记录全部保留（`status-delivered.json`）。

### 5.2 执行中停止与中断（修复后）

- `start`（附加指令要求先 `sleep 300`）返回 `w-03-stop-02`、`prompt_attempts=2`、tab `wJ:tD`（`logs/opencode-stop/start-smoke2.json`）。
- 观测到 `working` 后执行 `stop --reason ...`，5.42 秒完成；`lifecycle=stopped`、`reason=stop-requested`、监督进程 `exited`，agent 回落到 `done`（`stop-smoke2.json`、`status-smoke2-stopped.json`）。
- 会话消息显示 assistant 以 `MessageAbortedError` 结束，pane 底部显示 `Build · DeepSeek V4.1 Flash · interrupted`，证明 `escape escape` 是暂停而不是退出、且 `sleep` 回合被中止（`logs/opencode-stop/read-smoke2-stopped.txt`）。
- 分支 `hpm/w-03-stop-02`、worktree 与监督记录保留，未自动删除，也未写任何结果文件（`worktree-smoke2.txt`）。

### 5.3 blocked 信号（真实权限询问）

- 把一次性仓库与管理目录放在用户 OpenCode 配置 `external_directory` 白名单之外（`/tmp/hpm-exp03-blocked/`），启动 `w-03-blocked-fe4ec4`（`prompt_attempts=2`）。
- worker 读取管理目录中的合同时，OpenCode 弹出 `Permission required / Access external directory ... Allow once / Allow always / Reject`；Herdr 正确上报 `blocked`，监督层写入异常事项 `code=blocked`，`lifecycle` 仍是 `running`、`result=null`（`logs/opencode-blocked/pane-blocked.txt`、`status-blocked.json`、`supervisor.log`）。
- `stop` 4.39 秒完成，清除权限询问并保留现场（`status-stopped.json`、`stop.json`）。

## 5.4 Pi 回归冒烟（确认既有链路未受投递确认机制影响）

- 同一接口以 `--kind pi` 启动 `w-03-pi-smoke-afcb26`（tab `wJ:tF`），`prompt_attempts=1`、启动耗时 8 秒；Pi 在投递后立即进入 `working`，未观察到 E02 的丢弃竞态（`logs/pi-regression/start.json`、`start.err`）。
- 交付 `pi-smoke.txt`（内容 `PI-MATERIAL-de8b3dba`），HEAD `1f0f0d2` 与 branch HEAD 一致，worktree 干净，监督层未发补报（`logs/pi-regression/result.json`、`worktree-state.txt`、`supervisor.log`）。

## 6. 计划外情况（待主脑汇总到总览 README）

### E02：OpenCode TUI 在就绪握手后丢弃首条投递，旧行为把“结果补报”当成主投递路径

- 发现时间：2026-09-11（修复后同日验证）
- 关联工单：[03](../../issues/03-opencode-worker-delivery.md)；与 01 记录中的未验证项（“agent start 返回后立即 prompt 的竞态”）及 02 的补报机制直接相关
- 状态：已修复并验证（待主脑确认是否需要写入共享 README 并登记为已解决）
- 预期与实际：预期 `herdr agent start` 返回、`agent get` 报 `idle`／`interactive_ready=true` 后即可投递。实际在 OpenCode 中，紧随启动后的 `herdr agent prompt` 返回 `agent_prompted`（rc=0），但 OpenCode TUI 尚未接受键入，文本被完全丢弃：16 秒后 pane 的输入框仍是占位符 `Ask anything…`，会话中不存在该 user message（`logs/race-discovery/race-composer-unsubmitted.txt`、`prompt-race-observation.txt`）。两次真实启动都复现，随后监督层在 30 秒 settle 宽限期后发送的“结果补报”提示才成为会话第一条消息，worker 实际靠该提示（而非合同提示）开始工作；第一次冒烟之所以仍成功，是因为模型自行从 result 路径找到并读取了合同，属偶然而非保证（`logs/race-discovery/supervisor-delivered.log`、`oc-session-messages-smoke1.jsonl`、`oc-smoke1-user-parts.txt`）。
- 根因（已确认）：OpenCode 官方集成在进程启动早期即向 Herdr 报活并报告 `interactive_ready`，此时 TUI 的输入编辑器仍会丢弃键入；Herdr 的 `agent prompt` 只负责发送按键，返回成功不代表提示已提交。Pi 在同一窗口内未观察到该现象，但机制上同样存在竞态。
- 影响：没有修复时，OpenCode 的“首个业务投递”实际不可靠，且监督层的补报被误用作主投递；若 worker 不主动探索合同，会出现空转或错误交付。属于工单 03 验收条件 5（就绪／投递处理）。
- 处置：在 `cmd_start` 与监督层补报处引入 `deliver_prompt_confirmed`：投递后观察运行时是否离开 settled 状态，窗口内仍 settled 则判定键入被丢弃，最多重投 3 次（窗口默认 10 秒，可用 `HPM_PROMPT_CONFIRM_SECONDS`／`HPM_PROMPT_ATTEMPTS` 覆盖）；确认成功才继续。重投只发生在运行时明确 settled 时，不在运行中重发业务任务。修复后真实启动 `prompt_attempts=2`，会话第一条消息为合同提示，监督层未再补报；确定性测试用 `drop_prompts` 模拟丢弃并验证重投恰好一次。
- 后续工单／计划变更：无新增工单。建议工单 04 的会话替换复用同一确认投递路径（同一函数已在适配边界内），并在 04 的验收中保留“新会话确实读取交接文档”的证据要求。

### E03：OpenCode 对项目外路径的 `external_directory` 权限依赖管理目录位置

- 发现时间：2026-09-11
- 关联工单：[03](../../issues/03-opencode-worker-delivery.md)；影响 04（交接文档位置）、06（清理与资源位置）及实际部署须知
- 状态：待主脑决策（是否需要文档说明或调整管理目录布局策略）
- 预期与实际：合同、材料快照、结果文件都在 worktree 之外的管理目录。本次成功冒烟依赖用户全局配置已有 `external_directory /tmp/opencode/* → allow` 规则；在白名单之外（5.3）OpenCode 会弹出权限询问并进入 `blocked`。
- 证据：`logs/opencode-blocked/pane-blocked.txt`、`logs/opencode-blocked/status-blocked.json`；成功冒烟使用 `/tmp/opencode` 下路径（`logs/opencode-delivery/start.json`）。
- 影响：使用默认管理目录（目标仓库 Git common dir 下的 `herdr-plan-manager/`）时，若 OpenCode 将 worktree 视为项目根，该目录属于项目外路径，是否需要人工放行取决于用户权限配置。工具按设计不修改权限配置、会把 `blocked` 上报给主脑；但部署前应确认现有授权是否覆盖该路径，否则每次工单都会产生一个人工介入点。
- 处置与负责人：待主脑决定。候选：A. 部署文档建议把管理目录放在用户既有 allow 规则覆盖的路径，或由用户显式确认一次 `Allow always`；B. 在技能文档中说明该权限依赖，不改代码；C. 评估把管理目录放到 worktree 内部（会违反“主脑维护的执行记录不随 worker 分支提交、且 worktree 清理后仍需保留”的既有判断，不推荐）。工单 03 不改变权限配置。
- 解决与验证：待主脑决策后补充。

## 7. 双运行时契约对照

| 语义 | Pi | OpenCode | 证据 |
| --- | --- | --- | --- |
| 启动接口 | `--kind pi --provider/--model/--thinking` + `agent start -- <args>` | `--kind opencode` + tab env 注入，`agent start` 无运行时参数 | 配置注入测试、真实 start 输出 |
| 配置校验 | `pi --list-models` 的 provider／model／thinking 能力 | `opencode models --verbose` 的模型与 variant 集合 | 非法配置测试 |
| 配置生效 | 会话 JSONL `model_change`／`thinking_level_change`（工单 01） | DB message 行 providerID／modelID／variant + `debug config` | `oc-session-messages.jsonl`、`debug-config-with-injection.json` |
| 暂停／中断 | `escape`（两次调用） | `escape escape`（一次调用两键） | 真实 stop 冒烟、`MessageAbortedError` |
| 投递确认 | `deliver_prompt_confirmed`（共用，含 E02 重投） | 同左 | `test_dropped_prompt_is_redelivered_until_confirmed`、真实 `prompt_attempts=2` |
| blocked | 权限／提问 UI 需桥接扩展才能上报（E01） | 权限询问可靠上报为 `blocked` | 真实 blocked 冒烟、`test_opencode_blocked_is_an_exception_not_delivery` |
| 交付协议 | 工单／worker／HEAD／产物一致性检查、idle 不算完成 | 完全相同 | 双运行时参数化测试 |
| 停止保留 | 保留结果、分支、worktree、记录 | 完全相同 | 双运行时 stop 测试 + 真实 stop 冒烟 |

## 8. 验收条件核对

- [x] 同一启动、查看和停止接口支持选择 Pi 或 OpenCode；除必要运行时配置外流程一致（`test_opencode_injects_confirmed_config_without_touching_repo_config`、真实冒烟的 start/status/read/stop）。
- [x] OpenCode 显式接受并保存 provider／model／thinking；校验兼容性、无默认模型或静默替换（`runtime.env` 持久化、启动前 variant 校验、DB message 证据）。
- [x] 采用工单 01 验证过的映射方式并提供实际生效证据；未照搬根 TUI 不支持的参数（`OPENCODE_CONFIG_CONTENT` + DB／`debug config`）。
- [x] 配置注入不修改仓库受跟踪／本地配置，不覆盖权限设置；固定配置可被交接复用（仓库 `git status` 干净、`debug config` 权限保留、`runtime.env`）。
- [x] 正确处理就绪、投递、中断和错误／blocked 信号；错误不被误报为交付（E02 修复 + 投递确认、`escape escape` 中断、blocked 异常事项、idle 无结果不算完成）。
- [x] 交付遵循相同工单／worker／branch-HEAD／产物关联检查；代码与非代码任务均可用（双运行时参数化测试与真实交付）。
- [x] 停止保留与未知投递状态语义与 Pi 一致，不自动销毁或重复请求（双运行时 stop 测试、真实 stop 冒烟、`test_uncertain_prompt_is_observed_not_resent`）。
- [x] 双运行时契约验证与 OpenCode 实际冒烟证据齐备，Pi 单工单链路继续通过（25 个 plan_manager 测试、64 个全量测试、两段真实冒烟）。

## 9. 未决与边界

- 工单 04：自动交接、上下文观测与阈值不在本次范围；`runtime.env` 与 `deliver_prompt_confirmed` 已为同 worktree 换会话准备就绪。
- 工单 05：并发额度、wait／ack 不在本次范围；事项已带稳定 id 与 `acked_at` 占位。
- 工单 06：cleanup 删除接口不在本次范围；stop 只保留现场。
- E01（Pi 提问 UI 不报 `blocked`）仍未解决，处置属工单 04 的决定；本工单未改变 Pi 行为。
- OpenCode 的 variant 取值必须以模型元数据为准，`off` 等无对应 variant 的语义目前显式失败而不是换算，符合“不自动降级”。

## 10. 证据索引

| 路径 | 内容 |
| --- | --- |
| `logs/00-versions.txt` | Herdr／Pi／OpenCode 版本与集成状态 |
| `logs/deterministic/test-plan-manager.txt`、`test-all.txt` | 25／64 个测试通过 |
| `logs/deterministic/opencode-models-deepseek-v4.1-flash.txt` | 真实 variant 元数据（校验来源） |
| `logs/race-discovery/` | E02：修复前两次启动的 supervisor 日志、会话消息、投递竞态观察与 composer 现场 |
| `logs/opencode-delivery/` | 修复后真实交付：start、result、contract、materials、DB 会话消息、生效配置、注入前后 debug config、worktree 状态 |
| `logs/opencode-stop/` | 修复后真实停止：start、stop、status、pane 中断现场、worktree 保留 |
| `logs/opencode-blocked/` | 真实权限询问：pane 截图文本、blocked 事项、stop 后保留 |

## 11. 冒烟资源清理记录

- 2026-09-11 11:22 UTC（本地 19:22）：先对实验 agent 发送 `ctrl+c` 退出 TUI，再关闭 tab `wJ:t8`、`wJ:t9`、`wJ:tB`、`wJ:tC`、`wJ:tD`、`wJ:tE`；确认 wJ workspace 仅剩原有 tab `wJ:t1`、`wJ:t2`、`wJ:t3`，无遗留实验 agent。
- 移除实验 worktree 与分支 `hpm/w-03-smoke-8f4d17`、`hpm/w-03-stop-01`、`hpm/w-03-smoke-2075cc`、`hpm/w-03-stop-02`、`hpm/w-03-blocked-fe4ec4`；删除一次性仓库与管理目录 `/tmp/opencode/hpm-exp03`、`/tmp/opencode/hpm-exp03b`、`/tmp/hpm-exp03-blocked`。未操作其他 tab、workspace 或用户会话；证据已复制到本目录。
- Pi 回归冒烟同样清理：关闭 tab `wJ:tF`，移除分支 `hpm/w-03-pi-smoke-afcb26` 与一次性仓库 `/tmp/opencode/hpm-exp03-pi`；最终 wJ 仅剩原有 `wJ:t1`、`wJ:t2`、`wJ:t3`。
