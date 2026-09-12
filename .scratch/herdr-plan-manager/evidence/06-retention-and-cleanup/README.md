# 工单 06 执行记录：交付保留与按决定清理

- 日期：2026-09-12
- 执行：工单 06 worker
- 授权配置（沿用 01–05 已确认值）：`kind=pi`、`provider=opencode-go`、`model=deepseek-v4.1-flash`、`thinking=max`；冒烟另用同一配置的 `kind=opencode` run
- 状态：已交付（delivered，待主脑确认集成）

## 1. 结论摘要

- `stop` 现在**确认业务写入停止**：暂停（interrupt + 等待 settled）所有登记会话，而不只是当前会话；交接竞争中的新会话同样会被暂停，不会留下未受控写者。响应增加 `business_stopped` 与各会话状态；无法确认时形成 `stop-incomplete` 待处理异常并保留现场，不伪报停止。
- **交付后停止自动交接**：worker 写出结果文件后，监督进程仍继续观测上下文，但不再因阈值触发交接；`handoff` 命令对已有结果文件的 worker 直接拒绝。结果、分支、worktree、材料与交接材料保持可检查。
- 新增 `cleanup` 命令，**只有收到主脑明确决定才会清理**：必须提供 `--integrated <sha>` 或 `--disposition <text>`，worker 自报交付不构成清理决定。
- 清理前验证资源归属（是目标仓库登记的 worktree、分支匹配、不是主 checkout）、活动执行（监督进程不在、没有 `working` 会话）与未提交内容；不满足时返回 `cleanup-blocked`（rc 3）并保留现场，列出未清理原因。
- 未提交内容默认不动：`--archive-uncommitted` 把 tracked diff（`tracked.patch`）与 untracked 文件复制到 `workers/<id>/cleanup/` 后再删除；`--discard-uncommitted` 才明确丢弃。仍有业务写入或未保存成果的 worktree 不会被强制移除。
- 会话关闭只针对该 worker **登记过的 tab**；分支默认保留，`--delete-branch` 使用安全的 `-d`，未合并时报告拒绝原因，`--force-branch` 才使用 `-D`。管理目录（结果、合同、材料、handoff、监督日志、归档）不随清理删除。
- 重复 stop／cleanup 幂等：`already_stopped`／`already_cleaned` 明确返回；归属不明的路径、未登记终端、stale 记录都不会被自动删除。
- 确定性测试 111 passed（原 100 + 本工单 10 个新测试 + 1 个针对失败交付的清理测试）；真实 Herdr 冒烟覆盖 Pi 交付后清理、OpenCode 非代码交付的未提交归档、交接后清理（两个 tab）与运行中停止。

## 2. 新增与改动

| 文件 | 说明 |
| --- | --- |
| `bin/plan_manager.py` | 新增 `cleanup` 命令与清理记录；`stop` 改为确认停止全部登记会话并覆盖交接竞争；交付后抑制自动交接；`status` 增加 `cleanup` 事实；`git worktree remove` 前完成归属／活动／未提交检查；`tab_close` 返回错误码以便区分已关闭与失败 |
| `tests/test_plan_manager.py` | Harness 增加 `cleanup` 支持；新增 11 个测试（含 `needs-decision` 失败交付的清理） |
| `tests/fakes/scenario.py` | 新增 `deliver-then-work` 行为（先交付、继续 working）与交接续接窗口（可在中断时放弃写入） |
| `tests/fakes/bin/herdr` | prompt 状态先于 prompt 文件落盘，使“续接会话被中断后不再写入”可确定性验证 |
| `.scratch/herdr-plan-manager/issues/06-retention-and-cleanup.md` | 勾选验收条件并标注交付状态 |
| `.scratch/herdr-plan-manager/issues/README.md` | 新增 E08：cleanup 接口与停止语义变化同步 07／09 |

未改动：`prompts/plan-worker.md`、`schema/`、旧 `bin/dispatcher.py`、`SKILL.md`。主脑执行方法与迁移不在本工单范围。

## 3. 接口与存储

```bash
# 停止：确认业务写入停止、保留现场；可处理运行中与交接阶段
python3 bin/plan_manager.py stop --repo <repo> --worker <worker-id> [--reason <text>]

# 清理：必须给出主脑决定；默认保留分支，关闭已登记会话
python3 bin/plan_manager.py cleanup --repo <repo> --worker <worker-id> \
  (--integrated <full-sha> | --disposition <text>) \
  [--archive-uncommitted | --discard-uncommitted] \
  [--delete-branch [--force-branch]]
```

