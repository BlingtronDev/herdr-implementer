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
- 状态：已解决（2026-09-11 用户决定卸载触发该阻塞态的 `@juicesharp/rpiv-ask-user-question` 插件）
- 预期与实际：预期 `blocked` 信号能覆盖所有等待人工输入的 worker UI。实际在 Pi 中调用 `ask_user_question` 后，对话框真实阻塞，但 `herdr agent get` 持续报告 `working`，`herdr agent wait --until blocked` 超时（exit 1）。根因：Herdr 0.8.2 在 Pi 官方集成报活时以生命周期 hook 为唯一权威、跳过屏幕检测；集成仅在收到 `herdr:blocked` 事件时才上报 blocked，而 `rpiv-ask-user-question` 只发出自身命名空间的 `rpiv:ask-user:blocked`，未桥接。对照 `pi-subagents` 显式桥接了 `herdr:blocked`，其等待人工会被正确上报。
- 证据：[`evidence/01-verify-runtime-chain/logs/09-pi-blocked.txt`](../evidence/01-verify-runtime-chain/logs/09-pi-blocked.txt)、[`09-pi-blocked-current.txt`](../evidence/01-verify-runtime-chain/logs/09-pi-blocked-current.txt)、[`evidence/01-verify-runtime-chain/README.md` 附录 A](../evidence/01-verify-runtime-chain/README.md)；`~/.pi/agent/extensions/herdr-agent-state.ts:207`；`@juicesharp/rpiv-ask-user-question/events.ts:33`；Herdr 0.8.2 `agents.mdx`（Status authority / Blocked state）与 `pi-subagents/src/integrations/herdr-status.ts:267-280`。卸载证据：`pi remove npm:@juicesharp/rpiv-ask-user-question` 输出 `removed 2 packages`；`pi list` 与 `~/.pi/agent/settings.json` 的 `packages` 已不含该插件，`~/.pi/agent/npm/node_modules/@juicesharp/` 目录已移除（2026-09-11）。
- 影响：插件卸载后，Pi 会话不再注册 `ask_user_question`，这一具体阻塞场景不会再出现；工单 02／04 已落地的合同约束与主脑 `read` 抽查保留为通用兜底。机制层面的事实仍然成立：Herdr 的 Pi 集成只认 `herdr:blocked` 事件，其他阻塞 UI 或停滞仍会显示为 `working`；用户判断这类情况发生概率极低，本次按“卸载即解决”处理。OpenCode 的权限询问仍可靠映射为 `blocked`；工单 01 的验收结论不受影响（语义已实测并记录）。
- 处置与负责人：用户于 2026-09-11 决定并执行卸载：`pi remove npm:@juicesharp/rpiv-ask-user-question`（移除 2 个包）。候选 A（桥接扩展）因事件源消失不再需要；候选 B 的“`working` 长时间无输出 + pane 读取”自动兜底不实现；候选 C 不采用。工单 02 的合同约束与主脑 `read` 抽查保留为通用兜底。
- 后续工单／计划变更：无新增工单。05／06／07 不再需要针对本插件设计验收或文档；残余限制（Pi 侧其他阻塞 UI／停滞不会自动上报）如仍提及，仅作为已知限制说明，不作风险项。
- 解决与验证：已验证卸载生效：`pi remove` 输出 `removed 2 packages`，`pi list` 与 `~/.pi/agent/settings.json` 的 `packages` 均不再包含该插件，`~/.pi/agent/npm/node_modules/@juicesharp/` 目录消失。此后新启动的 Pi 会话不再注册该工具，无法进入该阻塞态；卸载前已启动的进程（包括执行卸载的当前会话）仍在进程内加载着旧工具，需重开会话才生效。

### E02：OpenCode 在就绪握手后丢弃首条投递，监督层的“结果补报”被当成主投递路径

