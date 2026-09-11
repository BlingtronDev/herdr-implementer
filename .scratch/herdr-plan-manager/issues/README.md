# Herdr Plan Manager 工单执行总览

本目录将[项目重建方案](../项目重建方案.md)拆成 9 张初始工单。执行或接手任一工单前，先阅读本页的依赖关系和计划外情况记录，再阅读对应工单的范围与验收条件。

## 工单总览

| 编号 | 工单 | 交付目标 | 直接前置 |
| --- | --- | --- | --- |
| 01 | [验证最小执行链路](01-verify-runtime-chain.md) | 验证 Herdr、Pi／OpenCode 显式配置、投递、中断和 handoff，形成可重复证据 | 无 |
| 02 | [Pi 单工单执行与交付](02-pi-worker-delivery.md) | 跑通隔离启动、后台监督、状态、结果及停止保留 | 01 |
| 03 | [OpenCode 统一执行接口](03-opencode-worker-delivery.md) | 通过相同接口执行 OpenCode 工单，保证显式配置生效 | 02 |
| 04 | [上下文观测与自动交接](04-worker-context-handoff.md) | 双运行时跨会话续接，保持工单、工作目录、分支和配置 | 03 |
| 05 | [并发与待处理事项等待](05-concurrency-and-wait-any.md) | 并发额度、wait-any、持久事项及 ack | 02 |
| 06 | [成果保留与按决定清理](06-retention-and-cleanup.md) | 交付及异常保留现场，协调停止、交接和明确清理 | 04 |
| 07 | [主脑动态管理计划](07-plan-management-skill.md) | 一次授权、动态派发、正常合并和执行记录 | 05、06 |
| 08 | [修复冲突与目标收口](08-integration-repair-and-closeout.md) | 派新 worker 修复冲突及行为问题，由主脑判断整体完成 | 07 |
| 09 | [端到端验收与迁移](09-end-to-end-and-migration.md) | 核验全部验收场景，切换新入口并移除被替代的旧实现 | 08 |

各工单文件的 `Status` 和验收清单记录该工单的实际进展，本页不复制一份独立状态表。`ready-for-agent` 表示工单已具备执行说明，是否可以派发仍取决于前置成果是否可用。

## 依赖关系

```text
01 → 02 → 03 → 04 → 06 ─┐
      └──────→ 05 ──────┴→ 07 → 08 → 09
```

- 从 01 开始；02 完成并集成后，03 和 05 可以并行推进。
- 03 → 04 → 06 构成运行时与交接链路，05 提供并发与等待；两条链路就绪后才能执行 07。
- 05 只依赖 02，可使用 Pi 与模拟会话变更进行验证；真实双运行时交接与等待的组合验证由 07 和 09 完成。
- Worker 的成果交付不等于成果集成。依赖代码成果的工单，应在所需成果进入目标分支后启动；验证工单的材料也须已交付并经主脑确认可用。
- 依赖图表示先后约束，不是固定批次。主脑在授权与并发上限内动态安排可执行工单。
- 新增、拆分或调整工单时，主脑同步更新相关工单的 `Blocked by`、本页总览与依赖图，并在下方留下变更原因。

## 计划外情况如何记录

**执行中发现会影响实现方式、验收、依赖或后续工作的计划外情况，必须记录到本 README 的“计划外情况记录”中。** 不等到工单结束才汇总。

典型情况包括：运行时能力与假设不符、环境条件阻塞验证、已有接口需要改变、发现隐藏依赖、需要新增修复／调查工单、验收要求存在歧义，以及集成后出现计划未覆盖的问题。普通执行日志和已经按预期处理的瞬态错误留在工单或运行记录中。

### 记录与处置职责

