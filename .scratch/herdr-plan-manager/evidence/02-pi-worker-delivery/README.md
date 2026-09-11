# 工单 02 执行记录：Pi 单工单启动、执行与成果交付

- 日期：2026-09-11
- 执行：工单 02 worker
- 授权配置（沿用工单 01 已确认值）：`kind=pi`、`provider=opencode-go`、`model=deepseek-v4.1-flash`、`thinking=max`，最大 2 个活跃 worker
- 状态：已交付（delivered）

## 1. 结论摘要

- 新入口 `bin/plan_manager.py` 提供 `start`、`status`、`read`、`stop` 四个操作；单个 Pi worker 可以从显式 base SHA 完成隔离启动、合同投递、后台监督、成果交付与停止保留。
- 无效或缺失配置（thinking 非法、模型不存在、base 非完整 SHA）在任何资源创建和业务投递之前失败，退出码 2，不产生 Herdr 调用与 worker 记录。
- 工单材料采用受控快照：主 checkout 中被忽略／未提交的文件会被复制到管理目录并记录来源与 sha256，worker 可读取；实测 worktree 中没有该文件，worker 仍成功读到并写入成果。
- 交付协议由工具做身份与资源一致性检查（工单、worker、branch HEAD、产物路径），不做质量验收；终端 idle／done 无有效结果不会被标记为交付。
- 结果缺失时只执行一次补报，仍无效则形成 `protocol-failure` / `missing-result` 待处理异常。
- 启动与投递的瞬态错误有有限机械重试；投递结果不确定时先观察 `herdr agent get`，确认已进入 `working` 则不重发。
- `stop` 结束业务执行与监督，保留结果、分支、worktree 与管理记录；真实 Pi 冒烟中一次停止约 0.9 秒完成。
- 监督进程心跳与存在性可在 `status` 中查看；监督进程被强杀后 `status` 显示 `supervisor-missing`，`stop` 可接管收尾。

## 2. 新增与改动

| 文件 | 说明 |
| --- | --- |
| `bin/plan_manager.py` | 新单 worker 生命周期工具（本工单范围：Pi） |
| `prompts/plan-worker.md` | 新 worker 合同模板（含结果协议、材料、边界与阻塞报告方式） |
| `tests/test_plan_manager.py` | 12 个确定性测试 |
| `tests/fakes/bin/herdr`、`tests/fakes/bin/pi`、`tests/fakes/scenario.py` | 确定性 fake Herdr／Pi 与场景 worker |

未改动：旧 `bin/dispatcher.py`、`bin/get_context.py`、`SKILL.md`、`schema/`，也未实现 OpenCode、自动交接、并发额度、wait／ack 与 cleanup（分别属于 03、04、05、06）。

## 3. 接口与运行记录

```bash
# 启动（运行前确认 HERDR_ENV=1 且处于目标 workspace）
python3 bin/plan_manager.py start \
  --repo <repo> --ticket-id <id> --title <title> --base <full-sha> \
  --material <spec-or-ticket-path> [--material ...] \
  --kind pi --provider <p> --model <m> --thinking <t> \
  [--instructions "<附加任务说明>"] [--worker-id <id>] [--branch <name>] [--worktree <path>] [--management-root <path>]

# 查看执行事实（含交付／异常事项、监督状态、branch HEAD、worktree 脏状态）
python3 bin/plan_manager.py status --repo <repo> [--worker <id>]

# 读取 worker 终端最近输出
python3 bin/plan_manager.py read --repo <repo> --worker <id> [--lines N]

# 停止业务执行与监督，保留现场
python3 bin/plan_manager.py stop --repo <repo> --worker <id> [--reason <text>]
```