| 文件 | 唯一写入者 | 内容 |
| --- | --- | --- |
| `workers/<id>/cleanup.json` | `cleanup`（worker 级 flock） | 主脑决定（integration SHA／disposition）、最近一次尝试（结果、移除项、blockers）、worktree 移除与分支删除时间、已关闭 tab |
| `workers/<id>/cleanup/uncommitted-<时间戳>/` | `cleanup` | `manifest.json`、`tracked.patch`、`untracked/**`：清理前的未提交内容归档 |
| `workers/<id>/state.json` | 监督进程；监督缺失时由 `stop` | 生命周期、会话（含每个会话的 tab）、结果、事项；stop 时把活动会话标记为 `ended` |

- `cleanup` 返回：`cleaned`、`already_cleaned`、`decision`、`worktree`（existed/removed/uncommitted=none|archived|discarded）、`branch`（deleted／retained_reason）、`sessions.closed_tabs`、`archive`、`evidence`（管理目录、结果、handoff 路径）。
- `cleanup-blocked`（rc 3）blockers：`worker-not-stopped`、`decision-missing`、`supervisor-running`、`active-session`、`worktree-is-main-checkout`、`worktree-unowned`、`uncommitted-content`、`archive-failed`、`session-close-failed`、`worktree-remove-failed`。
- `status --worker` 的 `cleanup` 字段可查询清理决定、`cleaned`、blockers、资源位置（worktree、branch、sessions、handoffs、management dir）与最近一次尝试，因此“资源在哪里、为什么未清理”无需读日志即可回答。
- 生命周期仍然是唯一活跃判定：清理只对终态 worker 生效（否则 `worker-not-stopped`），不会绕过 stop 造成额度泄漏。

## 4. 确定性测试

```bash
python3 -m pytest tests/ -q                                                      # 111 passed
python3 -m pytest tests/test_plan_manager.py -q -k "cleanup or delivery_stops_automatic_handoff or stop_wins_handoff or stop_after_delivery or stop_retains_scene or stop_during_handoff"   # 14 passed
```

原始输出：[`logs/deterministic/test-all.txt`](logs/deterministic/test-all.txt)、[`logs/deterministic/test-06-selection.txt`](logs/deterministic/test-06-selection.txt)。

| 测试 | 覆盖的验收点 |
| --- | --- |
| `test_cleanup_requires_an_explicit_decision` | 无决定不清理；互斥参数与 `--force-branch` 前置校验 |
| `test_cleanup_removes_worktree_but_keeps_branch_and_evidence[pi/opencode]` | 移除 worktree、保留分支与结果／合同／cleanup 记录、只关闭登记 tab、重复清理 `already_cleaned`、未合并分支安全拒绝、`--force-branch` 后删除 |
| `test_cleanup_refuses_live_worker_and_succeeds_after_stop` | 活动执行与监督运行前的 `cleanup-blocked`；stop 后清理成功并保留分支 |
| `test_cleanup_requires_a_decision_for_uncommitted_content_and_archives_it` | 未提交内容阻断；`status` 可见原因；归档后移除且保留归档文件 |
| `test_cleanup_never_removes_unowned_paths` | 归属不明路径拒绝且原文件保留；未登记 worker 报错 |
| `test_cleanup_refuses_worker_that_was_never_stopped` | 监督缺失但未 stop 时 `worker-not-stopped`＋`active-session` 阻断；stop 后可清理 |
| `test_cleanup_after_a_failed_worker_keeps_the_evidence` | `needs-decision` 失败交付可清理，`result.json` 与 cleanup 记录保留 |
| `test_delivery_stops_automatic_handoff` | 结果文件已存在时即使阈值命中也不交接（session 数不变、无 handoff 历史、prompt 只有 1 条） |
| `test_stop_after_delivery_keeps_the_result_and_the_scene` | 交付后 stop 不覆盖 delivered、不重复记录交付、结果与 worktree 保留 |
| `test_stop_wins_handoff_race_without_a_new_writer` | 交接已产生新会话后 stop 会暂停新会话；无 `working` 会话、无新提交、无续接文件写入；cleanup 关闭两个 tab |
| 既有 `test_stop_retains_scene_and_is_idempotent`、`test_stop_during_handoff_aborts_without_replacement` | stop 保留现场／幂等、交接中停不影响既有行为 |

## 5. 真实 Herdr 冒烟

环境：Herdr 0.8.2、Pi 0.85.1、OpenCode 1.18.30，workspace `wJ`；一次性仓库 `/tmp/opencode/hpm-exp06/repo`，base `8948648`。run 配置：`smoke06`（pi）与 `smoke06-oc`（opencode），均 `opencode-go / deepseek-v4.1-flash / max`，`max_workers=2`。原始日志见 [`logs/smoke/`](logs/smoke/)，汇总见 [`30-verification.txt`](logs/smoke/30-verification.txt)。

