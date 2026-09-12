# 工单 05 执行记录：多 worker 并发与待处理事项等待

- 日期：2026-09-12
- 执行：工单 05 worker
- 授权配置（沿用 01–04 已确认值）：`kind=pi`、`provider=opencode-go`、`model=deepseek-v4.1-flash`、`thinking=max`
- 状态：已交付（delivered，待主脑确认集成）

## 1. 结论摘要

- 新增执行（run）登记：`init-run` 一次确认 `kind/provider/model/thinking` 与 `max_workers`，写入 `<管理目录>/runs/<run-id>/run.json`；`start` 必须关联 `--run`，传入的运行配置必须与已确认值一致，不静默替换。
- 并发额度按 run 内**活跃 worker**（生命周期非终态）计数，与终端数量、会话序号无关；额度检查与 worker 登记在 run 级 `flock` 内完成，并发 `start` 不会突破上限。
- 交付、异常在 worker `state.json` 中耐久保存，全局事项标识为 `<worker-id>/<item-id>`（如 `w-smoke05-a/i001`）；`wait` 读取持久事实，结果先于 wait 产生时立即返回。
- `wait` 返回 run 内任一未 ack 事项，不等待慢 worker；无事项时在工具内部分轮询等待变化，默认无限等待，`--timeout` 只限制本次等待窗口。
- `ack` 只写 `<worker-id>/ack.json`（worker 级 `flock`），不触碰 `state.json`，不改变交付或集成结论；同一事项 ack 一次后不再返回，之后的新事项仍会被发现。
- 监督进程缺失会形成派生的 `supervisor-missing` 异常项（稳定标识 `<worker-id>/supervisor-missing`）；`start` 在监督启动前死亡会形成派生的 `start-stalled` 异常项（`<worker-id>/start-stalled`），两种情况下 `wait` 都不再假装等待健康 worker；停止或终态后该项自然消失，工具不自动接管或重建监督。
- 真实 Pi 冒烟（workspace `wJ`，一次性仓库）跑通：2 个 Pi worker 并发运行、第 3 个启动被额度拒绝、`wait` 在另一 worker 仍运行时返回首个交付、ack 后不重复返回、交付与停止都释放额度、目标分支未集成（交付 ≠ 集成）。
- 确定性测试 100 passed（原 85 + 本工单 15）。

## 2. 新增与改动

| 文件 | 说明 |
| --- | --- |
| `bin/plan_manager.py` | 新增 `init-run`／`wait`／`ack`；`start` 关联 run 并在锁内做额度检查与登记；`status` 支持 `--run`；事项视图、ack 读取与派生监督缺失／启动夭折异常 |
| `tests/test_plan_manager.py` | Harness 增加 run 与 wait／ack 支持；新增 15 个本工单测试；2 个既有配置校验测试改为“`init-run` 校验配置、`start` 校验与 run 一致” |
| `.scratch/herdr-plan-manager/issues/05-concurrency-and-wait-any.md` | 勾选验收条件并标注接口变化 |

未改动：`prompts/plan-worker.md`、`schema/`、旧 `bin/dispatcher.py`、`SKILL.md`。工单 06（清理）、07（主脑执行方法）不在本工单范围。

## 3. 接口与运行记录

```bash
# 初始化执行：一次确认运行配置与并发上限
python3 bin/plan_manager.py init-run --repo <repo> [--run-id <id>] \
  --kind <pi|opencode> --provider <p> --model <m> --thinking <t> --max-workers <N>

# 启动 worker（必须属于某个 run；配置参数可省略，给出时必须与 run 一致）
python3 bin/plan_manager.py start --repo <repo> --run <run-id> \
  --ticket-id <id> --base <full-sha> --material <path> [--worker-id <id>]

# 查看执行：run 汇总（活跃额度、worker、未处理事项）或单个 worker
python3 bin/plan_manager.py status --repo <repo> --run <run-id>
python3 bin/plan_manager.py status --repo <repo> --worker <id>

# 等待任一未处理事项：默认无限等待；--timeout 只限制窗口
python3 bin/plan_manager.py wait --repo <repo> --run <run-id> [--timeout <seconds>] [--poll <seconds>]

# 确认已处理：可重复，幂等
python3 bin/plan_manager.py ack --repo <repo> --item <worker-id>/<item-id> [--note <text>]
```

存储与写入责任（同一事实只保存一次）：

| 文件 | 唯一写入者 | 内容 |
| --- | --- | --- |
| `runs/<run-id>/run.json` | `init-run` | 已确认运行配置、`max_workers`、仓库、创建时间 |
| `runs/<run-id>/allocate.lock` | —（flock 锁文件） | 串行化额度检查与 worker 登记 |
| `workers/<id>/state.json` | 监督进程（启动前由 `start`、监督缺失时由 `stop`） | 生命周期、事项（`items`，只追加）、会话、结果；`starter` 记录启动进程 pid 供 `start-stalled` 派生 |
| `workers/<id>/ack.json` | `ack`（worker 级 flock） | 已处理事项及时间、备注 |
| `workers/<id>/control.json` | `handoff`／`stop` 命令 | 主动交接与停止请求 |