- 发现时间：2026-09-11（同日修复并验证）
- 关联工单：[03 统一执行接口](03-opencode-worker-delivery.md)；与 [01](01-verify-runtime-chain.md) 的未验证假设“`agent start` 返回后立即 prompt 的竞态”及 02 的一次结果补报机制直接相关
- 状态：已解决
- 预期与实际：预期 `herdr agent start` 返回、`agent get` 报 `idle`／`interactive_ready=true` 后即可投递。实际 OpenCode TUI 在该窗口内仍会丢弃键入：`herdr agent prompt` 返回 `agent_prompted`（rc=0），16 秒后 pane 输入框仍是占位符，会话中不存在该 user message（两次真实启动均复现）。旧行为下监督层 30 秒 settle 宽限期的“结果补报”提示成为会话第一条消息，worker 实际靠补报（而非合同提示）开始工作；首次冒烟的成功依赖模型自行找到并读取合同，属偶然而非保证。
- 证据：[`race-discovery/race-composer-unsubmitted.txt`](../evidence/03-opencode-worker-delivery/logs/race-discovery/race-composer-unsubmitted.txt)、[`race-discovery/prompt-race-observation.txt`](../evidence/03-opencode-worker-delivery/logs/race-discovery/prompt-race-observation.txt)、[`race-discovery/oc-smoke1-user-parts.txt`](../evidence/03-opencode-worker-delivery/logs/race-discovery/oc-smoke1-user-parts.txt)、[`race-discovery/supervisor-delivered.log`](../evidence/03-opencode-worker-delivery/logs/race-discovery/supervisor-delivered.log)；工单 03 证据 README 第 6 节
- 影响：未修复时 OpenCode 的首个业务投递不可靠；更关键的是“有限补报”这一安全网被误用为主投递路径，冒烟一度给出虚假成功。Pi 未观测到该现象，但机制上同样存在竞态。
- 处置与负责人：工单 03 已实现 `deliver_prompt_confirmed`：投递后观察运行时是否离开 settled，窗口内仍 settled 则判定键入被丢弃，最多重投 3 次（`HPM_PROMPT_CONFIRM_SECONDS`／`HPM_PROMPT_ATTEMPTS`），只在明确 settled 时重投。工单 04 的续接投递复用了同一路径。
- 后续工单／计划变更：无新增工单。建议 09 端到端验收保留“新会话确实读取交接文档”的证据要求。
- 解决与验证：修复后真实启动 `prompt_attempts=2`、会话首条消息为合同提示、监督层不再补报交付；确定性测试 `test_dropped_prompt_is_redelivered_until_confirmed` 用 `drop_prompts` 场景验证恰好一次重投；04 的 Pi／OpenCode 自动交接冒烟均走同一确认路径。

### E03：OpenCode 对项目外路径的 `external_directory` 权限依赖管理目录位置

- 发现时间：2026-09-11
- 关联工单：[03 统一执行接口](03-opencode-worker-delivery.md)；影响 [04](04-worker-context-handoff.md)（交接文档耐久位置）、[06](06-retention-and-cleanup.md)（清理与资源位置）及实际部署
- 状态：已解决（2026-09-11 决定采用 `--auto`；2026-09-12 真实冒烟通过）
- 预期与实际：预期 worker 可直接读取 worktree 之外的管理目录（合同、材料快照、结果）。实际 OpenCode 将 worktree 视为项目根，管理目录属于项目外路径：在 `/tmp/hpm-exp03-blocked/` 下读取合同时弹出 `Permission required`，Herdr 上报 `blocked`，形成待处理异常。首次记录曾推断成功冒烟依赖用户全局 `external_directory /tmp/opencode/* → allow` 规则；2026-09-11 复核发现 `~/.config/opencode/opencode.json`、`opencode.jsonc` 中并无任何 permission／放行规则，该推断缺少可复现证据。
- 证据：[`opencode-blocked/pane-blocked.txt`](../evidence/03-opencode-worker-delivery/logs/opencode-blocked/pane-blocked.txt)、[`opencode-blocked/status-blocked.json`](../evidence/03-opencode-worker-delivery/logs/opencode-blocked/status-blocked.json)；成功冒烟使用的路径见 [`opencode-delivery/start.json`](../evidence/03-opencode-worker-delivery/logs/opencode-delivery/start.json)；工单 03 证据 README 第 6 节
- 影响：默认管理目录位于目标仓库 Git common dir 下，OpenCode 视其为项目外；部署时若用户授权未覆盖该路径，每个工单都会产生一次人工放行（现由 `--auto` 消除）。工具不修改任何权限配置。03／04 的真实冒烟都在 `/tmp/opencode/` 下执行，因此没有暴露该成本。
- 处置与负责人：用户于 2026-09-11 决定采用 `--auto`：OpenCode worker 启动时附加 `--auto`（帮助文本：“auto-approve permissions that are not explicitly denied”），由 `OpenCodeAdapter.start_args` 统一注入，交接后重建的会话复用同一路径。它不写任何配置文件、不覆盖显式 `deny`（仓库 `permission: {webfetch: deny}` 仍然生效），但会消除所有未显式拒绝的权限询问，使 OpenCode worker 与 Pi worker 一样无人值守；代价是失去“权限询问 → `blocked`”这一人工介入信号，后续如需约束应写进显式 deny。原候选 A／B／C 不再采用。
- 后续工单／计划变更：无新增工单。05／06／07 按“OpenCode 权限询问不再出现”的前提设计；06 的管理目录位置不再受该权限限制。
- 解决与验证：2026-09-12 真实冒烟通过（证据：[03 证据 README 第 12 节](../evidence/03-opencode-worker-delivery/README.md)，`logs/e03-auto-smoke/`）。对照组（无 `--auto`）在同一环境读取管理目录时 `blocked` 并弹出 `Permission required`；修复组（带 `--auto`）worker 进程 argv 为 `opencode --auto`（Herdr 确实透传），约 50 秒交付，监督日志与 pane 中无任何权限询问，显式配置（`opencode-go / deepseek-v4.1-flash / max`）保持生效。对照组也证实了原记录中“全局 allow 规则”的推断无法复现；该历史疑问不再阻塞本条，已在证据 README 12.2 记录。

