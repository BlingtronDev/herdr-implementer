# 工单 08：修复冲突与目标收口证据

> 后续更新：用户确认的 [08-R2](../08b-close-delivered-tabs/README.md) 已实现成功交付自动关闭 tab，并关闭本页记录的原 7 个已完成 worker tab，磁盘现场未变。本页的 tab 保留状态及 113 passed 记录对应追加前的实验事实；当前完整测试为 144 passed，终端释放状态见 08-R2 证据。

## 1. 目标、授权与交付范围

- 依据：[重建方案](../../项目重建方案.md)第 3、4.3、9 节和 [工单 08](../../issues/08-integration-repair-and-closeout.md)。
- 项目基线：`main / 6f754e76f597d9de74d42e454d4e9c7872f1b7ca`。07 成果已在 `44abad7` 进入 main，后续英文合同为当前实际验证版本。
- 2026-09-13（本地日期；日志为 UTC 2026-09-12）用户确认：Pi／OpenCode 均为 `opencode-go / deepseek-v4.1-flash / max`，总并发 3，OpenCode `--auto`；可在一次性 Git 仓库隔离 worktree 中提交修复。
- 运行环境：Herdr 0.8.2、Pi 0.85.1、OpenCode 1.18.30；Pi 集成 v8、OpenCode 集成 v10 均为 current。
- 方法交付：[修复与收口流程](../../../../docs/plan-management/repair-and-closeout.md)、[修复材料模板](../../../../docs/plan-management/repair-brief.md)，并更新预览入口、操作参考、执行记录模板和验证步骤。
- 两组真实实验使用现有普通 `init-run / start / wait / ack / stop` 接口。种子和无关文档分支在启动前准备；A、B 原始成果和 R 修复由真实 worker 编写并提交。`smoke.py` 是主脑逐项调用的实验辅助脚本，每个动作独立选择，不进入产品执行器。

## 2. 执行记录与原始工单

实验根目录见 [location.json](location.json)：`/tmp/opencode/hpm08-real-4blmm7a6/`。Pi 和 OpenCode 分别使用 `pi/`、`opencode/` 仓库，目标分支均为 main，Git 策略为可快进时快进、否则普通合并。

每组的完整初始工单为 A、B，保存在 `logs/<kind>/captured/{spec,A,B}.md`；发现组合问题后新增 R，材料为同目录 `R.md`。材料起初仅位于各主 checkout 的 ignored `.scratch/`，由工具快照进入 worker 合同。

| 组 / 工单 | Worker | 交付 SHA | 主脑接受／集成决定 |
| --- | --- | --- | --- |
| Pi A：去除首尾空白 | `w08-pi-a` | `97d7f44` | 单项通过，先集成 main |
| Pi B：问候语 | `w08-pi-b` | `141bade` | 单项通过；合并冲突，中止并转 R；修复集成前保持 pending integration |
| Pi R：兼容两种意图 | `w08-pi-r` | `646f772` | 新 worker 复现冲突、提交修复及三项回归；集成为 `b744d42` |
| OpenCode A：生产者输出 cents | `w08-opencode-a` | `4ab1ac2` | 单项通过，先集成 main |
| OpenCode B：旧 dollars 格式化 | `w08-opencode-b` | `1664e77` | 单项通过，正常合并为 `7e057ac`，组合失败，目标继续执行 |
| OpenCode R：单位兼容 | `w08-opencode-r` | `5f45535` | 新 worker 修复 consumer 并提交三项回归；快进集成为同一 SHA |

全量 SHA、原始结果、基线、集成映射分别见各组 `integration.json`、`captured/<worker>/result.json` 和 `final-log.json`。它们是**实验仓库**的集成记录，不表示工单 08 的项目改动已经提交或集成。

### Pi：文本冲突、可控中止、修复期间目标变化

