# 操作参考

本页是[预览 skill](SKILL.md)的机械操作参考，不负责排程。参数权威来源为 `bin/plan_manager.py <操作> --help`；只使用新入口，不调用旧 dispatcher。以下 Shell 变量是说明用占位值，须先换成已确认值，不能直接采用历史冒烟配置。

## 准备与登记

以本页所在目录解析工具绝对路径为 `HPM=<项目根>/bin/plan_manager.py`；`REPO` 为目标 checkout 的绝对路径。检查 `HERDR_ENV=1`、`HERDR_WORKSPACE_ID` 存在，读取本机 `herdr --help`／`herdr agent --help` 发现实际 CLI，按本机帮助核查 Pi／OpenCode 官方集成已安装且可用。环境不可用先报告，不切换到别的执行协议。

运行时目录查询仅辅助用户选择和校验：Pi 使用 `pi --list-models`，OpenCode 使用 `opencode models --verbose`。必须校验所选模型支持指定 thinking；工具会在登记与启动时再次校验，不能拿全局枚举代替模型能力证据。

```bash
python3 "$HPM" init-run --repo "$REPO" --run-id "$RUN" \
  --kind "$KIND" --provider "$PROVIDER" --model "$MODEL" \
  --thinking "$THINKING" --max-workers "$MAX_WORKERS"
```

`RUN` 是当前执行的稳定标识（小写字母、数字、连字符，首字符为字母或数字，最多 64 字符）。保存返回 JSON 的 `run_id` 与配置；登记不等于启动 worker。`init-run` 不覆盖已有 run。

当前一个 run 绑定一套配置；`start` 继承它，若重复传入配置则必须相同。新会话沿用同一配置。需要改配置时先确认变更，另建 run 并记明关系，主脑同时约束所有相关 run 的总活跃数：工具并发锁仅限单 run，不能用新 run 绕过用户的总上限。双运行时演示可分别执行同一场景，不需要假设一个 run 内混用运行时。

OpenCode 适配器通过新 pane 的 `OPENCODE_CONFIG_CONTENT` 显式映射模型与推理配置，不改业务仓库配置；当前启动附加 `--auto`，自动批准未显式拒绝的权限，仍保留显式 deny。用户若未授权此行为，应在派发前澄清；工具没有临时关闭该参数的入口。

管理目录默认位于目标仓库 Git common dir 的 `herdr-plan-manager/` 下。可用 `--management-root <绝对路径>` 改位置，但本次执行后续每条操作都必须指定同一个位置。下面示例使用默认位置。

## 派发

主脑先确认本任务依赖已满足，取得**目标分支当前完整提交 SHA** 作为 `BASE`，再调用：

```bash
python3 "$HPM" start --repo "$REPO" --run "$RUN" \
  --ticket-id "$TICKET" --title "$TITLE" --base "$BASE" \
  --material "$SPEC" --material "$ISSUE" \
  --instructions "$TASK_CONTEXT"
```

- `TICKET` 是主脑分配的协议标识（字母／数字开头，含字母、数字、点、下划线、连字符，最多 64 字符），不约束原工单文件名、语言、标题或依赖写法。
- `TASK_CONTEXT` 补充单一任务的目标、范围、验收与依赖说明，不能只写“继续计划”。需要其他参考时重复 `--material`，参数可为文件或目录。
- 材料会受控快照到 worker 管理目录，manifest 保留原路径；主 checkout 内未提交或忽略的材料因此仍可读。派发前检查包含了必要引用目标；复制一个 Markdown 文件并不会自动复制其链接引用的所有文件。不要把秘密或无关目录整包传入。
- 返回 `worker_id`、branch、worktree、管理与结果路径及启动事实；将这些引用记入执行记录，不复制整份状态 JSON。每个 worker 只做这一工单，工具创建独立 worktree 并封装后台监督。
- 启动报错先查登记与现场。投递是否接受不确定时不能盲目重发；有限机械重试属于工具，新业务尝试由主脑决定。

默认交接阈值为 300000 tokens 或窗口占用 0.8；按需设置 `--handoff-tokens`／`--handoff-pct`，阈值应高于新会话种子上下文。300K 是运行策略，不是模型退化的科学界限。`--context-window` 仅在有可靠窗口依据时覆盖，不能用猜测掩盖观测失败。

## 观察与处理事项

```bash
python3 "$HPM" status --repo "$REPO" --run "$RUN"
python3 "$HPM" status --repo "$REPO" --worker "$WORKER"
python3 "$HPM" wait --repo "$REPO" --run "$RUN"
```

run 状态包含活跃数、worker 索引及待处理事项；worker 状态用于检查结果、会话／handoff、监督者、资源和 `cleanup`。活跃 worker（含交接）占槽；已交付且停止业务写入的保留现场不占槽。