1. **Pi 交付 → 清理删除分支**（`01`–`08`）：ALPHA-06 交付 `f5ca97c`，`main` 仍 `8948648`（交付 ≠ 集成）。`cleanup --integrated f5ca97c --delete-branch` 移除 worktree、关闭 tab `wJ:t13`，未合并分支安全拒绝并给出 `git branch -D` 提示；重复清理返回 `already_cleaned: true`；`--delete-branch --force-branch` 后分支删除（`deleted: true, forced: true`）。`result.json`、`contract.md`、`materials/`、`supervisor.log`、`cleanup.json` 全部保留。
2. **OpenCode 非代码交付 → 未提交归档**（`09`–`14`）：NONCODE-06 交付（`head: null`，artifact `findings.md`），`prompt_attempts=2`（E02 的确认重投仍生效）。不带归档的 cleanup 返回 rc 3、blocker `uncommitted-content`（`?? findings.md`），worktree 保留；`--archive-uncommitted` 后归档 `untracked/findings.md`（内容 `OC-FINDINGS-06`）并移除 worktree、关闭 tab `wJ:t14`。`--auto`（E03）使会话可读管理目录，无权限询问。
3. **交接后清理（场景 3、4 相容）**（`15`–`23`）：HANDOFF-06 由显式 `handoff` 从 session 1（tab `wJ:t15`）交接到 session 2（tab `wJ:t16`），`handoffs/handoff-001.md` 保留；交付 `d93d9d3`，`scratch-log.txt` 未提交。`cleanup --archive-uncommitted` 归档 scratch 日志、**一次关闭两个 tab**，未合并分支安全拒绝，`--force-branch` 后删除。
4. **运行中停止 → 清理**（`24`–`29`）：STOP-06 在 `working` 时 `stop`，返回 `business_stopped: true`、lifecycle `stopped`，session 1 标记 ended；worktree 保留 `slow-01.txt`…`slow-30.txt` 未提交成果（`status` 显示 `uncommitted-content`）。不带归档的 cleanup rc 3 保留现场；`--archive-uncommitted` 归档 30 个文件后移除 worktree、关闭 tab `wJ:t17`。`main` 仍为初始提交，`git worktree list` 只剩主 checkout，`wJ:t13`–`wJ:t17` 全部关闭，其他 workspace 的 tab 未受影响。

冒烟资源：worker worktree、分支与登记 tab 已通过 `cleanup` 处理；一次性仓库 `/tmp/opencode/hpm-exp06` 在证据复制后保留，未操作工作区其他 tab。

## 6. 验收条件核对

- [x] Worker 交付后停止自动交接，结果、分支、worktree 和必要材料仍可供主脑检查与集成（阈值命中时 `result_declared` 抑制触发、`handoff` 拒绝；测试 `delivery_stops_automatic_handoff`、`stop_after_delivery`，冒烟 1、3）。
- [x] stop 可处理正常执行与交接阶段，确认停止业务写入后保留现场；不会在交接竞争中留下未受控的新会话继续写入（`stop_registered_sessions` 覆盖所有登记会话；测试 `stop_wins_handoff_race`，冒烟 4；既有 stop 测试保持通过）。
- [x] 只有收到主脑明确清理决定才执行 cleanup；集成信息或处置由主脑提供，不以 worker 自报交付代替清理决定（`--integrated`／`--disposition` 必填；测试 + 冒烟全流程）。
- [x] 清理前验证资源归属、活动执行和需保留的未提交内容；不强制移除仍有业务写入或未保存成果的 worktree（归属／监督／活动会话／未提交 blockers；测试 3、4、5、6，冒烟 2、4）。
- [x] 正常结束且无未保存工作时可关闭登记会话；失败与异常保留可用现场，资源位置和未清理原因可查询（cleanup 关闭登记 tab；`status.cleanup` 与 `cleanup-blocked` 返回资源位置与 blockers；冒烟 1、3）。
- [x] 不关闭未登记终端，不清除归属不明路径；遇到 stale 或旧运行记录不自动删除分支／worktree（只遍历 `state.sessions` 的 tab；`worktree-unowned` 拒绝；清理仅由显式命令触发）。
- [x] Worktree 清理后保留必要交付与交接证据；分支删除遵循主脑明确决定和仓库习惯，不绑定“关闭会话即删除分支”（管理目录与归档不删除；分支默认保留，`-d`／`-D` 两级明确决定）。
- [x] 重复停止或清理有明确结果，不误操作其他资源；覆盖交付未集成、失败、未提交内容、归属不明和交接中停止等场景（`already_cleaned`／`already_stopped`；测试 2、3、4、5、6、7、10）。
