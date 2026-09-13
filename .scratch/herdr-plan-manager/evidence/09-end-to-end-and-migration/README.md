# 09 端到端验收与迁移：执行记录

**最终结论（2026-09-13）：09 已交付，建设与验收完成。** 新入口已安装到 `/home/blingtron/.agents/skills/herdr-plan-manager`，12 个方案场景有分层证据，双运行时最终补验通过。无未解决的验收阻塞；本项目仍未创建提交，最终提交由用户决定，工单保持 delivered 而非 integrated。

## 目标与授权

- 输入：[重建方案](../../项目重建方案.md)、[完整初始工单与变更](../../issues/README.md)、[09](../../issues/09-end-to-end-and-migration.md)。主脑已读取 01–09 与 08-R1/R2。
- 目标：按方案完成 12 场景证据核验、必要组合实测、正式 `herdr-plan-manager` 入口及旧实现迁移。
- 目标 checkout：`/home/blingtron/.agents/skills/herdr-ticket-dispatcher`，main 基线 `759d7213a28c5383b107e71f95e16d044a120f93`，开始时干净。最终安装目标为同级 `herdr-plan-manager`，待核验安装关系后切换。
- 用户于 2026-09-13 本轮确认：Pi / OpenCode 均使用 `opencode-go / deepseek-v4.1-flash / max`，总并发上限 3，OpenCode `--auto`；允许隔离副本与验收仓库提交。本项目主工作区纳入成果，最终提交由用户决定。
- 当前 Herdr 环境检查通过；官方 Pi v8 与 OpenCode v10 集成均 current。

## 前置与安排

- 01–07 已集成；08 含 08-R1/R2 已进入 main `759d721`。09 开始时修正滞后 Status，见 E13。
- 为隔离迁移与安装操作，09 先派实施 worker，交付后主脑使用新入口补验并切换实际安装目录。必要组合验证／修复由独立 worker 承担，已充分的历史证据复用。
- `09-implementation` 是 09 内部实施分工，未增加业务目标；共享工单和计划只由主脑更新。

| 工单／分工 | Worker | Run | 基线 | 当前结论 |
| --- | --- | --- | --- | --- |
| 09-implementation：入口、删除旧实现、证据缺口与回归 | `w09-migration` | `ticket09-implementation` | `759d721` | delivered `40e02a5`，squash 纳入主工作区但不提交，ack 已记录，tab 自动关闭 |
| 09-evidence-fix：实测揭示的夹具字段修正与完整证据核验 | `w09-evidence-fix` | `ticket09-implementation` | `40e02a5` | delivered `fdffc70`，纳入主工作区且字节一致，ack 已记录，tab 自动关闭；E14 已解决 |

## 资源位置

- 隔离副本：`/tmp/opencode/hpm09-implementation`
- 管理目录：`/tmp/opencode/hpm09-implementation/.git/herdr-plan-manager`
- 实施 worktree：`/tmp/opencode/hpm09-implementation/.git/herdr-plan-manager/worktrees/w09-migration`
- 分支：`hpm/w09-migration`；初始 Herdr tab/pane：`wJ:t1W` / `wJ:p1W`
- 合同、结果与状态：管理目录 `workers/w09-migration/{contract.md,result.json,state.json}`。

## 验收状态

验收通过。实际安装目录已于本轮迁为 `/home/blingtron/.agents/skills/herdr-plan-manager`，全量生产回归复用 worker 的 **111 passed in 465.08s**（原 144 减 36 个 dispatcher 测试、1 个 Codex 测试，加 4 个入口检查）。首次纳入后 `git diff 40e02a5 -- bin tests prompts docs SKILL.md README.md` 为空；补充修正后对 `fdffc70` 的对应代码／文档／驱动 diff 为空，随后仅由主脑回写最终验收与执行状态。目录迁移后本地入口检查 4 passed，新 OpenCode 进程发现且仅发现 `herdr-plan-manager` 正式入口，旧与 preview 均消失。

实际冒烟根为 `/tmp/opencode/hpm09-migration-lawoi34f`，两种运行时顺序执行、每 run cap=2。Pi 与 OpenCode A/B/C 均完成自动交接、额度拒绝、ack 前后目标不变、A 集成后 B working 时派 C、四项组合结果及成功 tab 释放。Pi cleanup 产品动作成功后的驱动断言错误由独立 `09-evidence-fix` 修正，并对已有清理作只读复核；OpenCode 直接通过修正后的 cleanup（E14）。

本项目 main HEAD 仍是 `759d721`，纳入工作区与安装可用不冒充已提交集成；最终提交由用户决定。

### 已有证据复核（本轮只读，非重新启动实验）

- `python3 .scratch/herdr-plan-manager/evidence/07-plan-management-skill/verify_real.py`：PASS；Pi / OpenCode 各 4 会话，自动交接分别为 38993 / 37078 tokens，A 集成后 B 仍 working 时派 C，四项输出、停止和归档核对通过。
- `python3 .scratch/herdr-plan-manager/evidence/08-integration-repair-and-closeout/verify_evidence.py`：PASS；6 个真实历史会话的配置/cwd、隔离修复、提交与测试归属、ack/集成、冲突中止、目标移动与行为修复证据核对通过，历史并发峰值 2。
- 安装发现：当前 checkout 是 `~/.agents/skills/herdr-ticket-dispatcher` 实体目录，当前 Git 只有主 worktree；`~/.claude/skills` 和 `~/.pi/agent/skills` 均无该项目安装链接。OpenCode 新进程的 `debug skill` 能发现旧入口与嵌套 preview，需迁移后复核只出现正式入口。通过 PTY 获取完整 JSON（直接管道输出在本机被截断）；这是诊断输出捕获方式，不是模型调用。

