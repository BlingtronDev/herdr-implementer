# 工单 07：主脑动态管理计划（交付证据）

日期：2026-09-12。实施基线：`b1eae56`，已确认前置 `0ee66d7`（05）、`3a60d48`（06）为当前 HEAD 祖先。

## 1. 交付与状态

- [预览 skill](../../../../docs/plan-management/SKILL.md)：一次授权、完整初始材料、语义依赖、动态派发、正常合并、默认利用验收且保留复核权、主脑记录和目标收口边界。
- [操作参考](../../../../docs/plan-management/operations.md)：当前 CLI 的 init-run、start、status、wait、ack、read、handoff、stop、cleanup；材料快照、OpenCode `--auto`、run 级并发边界、交付与集成区别。
- [执行记录模板](../../../../docs/plan-management/execution-record.md)：输入／授权／run 引用、完整工单索引、交付→集成 SHA、计划调整、验证、资源位置。
- [验收步骤](../../../../docs/plan-management/validation.md)：预览加载、确定性测试与需授权的真实双运行时组合冒烟。
- 根 `SKILL.md`、旧 dispatcher 和旧合同未修改；新工具已有的 `prompts/plan-worker.md` 作为 worker 合同唯一来源直接引用。未增加工具级 DAG、后台领单、固定批次或业务状态机。

