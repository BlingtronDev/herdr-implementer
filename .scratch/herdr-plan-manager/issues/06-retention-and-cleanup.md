# 06: 实现交付保留与按决定清理

**What to build:** 主脑能够在 worker 交付、失败或交接异常后保留现场，在确认集成或作出明确处置后结束及清理资源。清理与活动执行、自动交接之间具有一致行为，不因完成声明或旧状态误删成果。

**Blocked by:** 04 — 实现双运行时的上下文观测与自动 worker 交接。

**Status:** delivered（待主脑确认集成；执行记录与证据：[../evidence/06-retention-and-cleanup/README.md](../evidence/06-retention-and-cleanup/README.md)）

> 接口变化（主脑与工单 07／09 需知）：`stop` 现在确认全部登记会话的业务写入停止，并在交接竞争中负责暂停替换会话；响应增加 `business_stopped` 与会话状态，无法确认时形成 `stop-incomplete` 异常。交付（结果文件出现）后不再触发自动交接，`handoff` 也会拒绝。新增 `cleanup --worker <id> (--integrated <sha> | --disposition <text>) [--archive-uncommitted | --discard-uncommitted] [--delete-branch [--force-branch]]`，只有明确决定才移除登记资源；清零记录与归档位于 `workers/<id>/cleanup.json`、`workers/<id>/cleanup/`，`status --worker` 的 `cleanup` 字段可查询资源位置与未清理原因。见 [README E08](README.md)。

## 依据与边界

依据：[项目重建方案](../项目重建方案.md)第 6、8、10 节。

工具核对资源与可清理条件，主脑决定是否清理；工具不判断成果质量、不自行合并，也不建设旧执行恢复服务。

## 验收条件

- [x] Worker 交付后停止自动交接，结果、分支、worktree 和必要材料仍可供主脑检查与集成（阈值命中时抑制触发、`handoff` 拒绝；测试 `delivery_stops_automatic_handoff`、`stop_after_delivery`，真实 Pi／OpenCode 冒烟）。
- [x] stop 可处理正常执行与交接阶段，确认停止业务写入后保留现场；不会在交接竞争中留下未受控的新会话继续写入（`stop_registered_sessions` 覆盖所有登记会话；`stop_wins_handoff_race` 与既有交接停止测试；真实运行中停止冒烟）。
- [x] 只有收到主脑明确清理决定才执行 cleanup；集成信息或处置由主脑提供，不以 worker 自报交付代替清理决定（`--integrated`／`--disposition` 必填；交付后的 worker 仍返回 `decision-missing`）。
- [x] 清理前验证资源归属、活动执行和需保留的未提交内容；不强制移除仍有业务写入或未保存成果的 worktree（归属／监督／活动会话／未提交 blockers；未提交默认归档或明确丢弃）。
- [x] 正常结束且无未保存工作时可关闭登记会话；失败与异常保留可用现场，资源位置和未清理原因可查询（只关闭登记 tab；`status.cleanup` 与 `cleanup-blocked` 给出 blockers 与路径）。
- [x] 不关闭未登记终端，不清除归属不明路径；遇到 stale 或旧运行记录不自动删除分支／worktree（只遍历 `state.sessions`；`worktree-unowned` 拒绝；清理只由显式命令触发）。
- [x] Worktree 清理后保留必要交付与交接证据；分支删除遵循主脑明确决定和仓库习惯，不绑定“关闭会话即删除分支”（管理目录、结果、handoff 与归档保留；分支默认保留，`-d` 安全失败可读，`--force-branch` 才强制）。
- [x] 重复停止或清理有明确结果，不误操作其他资源；覆盖交付未集成、失败、未提交内容、归属不明和交接中停止等场景（`already_stopped`／`already_cleaned`；11 个新测试与真实冒烟）。

## 验证重点

覆盖验收场景 9，并验证与场景 3、4 的交接生命周期相容。真实冒烟见证据第 5 节；确定性测试见第 4 节。
