# 08-R2：正常交付后自动关闭 tab

## 结论

2026-09-13 用户确认采用“正常交付后自动关闭 tab，保留磁盘成果”。实现与验证完成，已纳入当前项目工作区，尚未提交／集成。

- 真实 Pi／OpenCode 各运行一个新工单：有效交付后 tab 自动关闭，未调用 stop 或 cleanup 来促成关闭；ack 在关闭验证之后，分支未集成。
- 本轮遗留 **7 个 worker tab 已关闭**。使用同一 `stop` 释放路径，并再次 stop 验证幂等；关闭前后分支 HEAD、worktree 路径／干净状态、结果文件 SHA256 全部一致。
- 实现 worker `w08-tab-release` 的 tab 也已通过新路径关闭；所有上述 worktree、分支、合同、结果和日志保留。
- Worker 完整测试 **144 passed**，专项 **38 passed**；主脑核实纳入的 5 个文件与验证版本逐字节相同，复用全量证据。真实双运行时及旧记录迁移路径验证通过。

## 来源与实现

工单：[08-R2](../../issues/08b-close-delivered-tabs.md)；计划外记录：[E12](../../issues/README.md)。沿用已确认 `opencode-go / deepseek-v4.1-flash / max`、总并发 3、OpenCode `--auto`。新实现的两个真实冒烟并发峰值 2；实现 worker 当时已经交付并停止业务。

为包含当前未提交的 08-R1 与方法文档，`prepare.py` 在一次性仓库中建立受控快照 `c706fad`，从此基线派出 Pi worker，交付提交为 `6d0a7e05364375421ac6fddedffff88e76693e54`。副本位置及基线见 [location.json](location.json)。副本提交不是本项目的集成 SHA。

变更仅涉及：

- `bin/plan_manager.py`：成功结果先保存，再释放终端；严格查询、占用检查、逐 tab 复查、可见的 `release` 记录；旧 delivered 记录重复 stop 复用释放路径，stop 不与活跃监督进程争写。
- `tests/fakes/bin/herdr`、`tests/test_plan_manager.py`：双运行时关闭、提前结果、无有效结果、脏工作区、失败保留、退出中变化、外来 pane、异常响应、并发 stop、多 tab 竞态与后续 cleanup。
- `docs/plan-management/{SKILL,operations}.md`：完成后的终端释放与磁盘清理解耦，说明 status/stop 的实际行为。

`status --worker <id>` 新增 `release`：`state=closed` 表示终端已释放；`state=retained` 的 `reason`／`message` 说明保留原因。失败、无有效交付、无法确认状态或有未提交内容时继续保留，关闭失败不改写 delivered 为业务失败。

## 原始证据

| 验证 | 证据 |
| --- | --- |
| Worker 验收及 144／38 passed | [result.json](logs/captured-worker/result.json)、[原始测试工具输出](logs/captured-worker/test-tool-results.json) |
| 精确补丁、基线与文件哈希 | [manifest.json](logs/captured-worker/manifest.json)、[worker.diff](logs/captured-worker/worker.diff) |
| Pi 无 stop 自动关闭 | [wait](logs/pi/wait.json)、[tab-closed](logs/pi/tab-closed.json)、[final-status](logs/pi/final-status.json)、[preserved-disk](logs/pi/preserved-disk.json) |
| OpenCode 无 stop 自动关闭 | [wait](logs/opencode/wait.json)、[tab-closed](logs/opencode/tab-closed.json)、[final-status](logs/opencode/final-status.json)、[preserved-disk](logs/opencode/preserved-disk.json) |
| 实际模型／thinking／cwd | `logs/{pi,opencode}/captured/runtime-config.json`，来自 Pi session 事件与 OpenCode 数据库 |
| 7 个遗留 tab 关闭与保留成果 | [before](logs/old/before.json)、每个 worker 的 `*-stop`／`*-stop-again`／`*-tab-closed`／`*-after`，以及 [verified](logs/old/verified.json) |
| 实现 worker tab 关闭 | [after](logs/repair-worker/after.json) |
| 主脑最终核对 | [verification.json](logs/verification.json)：5 文件字节一致、Python 语法、git diff 检查、两运行时实际配置与 closed 记录、旧资源不变 |

双轴审查发现并修正了：未知 agent 查询被当作退出、退出等待后占用数据过期、stop 与活跃监督进程争写、列表响应验证不足及多 tab 逐项关闭的竞争。复查确认相关修正；严格退出语义区别于旧的宽松退出 helper，保留独立实现。首轮意见及跟进见 [review-first.md](review-first.md)。

Herdr 无已确认的条件式原子关闭 API。当前每次关闭前复查能缩小检查到关闭之间的竞争窗口，但不宣称消除该窗口；没有扩建 Herdr 服务端或引入新的自动清理政策。

## 保留位置与后续使用

- 新冒烟仓库：`/tmp/opencode/hpm08-tabs-he0r3x0j/{pi-smoke,opencode-smoke}/`
- 实现副本：`/tmp/opencode/hpm08-tabs-he0r3x0j/source/`；worker worktree 为其 `.git/herdr-plan-manager/worktrees/w08-tab-release/`。
- 原 7 个 worker 的磁盘现场仍位于原 [08 证据索引](../08-integration-repair-and-closeout/README.md)列出的目录；其中关于 tab 保留的文字是当时事实，当前关闭状态以本页为准。
- 已退出监督进程的旧 delivered 记录，可再次运行 `python3 bin/plan_manager.py stop --repo <repo> --worker <id>`。关闭前仍检查条件；磁盘清理另行决定。

`smoke.py` 的动作由主脑明确逐项执行，`verify.py` 可离线核对捕获的证据及当前补丁。新一轮实验应使用新的证据目录／worker 标识，保留历史结果。