- 管理目录默认在目标仓库 Git common dir 下：`<common>/herdr-plan-manager/workers/<worker-id>/`，包含 `state.json`（监督者唯一写入的生命周期状态）、`control.json`、`contract.md`、`materials/`（含 `manifest.json`）、`result.json`、`supervisor.log`。
- 分支默认 `hpm/<worker-id>`，worktree 默认 `<common>/herdr-plan-manager/worktrees/<worker-id>`，两者都可用参数覆盖；已有同名 worker、分支、worktree 或 Herdr agent 时拒绝复用。
- 监督进程由 `start` 后台启动（`start_new_session=True`，标准输出重定向到 `supervisor.log`），主脑工具调用返回后继续运行；生命周期终态或收到 stop 后自行退出。
- 监督进程每轮记录心跳；`status` 以 pid 存活与心跳新鲜度报告 `supervisor-missing`／`supervisor-stale`，不伪装为正常运行。

## 4. 确定性测试

```bash
python3 -m pytest tests/test_plan_manager.py -q   # 12 passed
python3 -m pytest tests/ -q                      # 51 passed（含旧测试）
```

| 测试 | 覆盖的验收点 |
| --- | --- |
| `test_delivered_code_ticket` | 正常代码交付、材料快照可见性、manifest 来源与哈希、HEAD 一致性、监督日志 |
| `test_delivered_noncode_ticket` | 调查类交付，不制造空提交，branch 保持 base |
| `test_idle_without_result_is_not_delivery` | idle 无结果不算完成；只补报一次后形成协议异常 |
| `test_wrong_head_is_protocol_failure` | 错误 HEAD 不被接受为交付 |
| `test_wrong_worker_identity_is_protocol_failure` | 结果身份与登记 worker 不一致 |
| `test_needs_decision_is_an_exception` | 待决策形成可读异常事项 |
| `test_stop_retains_scene_and_is_idempotent` | 停止保留分支／worktree／记录，重复停止幂等 |
| `test_start_retries_transient_pane_busy` | 启动瞬态错误有限重试 |
| `test_uncertain_prompt_is_observed_not_resent` | 投递结果不确定先观察，不盲目重发 |
| `test_invalid_configuration_fails_before_delivery` | 非法 thinking／模型／base 在业务投递前失败，无 Herdr 调用与记录 |
| `test_registered_worker_and_branch_are_not_reused` | 不复用归属不明资源 |
| `test_supervisor_missing_is_visible_and_stoppable` | 监督缺失可见，stop 可接管 |

## 5. 真实 Pi 冒烟

环境：Herdr 0.8.2、Pi 0.85.1，workspace `wJ`；一次性仓库 `/tmp/opencode/hpm-exp02/repo`，base `aac2997`。材料 `repo/.scratch/ticket.md` 被 `.gitignore` 忽略且未提交。

### 冒烟 1：正常交付与材料可见性

- `start` 成功：agent `w-smoke-1-15301e`，tab `wJ:t6`，pane `wJ:p6`，branch `hpm/w-smoke-1-15301e`（`logs/start.json`）。
- 实测 worktree 中无 `.scratch/ticket.md`，worker 仍从快照读到材料内容，创建并提交 `smoke.txt`（内容 `MATERIAL 7f3a9c`，提交 `ed36cbd`），写出结构化结果（`logs/visibility-and-read.txt`、`logs/repo-state.txt`、`logs/result.json`）。
- 监督进程记录交付：`lifecycle=delivered`，`head` 与真实 branch HEAD 一致，item `i001` 为 delivery（`logs/status-delivered.json`、`logs/delivery-summary.json`、`logs/supervisor-delivered.log`）。
- 材料 manifest 记录来源 `repo/.scratch/ticket.md` 与 sha256（`logs/materials-manifest.json`）。
- 交付后 `stop` 返回 `already_terminal=true`，分支、worktree、结果与记录全部保留（`logs/stop-after-delivery.json`、`logs/status-list.json`）。
- `read` 成功读取终端最近输出（`logs/visibility-and-read.txt`）。
- 结果中申报的验证命令与退出码见 `logs/result.json`；交付内容 `git diff` 见 `logs/repo-state.txt`。