### E04：Pi 会话模型注册表不提供 context window，Pi 上下文观测不可用

- 发现时间：2026-09-11（同日修复并验证）
- 关联工单：[04 上下文观测与自动交接](04-worker-context-handoff.md)
- 状态：已解决
- 预期与实际：预期 `bin/pi_context.mjs` 能从 Pi 模型注册表读出 `contextWindow`。实际对 `opencode-go/deepseek-v4.1-flash` 返回 `window: null`，`get_context.py` 报 “Pi model registry has no context window”，Pi 观测全部为 `unobservable`；首次 Pi 冒烟因此无法按阈值自动交接，靠监督层一次结果补报才完成阶段二并交付。同一模型在 `pi --list-models` 的 `context` 列为 `1M`，说明目录数据存在、仅会话内注册表缺失。
- 证据：[`pi-attempt1-unobservable/status-unobservable.json`](../evidence/04-worker-context-handoff/logs/pi-attempt1-unobservable/status-unobservable.json)、[`pi-attempt1-unobservable/supervisor.log`](../evidence/04-worker-context-handoff/logs/pi-attempt1-unobservable/supervisor.log)；工单 04 证据 README 第 5.5、6 节
- 影响：修复前 Pi 无法自动交接；OpenCode 不受影响。该缺陷与 E02 叠加后，“结果补报”又一次成为掩盖观测失效的路径。
- 处置与负责人：工单 04 在 `bin/get_context.py` 增加回退：模型注册表没有窗口时查询 `pi --list-models`（与启动校验同一来源），输出增加 `window_source` 以区分窗口来源；`--window` 显式覆盖优先并标记 `explicit --window`。
- 后续工单／计划变更：无新增工单。建议 09 在“非默认 provider”场景保留该回退的验证。
- 解决与验证：修复后真实冒烟窗口 `1048576`、`pct=2.26%`，自动交接恢复；`tests/test_context.py::PiWindowTests` 覆盖回退与尺寸解析；04 的 Pi 自动阈值冒烟完成 7 次交接后交付。

### E05：阈值低于新会话起始上下文时会连续自动交接（观察项，非缺陷）

- 发现时间：2026-09-11
- 关联工单：[04 上下文观测与自动交接](04-worker-context-handoff.md)；影响 [07](07-plan-management-skill.md) 的阈值使用建议
- 状态：已解决（按用户判断关闭，不作变更）
- 预期与实际：把 `--handoff-tokens` 设为 1（仅用于演示）时，新会话读完合同与交接文档后的首个回合即超过阈值，于是每完成一个回合就再次交接：Pi 演示连续交接 7 次、OpenCode 连续 2 次，最终均交付。默认 300K／80% 阈值下不会出现（新会话起始约 13K–21K tokens）。
- 证据：[`pi/state.json`](../evidence/04-worker-context-handoff/logs/pi/state.json)、[`oc/state.json`](../evidence/04-worker-context-handoff/logs/oc/state.json)；工单 04 证据 README 第 6 节
- 影响：仅影响明显低于新会话种子上下文的阈值配置；不违反“连续多次交接仍属于同一工单执行”，也不造成并写。风险是主脑误设极低阈值时工单被反复交接、进展缓慢。
- 处置与负责人：用户于 2026-09-11 判定该现象没有实际意义：阈值 1 只是为演示制造的极端配置，正常使用不会出现。决定不处理——不加工具层抑制，也不作为风险跟踪；如需，07 的操作文档可用一句话提示阈值应高于新会话种子上下文，不单独立项。
- 后续工单／计划变更：无新增工单。
- 解决与验证：按“不作变更”关闭。默认 300K／80% 与任何常规配置不受影响；原始观测与证据保留在工单 04 与证据 README。