- 活跃判定：`lifecycle.state ∉ {delivered, failed, needs-decision, protocol-failure, launch-failed, agent-exited, stopped}`。交付、失败、待决策、停止都不占槽；`handing-off`／`handoff-failed` 期间同一 worker 只占 1 个槽。
- `wait` 不依赖 Herdr 通知：只读持久事实，因此通知缺失不会丢结果；反之通知存在也不会被当成结果。`timed_out: true` 只表示窗口内无事项，不是工单结论。
- 监督缺失项由 `lifecycle`、`supervisor.state` 与 pid 存活派生；`start-stalled` 由启动进程 pid 的存活判定派生；两者都不写入 `state.json`。worker 进入终态或已请求停止后不再出现。

## 4. 确定性测试

```bash
python3 -m pytest tests/test_plan_manager.py -q -k "init_run or concurrent_starts or ... "   # 15 passed
python3 -m pytest tests/ -q                                                                  # 100 passed
```

原始输出：[`logs/deterministic/test-05-selection.txt`](logs/deterministic/test-05-selection.txt)、[`logs/deterministic/test-all.txt`](logs/deterministic/test-all.txt)。

| 测试 | 覆盖的验收点 |
| --- | --- |
| `test_init_run_registers_confirmed_configuration_and_quota` | run 登记、配置与上限校验、重复 run、非法 run id |
| `test_concurrent_starts_cannot_exceed_max_workers` | 两个 `start` 进程竞争 `max_workers=1`，恰好一个成功，另一个报 `concurrency limit` |
| `test_delivered_worker_frees_its_slot` | 交付后 `active_count=0`，同 run 可再启动新 worker |
| `test_stopped_worker_frees_its_slot` | 停止释放额度；停止前同 run 启动被拒 |
| `test_session_handoff_does_not_consume_an_extra_slot` | 交接中 `active_count=1`、`worker_count=1`，交接后仍只占 1 个槽 |
| `test_wait_returns_the_first_item_without_waiting_for_slow_workers` | wait-any 在慢 worker 仍活跃时返回首个交付（无批次屏障） |
| `test_wait_returns_a_result_recorded_before_the_call` | 晚订阅：结果先写入，wait 立即返回（<3s） |
| `test_wait_waits_for_change_instead_of_external_polling` | 无事项时等待变化后才返回，主脑无需外部轮询终端 |
| `test_wait_timeout_is_not_a_result` | 窗口超时返回空列表与 `timed_out: true`，worker 仍为 running，不产生交付 |
| `test_blocked_worker_forms_an_exception_not_a_success` | blocked 形成异常项而非成功；ack 后不再返回，worker 仍在运行 |
| `test_ack_hides_a_handled_item_and_new_items_still_appear` | ack 幂等、ack 后不重复、后续新异常仍被发现、`items[].acked` 可见 |
| `test_ack_rejects_unknown_malformed_or_unregistered_items` | 事项标识校验，防止误 ack |
| `test_supervisor_missing_is_a_pending_exception` | 强杀监督进程后 `wait` 返回 `supervisor-missing`，ack 后不重复，stop 可收尾 |
| `test_stalled_start_is_a_pending_exception` | 启动进程在监督存在前死亡时形成 `start-stalled` 异常，仍占槽直到 stop；ack 后不重复 |
| `test_wait_scopes_items_to_its_run` | 多 run 隔离，wait 只返回本 run 事项 |

既有测试调整（接口演进的直接后果）：`test_invalid_configuration_fails_before_delivery` 与 `test_opencode_rejects_unsupported_configuration_before_delivery` 现在断言“`init-run` 校验模型/thinking，`start` 的配置不一致会在登记前失败”。

## 5. 真实 Pi 冒烟

环境：Herdr 0.8.2、Pi 0.85.1，workspace `wJ`；一次性仓库 `/tmp/opencode/hpm-exp05/repo`，base `090d7ad`。两个小工单分别要求提交 `artifact-a.txt`（`ALPHA-05`）与 `artifact-b.txt`（`BETA-05`）。日志见 [`logs/`](logs/)。