- [merge-B.json](logs/pi/merge-B.json)：真实 `git merge --no-edit 141bade...` 返回 1，`formatter.py` 冲突。
- [conflict-index.json](logs/pi/conflict-index.json)、[conflict-paths.json](logs/pi/conflict-paths.json)、[conflict-status.json](logs/pi/conflict-status.json)：三阶段 blob 和冲突路径；`owned-merge-head.json`／`owned-target-head.json` 核实发起身份与输入。
- [before-B.json](logs/pi/before-B.json) 与 [after-abort.json](logs/pi/after-abort.json) 完全一致：main HEAD 未变，干净，无进行中操作；[abort.json](logs/pi/abort.json) 返回 0。主脑没有编辑冲突文件。
- [repair-inputs.json](logs/pi/repair-inputs.json)：新增 R 的原因、明确 base、双方 SHA、目标 `executing` 及依赖保持阻塞。
- [status-R.json](logs/pi/status-R.json) 记录 R 仍 running；随后正常合并预置文档分支，目标由 `97d7f44` 变为 `8d8dd7e`。原始 A／B／R 都不包含该文档变更。
- R 交付后主脑比较基线差异，仅 `NOTICE.md`。这是不涉及 Python 行为的文档新增，故复用 R 验证，正常合并为 `b744d42`。见 [integration.json](logs/pi/integration.json)、[target-delta.json](logs/pi/target-delta.json)。
- [R 交付](logs/pi/captured/w08-pi-r/result.json) 与 [运行时工具调用](logs/pi/captured/tool-calls.json) 证明：R 在自己的 worktree 再次运行合并、得到冲突、写入 `name.strip()` 与 greeting 的组合实现，提交 merge 和 `test_formatter.py`，运行三项测试与组合命令。最终 [combined-success.json](logs/pi/combined-success.json) 返回 0。

### OpenCode：所有初始工单已交付和集成，整体仍失败

- A／B 修改不同文件；[merge-B.json](logs/opencode/merge-B.json) 返回 0。
- [combined-failure.json](logs/opencode/combined-failure.json) 返回 1，实测 `AssertionError: $1200.00`，而计划要求 `$12.00`。主脑没有报告整体成功。
- [repair-inputs.json](logs/opencode/repair-inputs.json) 记录目标继续 `executing`，R 从已集成但有错误的 `7e057ac` 开始。
- [R 交付](logs/opencode/captured/w08-opencode-r/result.json)、[工具调用](logs/opencode/captured/tool-calls.json) 证明：新 worker 先复现失败，再修改 consumer 依据 cents 元数据换算，保持 producer 和 legacy dollars 行为，提交 `test_currency.py` 三项回归并验证。
- R 交付时目标未变，主脑复用其组合验证证据并快进合并。最终 [combined-success.json](logs/opencode/combined-success.json) 返回 0，[integration.json](logs/opencode/integration.json) 才记录实验目标 complete。

## 3. 保留现场与边界验证

两组共 6 个真实会话，运行时原生配置证据为 `logs/{pi,opencode}/captured/runtime-config.json`：实际 provider/model/thinking/cwd 一致；`verify_evidence.py` 按会话起止时间计算并发峰值 **2**，未超过授权 3。每次新 R 都在对应 A／B 结束后启动，分支与 worktree 各自独立。

所有 A／B／R 均显式 stop，并确认 `business_stopped: true`；各组 `retained-run.json` 为 active_count 0。保留全部 worktree、分支、登记 tab、合同、结果与监督记录；没有触发 cleanup。

- Worktree：`<实验仓库>/.git/herdr-plan-manager/worktrees/<worker>/`
- 结果与状态：`<实验仓库>/.git/herdr-plan-manager/workers/<worker>/`
- 仓库内耐久快照：`logs/<kind>/captured/<worker>/`

[abort_boundaries.py](abort_boundaries.py) 使用真实 Git 与受控 `index.lock` 故障注入，记录在 [abort-boundaries.json](logs/abort-boundaries.json)：

1. 无法区分的用户笔记：保持内容与 HEAD，推迟合并；后续明确它是夹具所有的笔记并提交保存。
2. 缺少发起记录的观察者：保留现有冲突，不执行 abort。
3. 已确认前置条件的发起者遇到注入锁：`git merge --abort` 返回 128；业务文件、index、MERGE_HEAD 的哈希均未变，保存现场并报告，无 reset 或 clean。

该故障现场保留在 `/tmp/opencode/hpm08-abort-iptd7add`，包含有意注入的锁；它不是产品故障或待清理的真实用户仓库。

**证据边界：**文本修复、行为修复、文档型目标移动为真实 worker 实验；中止失败等为真实 Git 故障注入与主脑流程演练；“目标行为发生相关变化 → 再派兼容性 worker”在本轮为文档流程审查，未增加第三个真实目标变化实验。必要组合验证由 R 实际完成，因此复用证据，没有制造固定最终验证角色。未声称自动排程、主脑恢复或任何运行时都能保证模型遵循提示。

## 4. 检查与计划外发现