## 最终真实验收（新安装入口）

使用根 `SKILL.md` 与 `bin/plan_manager.py`，主脑逐项调用 [smoke.py](smoke.py)，每个 worker 只负责一张测试工单。B 的 checkpoint 与 A 的语料是观测夹具，不是产品调度策略。主脑派发顺序为 B→A，额度拒绝 Q 后等待 A，A 被接受并 ack 时主分支仍未前进；主脑合并 A 后派 C，再释放 B 的 checkpoint、收集并串行合并 C/B。

| 验收事实 | Pi | OpenCode |
| --- | --- | --- |
| Run / cap | `issue09-pi` / 2 | `issue09-opencode` / 2 |
| Worker | `w09-pi-a/b/c` | `w09-opencode-a/b/c` |
| 实际配置 | `opencode-go / deepseek-v4.1-flash / max` | 相同；`--auto` 已授权 |
| 自动 handoff | A 1→2，141767 tokens ≥35000 | A 1→2，35085 tokens ≥35000 |
| A 交付／集成 SHA | `fa344c044dc1eaee85fb49590aabfbe004220b20` | `75cca4500b2419eed3aef699e96888f90de78417` |
| C 交付／集成 SHA | `7254cba989096ad01ca870b019d809c3436bd70b` | `31fe6a59a4da690941040105ef88c83f50bd612d` |
| B 交付 SHA | `0ede29109b1943a8e42c01302306c2f543610349` | `10ed5d41d065897ddf5b50e3cdab794933256113` |
| B 正常合并后最终目标 SHA | `e7fc375b5025039a9120cc7a20193cdd0a9c5b01` | `49262979eb0b2a03454e61b5d393652d703c6b05` |
| 四项组合输出 | 逐字节匹配 | 逐字节匹配 |
| 并发／交接 | 第三项启动拒绝，未登记新 worker | 同左；A 的替换会话运行中再次拒绝第三项 |
| 交付终端释放 | 脏 A 保留；干净 B/C 自动关闭 | 同左 |
| stop / cleanup | A 确认停止，默认拒绝脏内容，显式 archive 后清理 | 同左，修正驱动全过程 exit 0 |
| 最终实时查询 | active=0、pending=0，全部登记 tab 不存在 | 同左 |

配置/cwd 证据来自原生 Pi 会话事件与 OpenCode 数据库，共 **8 个会话**；两个 A 均有新会话实际读取 handoff 的工具记录，旧活动完成早于新活动开始。Pi B/C 原始采集漏存单独配置文件，补充验证从登记的原生 session refs 只读提取，写入 `runtime-config-b-recovered.json`、`runtime-config-c-recovered.json`，没有用 status 配置声明替代实测。

本轮主脑同时安排的活跃业务 worker 峰值为 3（OpenCode A/B 与独立夹具修正 worker），在用户授权内；Pi 与 OpenCode 的 A/B/C 两轮本身顺序执行，各自峰值 2。所有正式、修正与冒烟会话共 10 个，目前监督均已退出，登记 tab 均已关闭。

### 可定位证据

- [最终双运行时校验摘要](logs/verify.json)：`python3 smoke.py verify` exit 0。
- [安装发现](logs/installation.json)：新 OpenCode 进程只发现正式入口。
- `logs/real-{pi,opencode}/` 下的 `init`、`start-*`、`quota-*`、`wait*`、`ack-*`、`integration-*`、`b-at-release`、`runtime-config-*`、`tool-activity-*`、`release-facts`、`disk-preserved-*`、`cleanup-a-*`、`goal-closeout`。
- 同目录 `captured/w09-*/`：合同、材料来源清单、状态／结果、handoff、清理记录与归档笔记副本。
- 同目录 `closeout-live-*`、`closeout-tab-*`：最终实时 status 与 Herdr `tab_not_found` 响应，确认没有遗留业务会话或待处理事项。
- [实施 worker 最终状态](logs/w09-migration-closeout.json)、[修正 worker 最终状态](logs/w09-evidence-fix-closeout.json)：delivered、ack、自动关闭与磁盘资源位置。
- [全量回归](logs/verify/full-suite.txt)、[组合回归](logs/verify/workflow.txt)、[38 项夹具验证](logs/verify/validate-smoke.txt)、[Pi 只读回放](logs/verify/replay-real-pi.txt)。

## 保留资源与后续

- 正式入口：`/home/blingtron/.agents/skills/herdr-plan-manager/SKILL.md`；操作工具同目录 `bin/plan_manager.py`。新会话加载正式 skill；当前会话原始技能清单不是热更新的。
- 两轮 A 的 worktree 已明确清理，原始未提交笔记分别在其 worker 管理目录 `cleanup/uncommitted-20260913T030642Z`（Pi）以及 OpenCode `cleanup.json.last_archive.path` 指向的归档中；本证据目录还有耐久副本。所有 A/B/C 分支保留，B/C 的 4 个 worktree 保留。
- 两个实施分支及 worktree 均保留于 `/tmp/opencode/hpm09-implementation/.git/herdr-plan-manager`；初始 clone 的 origin 仍是创建时旧路径，仅作为历史副本保留，后续若需 fetch 应显式使用新路径。
- 旧运行记录与历史 worker 现场未接管、未迁移、未删除。此轮只清理了明确归属的两个 A 测试 worktree。
- 本项目 `main` HEAD=`759d721`，用户要求的最终提交尚未执行；当前安装工作树包含全部已验收成果。此项行政待办不被伪写为 integrated。
- 未解决验收项：无。确定性故障场景与未额外重跑的修复分支边界见[最终验收报告](final-acceptance.md)。