1. **发现时记录事实：** 写明关联工单、原先预期、实际现象、证据及影响；尚未确认的原因标为待验证。
2. **由主脑汇总到本页：** 独立 worker 及时将结构化情况及证据位置报告给主脑，由主脑写入共享 README，避免多个 worker 并发覆盖。直接执行工单的主脑应即时记录。尚未汇总时，worker 在交付或交接材料中明确标注待记录事项。
3. **按授权决定下一步：** 主脑自主安排常规修复、调查和工单调整；不可推断的需求取舍或授权变化交由用户决定。记录应指出受影响工单、是否阻塞，以及可以继续的工作。
4. **持续更新同一条记录：** 决定或验证结果出现后补充处置、关联工单和解决证据，保留最初事实。已经解决的记录继续保留，供后续执行理解变更原因。

### 记录模板

每个独立事项使用递增编号 `E01`、`E02` 等；同一问题更新原条目。

```markdown
### E01：<简短标题>

- 发现时间：<实际日期／时间>
- 关联工单：<编号及链接>
- 状态：待调查／待决策／处理中／已解决
- 预期与实际：<原计划假设；实际现象；已确认事实与待验证推测>
- 证据：<日志、命令结果、产物或提交的可定位引用>
- 影响：<影响范围、依赖／验收变化、是否阻塞及哪些工作可继续>
- 处置与负责人：<主脑决定、worker 后续任务，或需要用户决定的问题>
- 后续工单／计划变更：<新增或调整的工单与理由；没有则写无>
- 解决与验证：<解决证据和解除阻塞条件；未解决则写待补充>
```

## 计划外情况记录

### E01：Pi 的阻塞对话框不被 Herdr 标记为 `blocked`

- 发现时间：2026-09-11
- 关联工单：[01 验证最小执行链路](01-verify-runtime-chain.md)
- 状态：待决策（根因已确认，处置方案待定）
- 预期与实际：预期 `blocked` 信号能覆盖所有等待人工输入的 worker UI。实际在 Pi 中调用 `ask_user_question` 后，对话框真实阻塞，但 `herdr agent get` 持续报告 `working`，`herdr agent wait --until blocked` 超时（exit 1）。根因：Herdr 0.8.2 在 Pi 官方集成报活时以生命周期 hook 为唯一权威、跳过屏幕检测；集成仅在收到 `herdr:blocked` 事件时才上报 blocked，而 `rpiv-ask-user-question` 只发出自身命名空间的 `rpiv:ask-user:blocked`，未桥接。对照 `pi-subagents` 显式桥接了 `herdr:blocked`，其等待人工会被正确上报。
- 证据：[`evidence/01-verify-runtime-chain/logs/09-pi-blocked.txt`](../evidence/01-verify-runtime-chain/logs/09-pi-blocked.txt)、[`09-pi-blocked-current.txt`](../evidence/01-verify-runtime-chain/logs/09-pi-blocked-current.txt)、[`evidence/01-verify-runtime-chain/README.md` 附录 A](../evidence/01-verify-runtime-chain/README.md)；`~/.pi/agent/extensions/herdr-agent-state.ts:207`；`@juicesharp/rpiv-ask-user-question/events.ts:33`；Herdr 0.8.2 `agents.mdx`（Status authority / Blocked state）与 `pi-subagents/src/integrations/herdr-status.ts:267-280`。
- 影响：工单 02（单 worker 生命周期监督）与 04（异常上报／自动交接）不能仅靠 `blocked` 识别 Pi 的人工介入信号；OpenCode 权限询问仍可靠映射为 `blocked`。工单 01 的验收结论不受影响（语义已实测并记录）。作为推论，卸载 `rpiv-ask-user-question` 不会改变该行为；只有卸载 Herdr 的 Pi 集成并回退屏幕 manifest 检测，才可能将可见审批／提问 UI 判为 `blocked`，但会丢失精确生命周期与会话身份。
- 处置与负责人：待主脑决策。候选：A. 增加桥接扩展，监听 `rpiv:ask-user:blocked` 并转发 `herdr:blocked`（放在 `herdr-agent-state.ts` 旁）；B. 监督层对 Pi 采用 `working` 长时间无输出 + pane 读取兜底；C. 卸载 Pi 集成回退屏幕检测（不推荐）。
- 后续工单／计划变更：暂无新增工单；建议在工单 02／04 的验收中明确 Pi 阻塞 UI 的识别路径。
- 解决与验证：待补充。