1. **执行登记**：`init-run --run-id smoke-05 --max-workers 2`（`01-init-run.json`）。
2. **并发启动**：worker A（`w-smoke05-a`，tab `wJ:t0`）与 worker B（`w-smoke05-b`，tab `wJ:t11`）均成功，`start` 返回 `active_workers=1`、`2`（`02-start-a.json`、`03-start-b.json`）；`status --run` 显示 `active_count=2`、两个活跃 worker（`04-run-status-two-active.json`）。
3. **额度拒绝**：第 3 个 `start`（`w-smoke05-c`）返回 2，错误为 `run 'smoke-05' is at its concurrency limit: 2 active worker(s) of max_workers=2 (w-smoke05-a, w-smoke05-b)`（`05-start-c-refused.err`）。
4. **wait-any / 晚订阅**：A 先交付（提交 `fe10135`）；`wait --timeout 300` 在 0.13 秒内返回 `w-smoke05-a/i001`（delivery），此时 B 仍活跃（`06-wait-first.json`；`status` 输出 `active_worker_ids=["w-smoke05-b"]`）。
5. **ack 与后续事项**：ack A 的事项后重复 ack 返回 `already_acked=true`（`07-ack-a.json`、`20-ack-a.json`）；`wait` 随后立即返回 B 的交付 `w-smoke05-b/i001`（`08-wait-second.json`）；ack B（`09-ack-b.json`、`21-ack-b.json`）后 `wait --timeout 5` 返回空列表与 `timed_out: true`，并带“timeout is not a task result”说明（`10-wait-none.json`）。
6. **额度释放**：两个交付后同 run 启动第 3 个 worker（`w-smoke05-c`）被接受，`active_workers=1`（`11-start-c.json`）；随即 `stop` 该 worker，`status --run` 显示 `active_count=0`、`worker_count=3`，分支与 worktree 保留（`12-stop-c.json`、`13-run-status-after-stop.json`）。
7. **交付 ≠ 集成**：目标分支 `main` 仍只有初始提交；A、B 的成果只在各自分支（`14-integration-not-done.txt`）。结果文件与监督日志见 `15-result-a.json`、`16-result-b.json`、`17-supervisor-a.log`、`18-supervisor-b.log`。
8. **记录责任核对**：`state.json` 内 `items` 无 ack 字段（`19-worker-facts.txt`），ack 状态在 `ack.json`（`20/21`）。

冒烟资源清理（2026-09-12 20:0x 本地）：关闭 `wJ:t0`、`wJ:t11`、`wJ:t12`；`git worktree remove` 三个 worktree；删除 `hpm/w-smoke05-*` 分支；删除一次性仓库 `/tmp/opencode/hpm-exp05`。工作区 `wJ` 恢复为原有 3 个 tab，未操作其他 tab 或用户会话。

## 6. 验收条件核对

- [x] 当前执行关联明确 max_workers，允许在上限内连续启动独立 worker；并发启动请求不能因竞争突破额度（`init-run`／`start` 锁内登记；测试 + 冒烟 2、3）。
- [x] 额度按活跃 worker 而非终端数量或会话序号计；已确认停止业务执行的交付不占槽，同一 worker 替换会话不重复占槽（`is_active_worker`；测试 `delivered/stopped/handoff` + 冒烟 6）。
- [x] 待处理交付和异常耐久保存并具有稳定标识；结果先于 wait 产生时，wait 仍能立即返回（`state.json` + `worker/item` 标识；测试 + 冒烟 4）。
- [x] wait 可返回任一可处理事项，不等待其他慢 worker；无事项时等待变化，不要求主脑外部反复查询所有终端（测试 6、7、8 + 冒烟 4）。
- [x] ack 后同一事项不再作为未处理项返回；ack 不修改工单验收或集成结论，后续新异常仍可被发现（`ack.json` 只记录处理；测试 11、12 + 冒烟 5）。
- [x] Herdr 状态事件只触发事实检查，通知缺失不导致结果丢失；不把单次等待超时、idle/done 或 blocked 直接解释为工单成功（wait 只读持久事实；测试 9、10 + 冒烟 5）。
- [x] 监督进程缺失可形成可处理异常，不能无限假装等待健康 worker；不自动接管失联执行或重建监督（派生 `supervisor-missing`／`start-stalled`；测试 13、14）。
- [x] 记录更新与 ack 的并发不会丢失事项或覆盖生命周期进展，采用简单明确的写入责任（`state.json` 监督者写、`ack.json` ack 写、各自 flock；测试 11、13 + 第 3 节写入责任表）。
- [x] 验证多个 worker 先后交付、晚订阅、重复确认、通知缺失、并发启动及同一 worker 会话变更；工具未引入批次屏障、DAG 调度或任务时长／费用预算（第 4、5 节；代码中无批次、依赖图或时间/费用预算逻辑）。

## 7. 未决与边界

- **接口变化（需主脑知悉并可能记入共享 README）**：`start` 现在必须带 `--run`；运行配置校验前移到 `init-run`，`start` 只做一致性校验。工单 07 的操作文档需先 `init-run`，再按并发上限动态 `start`，用 `wait`／`ack` 收割事项。这不改变 07 的职责边界，只是接口落点变化。
- `wait` 默认无限等待（`--timeout 0`）；有界等待由调用方给出，超时不等于失败。工单 07 可选择较长窗口（如数分钟）以在等待期间保留主脑判断机会。
- 工单 06（清理）尚未实现：本工单的停止/交付都保留现场，冒烟中的 tab、worktree、分支由本次执行手工清理。`cleanup` 需以本工单的 run/worker 登记为依据。
- 工单 07 的阈值使用建议仍适用 E05：`--handoff-tokens` 应高于新会话种子上下文；本工单未新增抑制逻辑。
- 已知残余限制（E01 派生）：Pi 侧除 `blocked` 事件外的其它停顿仍可能显示 `working`；本工单的监督缺失/超时语义不依赖该信号，也未加重依赖。
- 本工单未实现 run 级全局故障恢复、失联监督自动重建或结果 exactly-once 交付；这些是第一版明确不建设的范围。