### 冒烟 2：执行中停止保留现场

- `start` 成功：agent `w-smoke-2-b2dae1`，tab `wJ:t7`，附加指令要求先 `sleep 300`（`logs/start-smoke2.json`）。
- 观测到 `working` 后执行 `stop --reason "smoke stop retention"`，0.9 秒内完成；`lifecycle=stopped`、监督进程 `exited`、agent 回落到 `done`（`logs/stop-smoke2.json`、`logs/status-smoke2-stopped.json`）。
- 分支 `hpm/w-smoke-2-b2dae1` 与 worktree 保留，未自动删除或清理。

### 冒烟资源清理

- 2026-09-11 17:54（本地）：关闭实验 tab `wJ:t6`、`wJ:t7`，`git worktree remove` 两个实验 worktree，删除两个实验分支，删除一次性仓库 `/tmp/opencode/hpm-exp02`；仅保留本目录证据副本。未操作其他 tab、workspace 或用户会话。

## 6. 验收条件核对

- [x] 用工单标识、明确材料、确认的 Pi 配置、仓库和完整 base SHA 发起执行；缺失／无效配置在业务投递前明确失败（测试 + `logs/start.json`）。
- [x] 从指定基线创建独立分支与 worktree，登记 worker、工单、配置、Herdr 定位、监督身份、结果位置；不复用归属不明资源（测试 + `logs/status-delivered.json`）。
- [x] worker 能读取主 checkout 中未提交或被忽略的材料；使用受控快照并保留来源（`logs/visibility-and-read.txt`、`logs/materials-manifest.json`）。
- [x] 投递合同明确范围、验收、材料、工作目录、结果位置与阻塞报告方式（`logs/contract.md`）。
- [x] 启动调用返回后监督继续运行；`status` 返回启动、运行、交付、失败、待决策事实；监督缺失可见（测试 + `logs/supervisor-delivered.log`）。
- [x] 交付结果关联工单与 worker，含摘要、验收、实际验证、HEAD／产物、遗留问题；工具只做协议与资源一致性检查（`logs/result.json`）。
- [x] 代码与非代码工单均能交付，后者不强制空提交；idle／done 无有效结果不标记交付（测试）。
- [x] 瞬态启动／投递错误有限重试；不确定时先观察不重发；缺失结果一次补报后形成协议异常（测试）。
- [x] `stop` 结束业务执行与监督并保留现场；交付与失败都不自动强制删除；无时间／费用预算（测试 + 冒烟 2）。
- [x] 提供可重复的单工单步骤与覆盖正常交付、非代码交付、材料可见性、结果缺失、错误分支／HEAD、停止保留的验证（本 README 第 3、4、5 节）。

## 7. 未决与边界

- 自动交接与上下文观测属于工单 04；本工单不读取上下文，也不做会话替换。
- OpenCode 适配属于工单 03；当前 `--kind opencode` 会在校验阶段明确拒绝。
- 并发额度、wait／ack 属于工单 05；本工单的 `items` 已带稳定 id 和 `acked_at` 占位，供 05 扩展。
- 按决定 cleanup 属于工单 06；本工单只保证 `stop` 保留现场，未实现删除接口。
- 交付或终态后 `stop` 报告 `already_terminal`，不会强制结束仍在运行的 TUI；该交互由 04／06 细化。
- E01（Pi 交互式提问不报 `blocked`）仍未解决。本工单的处理：worker 合同禁止使用 `ask_user_question` 等交互 UI，需要决策时写 `needs-decision` 结果；监督层对 Herdr 上报的 `blocked` 形成异常。若 Pi 仍停在交互 UI，状态保持 `working`，主脑可用 `read` 查看后处置；系统性兜底由 04 决定。该事项需主脑更新共享 README 的 E01 条目。