### E06：E02–E05 未按本页规则及时汇总，工单状态未随集成回写

- 发现时间：2026-09-11（补记时确认）
- 关联工单：01–04 全部
- 状态：已解决
- 预期与实际：本页规定“发现即记录、不等工单结束才汇总”，且由主脑统一写入以避免多 worker 并发覆盖。实际只有 E01 进入本页：工单 03、04 的证据 README 都明确标注“E02／E03 待主脑汇总”“仍未见汇总”，但三个交付提交都没有回写本页；E01 的状态也未随 02／04 的事实处置更新。同时四张工单的 `Status` 仍写“delivered（待主脑确认集成）”，而其提交已进入 `main`。
- 证据：`git log -- .scratch/herdr-plan-manager/issues/README.md` 最后改动为 `6ef992a`（仅 E01）；工单 03／04 证据 README 第 6 节；`git log --oneline` 中 02–04 的提交；工作区干净且 `python3 -m pytest tests/ -q` 为 85 passed。
- 影响：后续工单（05–09）无法只读本页获得完整变更历史；E01 的“待决策”与事实处置不符，可能误导接手者。
- 处置与负责人：主脑于 2026-09-11 补记 E02–E05、更新 E01，并把 01–04 工单 `Status` 回写为 integrated（附各自提交号）。
- 后续工单／计划变更：无新增工单。
- 解决与验证：四张工单的 `Status` 已改为“已集成：main 提交 `<hash>`”；本页记录完整，条目关闭。

### E07：工单 05 改变了 start 接口与事项存储布局，07／09 需以新接口为准

- 发现时间：2026-09-12
- 关联工单：[05 并发与待处理事项等待](05-concurrency-and-wait-any.md)；影响 [07](07-plan-management-skill.md)、[09](09-end-to-end-and-migration.md)
- 状态：已解决（随工单 05 集成，已同步 07／09）
- 预期与实际：预期 `start` 延续 02–04 的单工单接口，直接传 `--kind/--provider/--model/--thinking`，并发上限由使用侧约束。实际为实现“当前执行关联明确 max_workers”，工单 05 引入 `init-run` 登记一次执行（确认运行配置与 `max_workers`，写入 `runs/<run-id>/run.json`）；`start` 必须带 `--run`，运行配置校验前移到 `init-run`，`start` 只校验传入值与已确认值一致，不静默替换；事项标识为 `<worker-id>/<item-id>`，`ack` 只写 `workers/<id>/ack.json`。属于本页所列“已有接口需要改变”的计划外情况。
- 证据：[05 证据 README 第 3、6 节](../evidence/05-concurrency-and-wait-any/README.md)、提交 `0ee66d7`
- 影响：工单 07 的操作文档需先 `init-run`，再用 `start --run` 派发、用 `wait`／`ack` 收割事项；工单 09 的端到端验收以新接口为入口。02–04 的合同、结果协议与交接语义不变；工单 06 不受影响（cleanup 依据 worker 登记，与 run 接口正交）。
- 处置与负责人：主脑于 2026-09-12 确认集成并同步后续工单：工单 07 增加“接口现状”说明（含 `wait` 默认无限等待、`ack` 事项标识与 300K 阈值建议），工单 09 增加新入口提示。
- 后续工单／计划变更：无新增工单；依赖图不变（07 仍等 06）。
- 解决与验证：`git merge-base --is-ancestor 0ee66d7 main` 成立，工作区干净，`python3 -m pytest tests/ -q` 为 100 passed（原始输出：[05 证据目录](../evidence/05-concurrency-and-wait-any/logs/deterministic/test-all.txt)）；工单 05 `Status` 已回写为 integrated。

### E08：工单 06 新增 cleanup 接口并改变 stop／交付语义，07／09 需以新接口为准