- [verify_evidence.py](verify_evidence.py)：离线核对六会话配置/cwd、源码编辑和提交调用的归属线索、交付/集成 SHA 映射、ack 与集成顺序、旧/新目标 SHA、停止保留、两类失败到成功和中止失败场景。脚本做结构与证据一致性核对，不代替质量判断；实际修复与验证结论由主脑对照 worker 结果和原始输出检查。各组 `captured/repair-tool-results.json` 保存实际工具输出（含 3 tests / OK），避免把带 `echo` 的 shell 最终成功码误当作内层测试结果。
- 文档本地引用存在性与实验脚本 Python 语法通过，见 `logs/checks/docs-syntax.json`。
- 第一次完整测试被调用方 240 秒窗口中断，未计为失败或成功；第二次取得完整结果：112 passed / 1 failed，301.91 秒。见 `logs/checks/interrupted.json`、`test-all.json`。
- 失败定位到已有陈旧样本测试取值 None；新增 [08-R1](../../issues/08a-stabilize-stale-context-test.md)，追踪 [E11](../../issues/README.md#e1108-全量检查暴露已有上下文测试的采样时序问题)，现已解决。第 7 个真实 Pi 会话 `w08-stale-test-fix` 在一次性项目副本完成诊断与测试修复，提交 `93c02f7`，只有 `tests/test_plan_manager.py` 改动。快速交付会跳过首次采样，改用持续执行的 slow worker 并等待实际 stale 和后续采样，再检查没有交接；故意移除 stale 保护的变异副本会被新测试捕获。
- [08-R1 交付](logs/test-repair/captured/result.json)：修复后单测 5/5、上下文相关 7 passed、模块 70 passed、完整测试 **113 passed in 310.20s**。实际原始测试输出见 [tool-results.json](logs/test-repair/captured/tool-results.json)。
- 主脑将 worker 最小补丁原样纳入当前工作区；[复用核对](logs/checks/worker-suite-reuse.json) 证明测试文件与 worker 已验证版本逐字节相同、其他 bin/tests/prompts 未改变；[本地专项](logs/checks/test-fixed-local.json) 为 **1 passed in 4.80s**。因此复用已充分的完整验证结果，没有再次运行同一全套测试。
- 补充 worker 原生配置/cwd 保持相同已确认值，证据在 `logs/test-repair/captured/runtime-config.json`；它在前六会话全部停止后启动，总体仍峰值 2。已明确 stop，保留 `/tmp/opencode/hpm08-test-repair/.git/herdr-plan-manager/{workers,worktrees}/w08-stale-test-fix/` 与 `hpm/w08-stale-test-fix` 分支；没有清理其诊断／变异副本。
- 最后证据一致性验证见 `logs/checks/verify-evidence-3.json`，本地文档链接、实验脚本语法与 [diff 检查](logs/checks/diff-check.json) 通过。

**08 交付结论：**全部验收项已具备方法与相应证据，E11 已处理，无未解决的 08 验收问题。项目成果在当前工作区，尚未提交／未集成；09 仍需以实际项目集成为前置。实验故障锁、失败的历史输出与已停止资源作为可追溯证据保留。

### 双轴审查

**Standards：**首次发现两处文档触发／分支问题：合并前证据的指针过晚；通用修复步骤无条件要求复现文本冲突。现已在入口合并前加载捕获要求，并将文本合并限定到对应分支；复查均已解决。补充测试修复复查无具体缺陷，无剩余标准问题。

**Spec：**修复方法与真实归属符合目标。复查建议加强自动校验的证据表述及原始输出留存；现已增加 repair tool results、源码路径和交付／集成映射核对，并明确结构检查的边界。最后复查确认原始 worker 输出支持 113 passed、补丁一致性支持全量证据复用，无剩余实质性问题。工单状态与清单已按实际结果回写。

## 5. 重复方法

先按 [真实修复验证步骤](../../../../docs/plan-management/validation.md#run-repair-and-closeout-experiments) 确认当次运行授权。保留本目录的历史产物；新实验应复制辅助脚本到新的证据目录，或在独立仓库 checkout 使用独立证据位置，避免覆盖 `location.json`。

`smoke.py setup` 创建新夹具；按组执行 `init`、`start-A`、`start-B`，用 `wait` 观察并阅读交付，然后执行 `inputs`、`start-R`；Pi 立即执行 `move-target`，确认移动发生在 R 活跃期间。等待并审阅 R 后执行 `integrate`、`retain`、`capture`。每一步由主脑决定何时执行；辅助脚本断言未满足应停下调查，不能为了让脚本通过而改变真实结果。

`abort_boundaries.py` 独立运行一次保留注入失败现场。`verify_evidence.py` 可直接离线重复。`smoke.py checks` 另运行项目全量测试，调用方需提供足够的等待窗口（历史约 5 分钟）；该窗口不是工单执行期限。
