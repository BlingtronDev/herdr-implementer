# 工单 07 真实组合冒烟执行记录

## 目标与授权

- 目标：验证预览主脑方法与真实 Pi／OpenCode worker 的自动交接、wait-any、动态集成和保留现场组合。
- 授权：2026-09-12 用户确认两种运行时均用 `opencode-go / deepseek-v4.1-flash / max`，总并发上限 3，OpenCode 使用 `--auto`。
- 环境：Herdr 0.8.2；Pi 0.85.1／官方集成 v8 current；OpenCode 1.18.30／官方集成 v10 current；调用 workspace `wJ`。
- 输入：`smoke.py setup` 生成完整 spec、A／B／C 和语料，只在主 checkout 忽略目录；worker 使用工具快照。测试业务仓库根：`/tmp/hpm07-real-l6liisu5/`，Pi 与 OpenCode 子仓库各使用 main。
- 管理目录：各仓库 `.git/herdr-plan-manager/`。run 分别为 `issue07-pi`、`issue07-opencode`，登记 max_workers=3；主脑顺序执行两轮，任何时刻总活跃数不超过 3。
- 工具与文档实施基线：`b1eae56` 加本轮未提交的工单 07 文档／测试，无生产工具修改。

## 工单与依赖

两轮各执行同一初始集合，不是工具解析的 DAG：

| 工单 | 目标 | 解锁条件 | worker |
| --- | --- | --- | --- |
| A | 自动交接后提交 api.txt 与 resumed.txt，保留未提交 seed-note | 无 | w07-<kind>-a |
| B | 提交独立 b.txt，在主脑观测点释放后验收交付 | 无代码依赖；受控实验观测点 | w07-<kind>-b |
| C | 读取已集成 api.txt 并提交 consumer.txt | A 实际进入 main | w07-<kind>-c |

B 的观测点是为稳定复现“A 集成后 C 在 B 结束前启动”设置的实验夹具，不是产品排程政策或任务超时。B 等待期间有活跃前台调用；主脑创建只读 gate 后 B 才完成结果验收。A 采用 35000 tokens 阈值与一次语料读取，新会话不重读语料，以避免阈值 1 造成连续无意义交接。观察窗口超时不推导失败。

## 决定与实时进展

- 已通过目录查询确认用户描述的 “opencode go” 对应正式标识 `opencode-go`，不是模型替换。
- 先运行 Pi。B 和 A 已从同一初始 SHA `b9dda994bc983a01791c78bc70b479a591906533` 启动；启动响应活跃数 1→2。A 首次保存的上下文观测 32223 tokens，尚未到 35000 阈值，正常继续。
- 首次 wait 30 秒返回空窗口，不算失败；监督继续运行。

- Pi A 在 38993 tokens 自动交接一次，续接会话首次保存观测为 18062；主脑未干预交接。收到 A 交付后接受其验收，先 ack 标记“待集成”，确认目标 SHA 未变化，再正常合并 A。以 A 集成 SHA 启动 C，此时 B working 且无结果（start-c active=2）。随后释放 B 实验观测点。
- Pi B 先交付，主脑接受并 ack，记录“等待 C 后正常集成”；C 交付后串行合并 C、B，四个输出内容核对通过。所有 worker stop 确认 business_stopped=true 后，才开始 OpenCode 轮次。
- OpenCode A 在 37078 tokens 自动交接一次，续接后上下文低于 35000；后续与 Pi 相同，A 合并后 C 先于 B 交付启动。首次业务投递的 settled 丢弃由工具有限重投一次处理，未动模型／权限配置，未派新尝试。
- 两个 A 的 seed-note.txt 均为有意保留的未提交内容。主脑决定：先验证 cleanup 默认拒绝，随后明确 `--archive-uncommitted`，保存笔记后清理 A worktree 与其登记 tab，保留分支。其余 B／C 仅 stop、保留现场，不作自动清理。
- `verify_real.py` 核对两轮共 8 个真实会话的配置／cwd、交接、时序、结果、集成和归档，全部通过。原始命令／响应按动作存入 `logs/real-<kind>/`；`smoke.py` 只提供主脑显式调用的动作，不自动选择工单或重试。

## 交付与实际集成

完整 SHA 映射见各轮 `integration-*.json`；以下为便于阅读的缩写。

| 轮次／工单 | 交付 SHA | 合并前 main | 实际集成 SHA | 决定 |
| --- | --- | --- | --- | --- |
| Pi A | fb1194f | b9dda99 | fb1194f | 已集成，解锁 C |
| Pi C | 6c5bc24 | fb1194f | 6c5bc24 | 已集成 |
| Pi B | c944516 | 6c5bc24 | 7cc8b92 | 无冲突正常 merge；目标满足 |
| OpenCode A | c0dd4f2 | b9dda99 | c0dd4f2 | 已集成，解锁 C |
| OpenCode C | 6cde2ae | c0dd4f2 | 6cde2ae | 已集成 |
| OpenCode B | 6703980 | 6cde2ae | e5607d4 | 无冲突正常 merge；目标满足 |

## 验证与收口

- 复用六个 worker 的验收证据：真实读取材料、精确内容检查、提交列表、真实 HEAD 与未提交项。结果分别保存在 `logs/real-<kind>/captured/w07-<kind>-<role>/result.json`。
- 两轮 main 的 api.txt、resumed.txt、consumer.txt、b.txt 全部逐字节匹配，`combined-verification.json` exit 0；主脑此次简单内容核对足够支持收口，无需另派整体验证工单。
- `final-run.json`：两轮各 active_count=0、pending_items=[]；业务停止且不再自动交接。并发峰值 2，小于用户总上限 3。
- 资源：`/tmp/hpm07-real-l6liisu5/{pi,opencode}/.git/herdr-plan-manager/`。A worktree 清理，分支保留，笔记在 `workers/w07-<kind>-a/cleanup/uncommitted-*/untracked/seed-note.txt`；B／C worktree 和 tab 保留。证据副本已进入本项目证据目录。
- 整体结论：两轮真实组合目标均完成，E09 关闭。第 7 工单文档与验证全部交付；本项目改动仍未提交／集成，不能混淆冒烟仓库 SHA 与项目集成 SHA。