- 发现时间：2026-09-12
- 关联工单：[06 成果保留与按决定清理](06-retention-and-cleanup.md)；影响 [07](07-plan-management-skill.md)、[09](09-end-to-end-and-migration.md)
- 状态：已解决（随工单 06 集成，已同步 07／09）
- 预期与实际：预期 `stop` 只中断当前会话、清理尚无工具入口。实际工单 06 引入 `cleanup --worker <id> (--integrated <sha> | --disposition <text>) [--archive-uncommitted | --discard-uncommitted] [--delete-branch [--force-branch]]`；`stop` 确认全部登记会话的业务写入停止并覆盖交接竞争，无法确认时形成 `stop-incomplete` 异常；worker 写出结果文件后停止自动交接且 `handoff` 被拒绝；新增 `workers/<id>/cleanup.json` 与 `workers/<id>/cleanup/` 归档，`status --worker` 增加 `cleanup` 字段。属于本页所列“已有接口需要改变”的计划外情况。
- 证据：[06 证据 README 第 3、5 节](../evidence/06-retention-and-cleanup/README.md)
- 影响：工单 07 的操作方法需要写明 cleanup 的明确决定参数（integration SHA／disposition）、默认保留分支与 `--delete-branch`／`--force-branch` 的区别、未提交内容的归档／丢弃选择，以及交付后不再自动交接；工单 09 的端到端验收需覆盖 stop→cleanup、未提交归档、只关闭登记 tab 与资源保留。
- 处置与负责人：主脑于 2026-09-12 确认集成并同步后续工单：工单 07 增加“接口现状”说明（cleanup 决定参数、分支政策、归档／丢弃选择、交付后无自动交接），工单 09 增加新入口提示。
- 后续工单／计划变更：无新增工单；依赖图不变（07 仍等 05、06）。
- 解决与验证：`git merge-base --is-ancestor 3a60d48 main` 成立，工作区干净；`python3 -m pytest tests/ -q` 为 111 passed（原始输出：[06 证据目录](../evidence/06-retention-and-cleanup/logs/deterministic/test-all.txt)）；工单 06 `Status` 已回写为 integrated。

### E09：工单 07 的真实组合冒烟待本轮运行配置授权

- 发现时间：2026-09-12
- 关联工单：[07 主脑动态管理计划](07-plan-management-skill.md)
- 状态：已解决（2026-09-12 用户补充授权，双运行时真实组合冒烟通过）
- 预期与实际：07 要求双运行时交接、多 worker 等待和动态集成的首次组合验证。本轮用户最初授权开始实施第 7 张工单，但没有指定 Pi／OpenCode 各自的 provider、model、thinking 和并发上限；历史冒烟配置不视为本轮启动授权。主脑先询问并完成文档／确定性验证，在收到下述用户授权后才启动真实 worker。
- 证据：[07 证据索引](../evidence/07-plan-management-skill/README.md)；[真实冒烟步骤](../../../docs/plan-management/validation.md)。
- 影响：初期只阻塞 07 真实组合验收，文档与确定性验证可继续。现全部验收已通过，07 标记 delivered；本项目交付尚未提交／集成，08 仍须在所需成果实际集成后启动，不能用冒烟仓库的集成 SHA 代替。
- 处置与负责人：主脑先完成不需真实 provider 的实现与测试。用户补充两套显式配置、总并发 3 和 OpenCode `--auto` 授权后，主脑顺序执行 Pi、OpenCode 的真实 A／B／C 冒烟，并完成正常集成、状态回写与证据留存。
- 后续工单／计划变更：无新增工单，依赖图不变。预览文件位于 `docs/plan-management/`，正式入口和旧实现迁移仍留给 09。
- 解决与验证：确定性全量测试 113 passed。2026-09-12 用户明确授权两种运行时均为 `opencode-go / deepseek-v4.1-flash / max`（目录核验正式 provider 标识为 `opencode-go`），总并发上限 3，OpenCode 使用 `--auto`。真实两轮均在 A 自动交接一次并集成后、B 仍 working 时启动 C，最终正常合并三项成果、组合检查通过；实际 8 个会话配置／cwd 一致，总活跃峰值 2。全部 worker 已停止，A 未提交笔记归档后清理、分支及 B／C 现场保留。见 [07 真实证据](../evidence/07-plan-management-skill/README.md#4-真实组合验收2026-09-12)、[验证输出](../evidence/07-plan-management-skill/logs/verify-real.txt)。