**当前结论：文档、确定性验证与真实双运行时组合验收均完成。** 用户补充显式配置、总并发 3 和 OpenCode `--auto` 授权后，实际运行了两套 A／B／C 场景并完成集成、检查、停止和归档。07 交付位于当前工作区，未提交／未标记集成；冒烟仓库内部的集成不等于本项目工单集成。授权与解决经过见 [E09](../../issues/README.md#e09工单-07-的真实组合冒烟待本轮运行配置授权)，逐项记录见[主脑执行记录](execution.md)。

## 2. 验证

- `python3 -m pytest tests/test_plan_management_workflow.py -v`：2 passed（Pi、OpenCode 分别执行）。[原始输出](logs/test-workflow.txt)。
- `python3 -m pytest tests/ -q`：**113 passed in 284.07s**。[原始输出](logs/test-all.txt)。
- 新文档本地链接与 CLI 选项核对：[检查输出](logs/docs-check.txt)。
- `python3 .scratch/herdr-plan-manager/evidence/07-plan-management-skill/verify_real.py`：真实证据核对通过，两种运行时共 8 个会话。[原始输出](logs/verify-real.txt)。脚本检查本轮保存的真实证据与留存归档，不代替启动实验。

[组合测试](../../../../tests/test_plan_management_workflow.py)通过真正的 `plan_manager.py` CLI、后台监督和临时 Git worktree 执行；仅 Herdr、上下文读取、worker 行为与模型目录为确定性模拟。两种运行时各使用单 run、max_workers=2：

1. B 持续 working；A 由上下文阈值触发自动交接。交接期间仍只有两个活跃 worker。
2. A 的会话序号 1→2，worktree／branch／配置不变，旧会话结束状态为 replaced，新会话提交从 handoff 文档读取的 marker。
3. wait 先返回 A；ack 后目标 SHA 仍是初始 SHA、C 尚未派发。A 的交付未被误当集成。
4. 测试扮演主脑执行正常 ff-only 合并（仅此临时仓库策略），记录集成 SHA，以此作为 C 的 base。C 的 worktree 已含 A 成果，启动时 B 仍 working、活跃数为 2。
5. C 交付并集成；A 留有未提交 partial.txt，默认 cleanup 拒绝，明确归档后内容仍可读、分支与结果保留。
6. B 在演示收尾被明确 stop，保留未交付现场；不伪称 B 完成或整体目标完成。此确定性场景证明动态推进，不替代真实三工单完整收口。

首次运行的两例测试因模拟上下文读取共用全局计数器失败：B 已消耗“首个高用量样本”，A 未触发交接。这是新增多 worker 测试的夹具隔离问题，不是产品缺陷。`tests/fakes/bin/get_context.py` 新增可选计数器文件名；测试为每个 worker 设置独立计数器，同 worker 跨会话仍共享，既隔离 B 又避免 A 续接后重复尖峰。修改后两例及全量回归通过，未改生产代码。

## 3. 主脑方法核对

| 验收 | 证据／限制 |
| --- | --- |
| 完整初始输入、默认／显式路径、语义依赖 | skill §1；操作参考“派发” |
| 一次授权、显式配置与并发 | skill §1；操作参考“准备与登记” |
| 自主调整与失败处置 | skill §2；执行记录“决定与计划外情况” |
| 工具组合、交接无需主脑确认 | 操作参考；模拟与真实双运行时组合均通过，见下节 |
| 交付／ack／集成区别 | skill §2–3；测试在 ack 后检查目标 SHA 不变 |
| 正常合并与复核、具体修复派新 worker | skill §3；完整修复场景仍由 08 补齐 |
| A／B 并行，A 集成后 C 先于 B 完成启动 | 两例模拟测试与两轮真实演示均通过 |
| 独立合同与按需参考 | 新入口链接实际 worker 合同和三份按需参考 |
| 无主脑恢复／固定批次／后台领单依赖 | 方法边界与现有工具职责未扩张 |

## 4. 真实组合验收（2026-09-12）

用户确认两种运行时都用 `opencode-go / deepseek-v4.1-flash / max`、总并发 3、OpenCode `--auto`。本机版本与目录核对见[预检](logs/real-preflight.txt)。Pi 先运行并停止全部业务会话，再执行 OpenCode；两轮均最多 2 个活跃 worker，交接属于同一 worker，从未靠另建 run 突破总上限。

| 事实 | Pi | OpenCode |
| --- | --- | --- |
| run | issue07-pi | issue07-opencode |
| A 自动交接触发 | 38993 ≥ 35000 tokens | 37078 ≥ 35000 tokens |
| A 会话 | 1→2，正常交接一次 | 1→2，正常交接一次 |
| A 交付／集成 SHA | `fb1194fb6c3db7bf4181794fdc6a88764ba363f2` | `c0dd4f2f7ce93b534541fdc9550b324b557e808a` |
| C 交付／集成 SHA | `6c5bc24afa841b22e327c33fadc099b943c5951d` | `6cde2aef235f0b8d550bae5e326b3bccd7008f0b` |
| B 交付 SHA | `c944516dda6d5cf0a91f50190ba81455e54a3519` | `6703980188c6c80852e374c84c9242c31b38b3d3` |
| B 正常合并后目标 SHA | `7cc8b9228a776a05c5bb9aaf261b4298325fe5ee` | `e5607d4fbd6881674c04fbd600d0a7db3676dee4` |
| C 启动时 | base=A 集成 SHA；B working、无结果；active=2 | base=A 集成 SHA；B working、无结果；active=2 |
| 组合结果 | main 上四个输出逐字节匹配，exit 0 | main 上四个输出逐字节匹配，exit 0 |
| 清理 | 未提交内容先拒绝删除；归档后仅关闭 A 登记 tab／删除 A worktree，保留分支 | 同左 |

原始证据位于 [`logs/real-pi/`](logs/real-pi/) 与 [`logs/real-opencode/`](logs/real-opencode/)：

- `init.json`、`start-*.json`：明确配置、base、max_workers、实际定位和活跃数。OpenCode 的首投均经过一次已知 settled 重投，最终 prompt_attempts=2；属于 E02 已验证的有限机械重试，本轮没有新增异常或人工重派。
- `wait*.json`、`ack-a.json`、`integration-a.json`：首次交付由 wait 捕获；主脑先 ack 并确认目标 SHA 不变，再正常合并 A。`start-c.json` 和 `b-at-release.json` 共同证明 C 从已集成 A 启动且 B 尚未交付。
- `integration-*.json`、`merge-*.json`：三个工单交付→合并前目标→实际集成 SHA 的映射，B 采用正常无冲突 merge，A／C 可快进。`combined-verification.json` 是主脑用于支持收口判断的简单内容核对；worker 各自已执行验收，不额外强制最终验收角色。
- `captured/runtime-config.json`：Pi 会话 JSONL 中的 session.cwd、model_change、thinking_level_change；OpenCode 只读数据库 assistant 消息中的 providerID、modelID、variant、path.cwd。各 4 个会话均实际生效为已确认配置，A 交接前后 cwd 相同，非仅 argv 声明。
- `captured/tool-metadata.json`：本轮会话的工具名、输入路径／命令、时间（不复制整段会话或语料输出）。两个 A 的新会话均实际读取 handoff-001.md，不重读 corpus；旧会话最后工具活动早于新会话第一次工具活动，无旧写入跨越续接。Pi 旧末次命令为同步的只读 handoff／Git 检查；OpenCode 有工具完成时间。`state.json` 另记录旧会话 replaced 和一次 token 自动触发。
- `captured/w07-<kind>-*/`：结果、合同、材料来源 manifest、监督日志、handoff 和清理记录。材料只在主 checkout 忽略目录，也可被隔离 worker 正确读取。
- `stop-*.json`、`cleanup-a-refused.json`、`cleanup-a-archived.json`：全部停止确认 business_stopped=true；默认有 seed-note.txt 时 exit 3 并未删除资源，显式归档后成功且保留分支。`captured/w07-<kind>-a/archived-seed-note.txt` 是归档的耐久副本。

B 的 gate 是稳定观测先后顺序的测试夹具，不是产品固定批次屏障。A 的受控语料用于触达真实阈值；两次续接上下文均低于 35000，不会重读语料反复交接。`smoke.py` 只是这次主脑逐步选择动作的记录脚本；`capture.py` 只读提取本轮证据；二者均不是新的产品入口或后台排程器。

## 5. 资源与后续

现场根 `/tmp/hpm07-real-l6liisu5/`：两仓库 main 已完成各自整体目标；每轮 A 的 worktree 已按明确决定清理，未提交笔记在其 worker 管理目录 `cleanup/uncommitted-*/` 归档，所有分支保留。B／C 的停止现场（4 个 worktree／tab）保留供复核；结果、handoff、归档和关键运行时证据已复制进本证据目录，避免只依赖 OS 临时目录。

07 的真实验证阻塞解除，未发现需生产代码修复的新问题。完整冲突修复场景与正式迁移仍分别属于 08、09。工单 07 本项目提交／集成尚未执行；实际提交后主脑再回写集成 SHA，不能用上表冒烟 SHA 代替。