`wait` 默认无限等待，已有事项立即返回，返回的 `items` 可有多条。主脑逐条处置即可，不必等其他 worker。若主脑工具调用有窗口限制，可加 `--timeout 30` 等正数：`timed_out: true` 和空 `items` 只表示等待窗口结束，不代表工单失败或可以清理。协议响应超时同样不是任务执行预算。

检查 `kind`／`code` 后读取相关 worker 结果与证据。`idle/done` 不是业务验收，`blocked` 也可能是运行错误。无结果空闲时工具最多进行一次结果补报，失败形成协议异常。成功交接由工具自行处理；持续观测失败、交接失败、监督缺失等由主脑决定下一步。Pi 的部分阻塞 UI 可能仍报告 working；怀疑停滞时可用：

```bash
python3 "$HPM" read --repo "$REPO" --worker "$WORKER" --lines 120
```

需要主动交接时复用标准流程：

```bash
python3 "$HPM" handoff --repo "$REPO" --worker "$WORKER" --reason "$REASON"
```

请求返回不等于交接成功，之后查 `status`；成功续接不会创建新工单。结果声明后不再允许交接。交接失败保留旧现场与耐久文档，不自己发送“继续”或并行启动同 worktree 的写入者。

处置已写入主脑执行记录后确认事项（`ITEM` 直接取返回值的 `item_id`，格式为 `<worker-id>/<item-id>`）：

```bash
python3 "$HPM" ack --repo "$REPO" --item "$ITEM" --note "$DECISION"
```

可重复 `--item`；ack 后此事项不再被 wait 返回，其他新事项仍可见。ack 不停止 worker、不改变质量结论、不推进目标分支。先 ack 的交付仍须保留“待集成”记录，不能因待处理列表清空而宣布完成。

## 停止与明确清理

```bash
python3 "$HPM" stop --repo "$REPO" --worker "$WORKER" --reason "$REASON"
```

必须查看返回的 `business_stopped`；无法确认全部登记会话已停止业务写入时会报告 `stop-incomplete`。保留现场并调查，不视为已释放写入权。`stop` 协调监督与正在交接的会话，不删除 worktree、分支、结果或 handoff。

确认资源可清理、停止已获确认后，选择一种明确决定：

```bash
# 代码已集成，SHA 为主脑记录的实际集成提交
python3 "$HPM" cleanup --repo "$REPO" --worker "$WORKER" --integrated "$INTEGRATION_SHA"

# 调查产物已保存／任务明确放弃等非集成处置（不能照抄模糊理由）
python3 "$HPM" cleanup --repo "$REPO" --worker "$WORKER" --disposition "$DISPOSITION"
```

`--integrated` 是主脑提供的结论，不是工具替你验证 spec 或完成合并。对非代码产物先确保有耐久副本，再清理其 worktree。

- 默认有未提交内容就拒绝删除。需要保留时加 `--archive-uncommitted`，归档后再检查返回的保存位置；只有明确授权丢弃该内容才加 `--discard-uncommitted`，两者互斥。失败／异常现场继续保留，除非已有明确处置决定。
- 默认保留分支。`--delete-branch` 使用 Git 的安全删除检查；`--force-branch` 须与它同时使用，适用于已核实 squash 等集成映射、明确批准删除的情形，不是清理失败后的自动重试。
- 只关闭登记 tab，核验归属与活动写入；不接管或清理旧 dispatcher 资源。已提交成果也要先确认保留分支或已集成，不能用笼统 disposition 丢弃唯一代码成果。
- 清理后查看 `status --worker` 的 `cleanup`：记录已清理资源、残留／拒绝原因和归档位置。管理记录、结果及 handoff 的耐久位置仍用于追溯。

## A／B／C 动态演示

两次独立执行（Pi、OpenCode）各用一套已确认配置、`max_workers=2`，一次性仓库中准备完整 A／B／C 工单：A 产出接口，B 做无关工作，C 只依赖 A。真实演示先读[验收步骤](validation.md)。

1. 同一目标基线启动 A、B；可控触发 A 自动交接，保存会话、配置与同 worktree 证据。
2. wait 收到 A 交付时 B 仍活跃；验收 A 并记录交付。即使先 ack，目标分支还未更新，C 仍待依赖。
3. 正常合并 A，记录实际集成 SHA；以新目标 SHA 启动 C，证明 C 可以在 B 结束前执行，活跃总数不超过 2。
4. 处理后续结果，正常集成并核对目标覆盖；按明确决定 stop／cleanup，核验未提交内容和失败现场保留。

这是主脑决策的演示，不是要固化进工具的批次模板或排程算法。
