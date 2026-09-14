# herdr-plan-manager 架构与流程总览

> 本文是理解性文档（开发资产，已被 `.gitattributes` 的 `export-ignore` 排除在运行时分发包之外），不是运行时代理必须读取的 reference。
> 运行时代理只需要 `SKILL.md` + `docs/plan-management/` 中的文档。

一句话定位：**主脑负责判断，worker 负责具体工作，工具负责机械可靠性。**
`bin/plan_manager.py` 不排程、不选工单、不重试业务、不合并；它只把 Herdr 上的一次执行变成可观测、可交接、可保留、可清理的生活周期。

---

## 图 A：系统全貌（谁控制谁，谁写哪些事实）

```mermaid
flowchart TB
    U["用户：确认目标 / 范围 / 授权边界 / 运行配置 / 并发上限"]

    subgraph SKILL["本 skill（安装到运行时的目录）"]
        SM["SKILL.md：主脑的工作流契约（唯一入口）"]
        DOC["docs/plan-management/：operations · execution-record<br/>plan-worker · repair-and-closeout · repair-brief · validation"]
    end

    subgraph COORD["主脑 = 加载该 skill 的 agent（不在 bin/ 里，也不是子进程）"]
        C["读 spec / 计划 / 完整初始工单集<br/>动态选工单 · 常规失败决策 · 串行合并 · 整体收口"]
    end

    subgraph TOOL["bin/plan_manager.py：工具（机械可靠性）"]
        CLI["CLI：init-run · start · status · wait · ack · read · handoff · stop · cleanup"]
        SUP["后台 supervisor 进程（隐藏命令 _supervise）"]
        ADP["RuntimeAdapter：PiAdapter / OpenCodeAdapter"]
        CTX["上下文观测：get_context.py + pi_context.mjs"]
    end

    subgraph HERDR["Herdr：终端多路复用 + agent 控制"]
        HC["tab / pane / agent start · prompt · read · status · send-keys"]
    end

    subgraph WORK["Worker：一个工单一个 worker，可跨多个会话"]
        W["独立 worktree + 分支 hpm/&lt;worker-id&gt;<br/>Pi 或 OpenCode 会话"]
    end

    subgraph REPO["目标 Git 仓库"]
        TB["目标分支：主脑按仓库策略串行合并"]
        ER["执行记录 execution.md：主脑唯一写者"]
    end

    subgraph STORE["工具状态目录（默认 Git common dir / herdr-plan-manager）"]
        RUN["runs/&lt;run-id&gt;/run.json：一次确认的 kind / provider / model / thinking / max_workers"]
        WS["workers/&lt;worker-id&gt;/：state.json · result.json · contract.md<br/>handoffs/ · materials/ · ack.json · cleanup.json · supervisor.log"]
    end

    U --> C
    SM -.-> C
    DOC -.-> C
    C -->|"调用 CLI（绝对路径）"| CLI
    CLI --> ADP
    CLI --> SUP
    CLI --> RUN
    SUP --> CTX
    SUP --> HC
    CLI --> HC
    HC --> W
    W -->|"写 result.json / handoff 文档"| WS
    SUP --> WS
    W -->|"提交到自己的分支"| TB
    C -->|"合并"| TB
    C --> ER
```

要点：

- 主脑与工具是**两个进程层级**：主脑是交互 agent，工具是它调用的 CLI；后台 supervisor 是工具为每个 worker 拉起的进程。
- 事实分两处存放：**业务事实**（计划、工单状态、执行记录）在目标仓库；**工具状态**（run / worker / result / handoff）在管理目录，二者不互相复制。
- `init-run` 只注册不启动。`start` 一次只派一个工单，同一 run 内共享一份已确认配置与并发额度。

---

## 图 B：主脑主循环（这是项目的主流程图）

```mermaid
flowchart TD
    Start(["开始：目标仓库 + spec/计划 + 完整初始工单集"])
    Start --> P1["读约定 / 计划 / 工单；识别依赖、目标分支、方向性未决问题"]
    P1 --> P2{"材料齐全且方向性抉择已解决？"}
    P2 -->|"否"| P3["向用户补齐或澄清：只问缺失项与矛盾项"] --> P1
    P2 -->|"是"| P4["一次确认执行契约：kind · provider · model · thinking · max_workers + 授权边界"]
    P4 --> P5["init-run 注册 run：校验运行时配置，锁定配额，拒绝静默回退"]
    P5 --> P6["在目标仓库创建 execution.md：输入 / 授权 / 目标基线 / 完整工单总表"]
    P6 --> Loop{"还有可推进的工作？"}
    Loop -->|"没有"| Close["收口：逐条映射计划级目标 → 集成结果 + 验收证据"]
    Loop -->|"有"| Sel{"有空闲槽位，且存在依赖已满足的工单？"}
    Sel -->|"没有"| Obs["wait：等待任一 delivery / exception<br/>窗口超时不是任务结果"]
    Sel -->|"有"| St["start：一个工单一个 worker<br/>显式 base SHA + 材料快照 + 渲染 worker 契约"]
    St --> Obs
    Obs --> Item{"拿到待处理事项？"}
    Item -->|"窗口超时，无事项"| Loop
    Item -->|"是"| Dec["检查 result.json · plan_deviations · 验收证据 · 分支 HEAD / 产物"]
    Dec --> Judge{"处置决定（质量与集成由主脑判断）"}
    Judge -->|"交付可接受"| BF["回填 execution.md：结果 + 偏差逐条处置<br/>无偏差也要显式声明 none"]
    Judge -->|"失败 / 需决策"| Auth{"在授权内可自行处理？"}
    Auth -->|"是"| Act["调查 / 换 worker 重试 / 新增修复·验证工单<br/>替换前确认旧写者已停止业务写入"] --> BF
    Auth -->|"否"| Ask["向用户提问：需求取舍 / 扩大目标 / 改授权"] --> BF
    BF --> Ack["ack &lt;worker-id&gt;/&lt;item-id&gt;<br/>交付 ≠ 集成；ack ≠ 验收；终端空闲 ≠ 结果"]
    Ack --> Code{"是代码成果？"}
    Code -->|"是"| Merge["主脑串行合并到目标分支<br/>按仓库策略，记录 delivered SHA → integration SHA"]
    Code -->|"否"| Acc["记录非代码成果可接受的位置"]
    Merge --> Loop
    Acc --> Loop
    Close --> Map{"每个计划级目标都有集成结果 + 证据支撑？"}
    Map -->|"否"| New["新增修复 / 组合验证工单（新 worker 承担）"] --> Loop
    Map -->|"是"| Done(["报告完成：主要集成结果 · 验证摘要 · 未决项 · 保留资源位置"])
```

阅读要点：

- 循环**没有批屏障**：拿到任一事项就处理，不必等其他 worker。
- `ack` 可以发生在合并之前，但执行记录必须保留 `pending integration`；发布依赖代码工单时，必须在所需成果进入目标分支之后。
- 常规失败由主脑在授权内自行处理；只有需求取舍、扩大目标、改授权约束才回到用户。
- 修复、冲突解决、组合验证都是**新 worker 的新工单**，主脑只做判断和正常合并。

---

## 图 C：单个 worker 的生命周期（state.json 中的 `lifecycle.state`）

```mermaid
stateDiagram-v2
    [*] --> allocating: start：锁内占槽并注册，随后建 worktree / 分支 / tab / agent
    allocating --> prompting: agent 到达可交互状态，准备投递契约提示词
    prompting --> running: 提示词确认被运行时接手
    allocating --> launch_failed: 启动失败，保留现场并返回错误
    prompting --> launch_failed

    running --> handing_off: 上下文达阈值，或主脑 handoff 请求
    handing_off --> running: 同 worker 新会话接续，工单 / 分支 / worktree / 配置不变
    handing_off --> handoff_failed: settle、文档或替换会话失败
    handoff_failed --> running: 主脑再次决策：再交接 / 停止后另派

    running --> delivered: result.json 声明 delivered 且结构 / HEAD / 产物校验通过
    running --> failed: result.json 声明 failed
    running --> needs_decision: result.json 声明 needs-decision
    running --> protocol_failure: 结果非法 / 连续 3 次解析失败 / 一次补报后仍无结果
    running --> agent_exited: agent 在写出有效结果前消失
    running --> stopped: stop 且 business_stopped = true

    delivered --> [*]
    failed --> [*]
    needs_decision --> [*]
    protocol_failure --> [*]
    agent_exited --> [*]
    stopped --> [*]
```

要点：

- `allocating` / `prompting` 仍占一个并发槽位；只有终态才释放槽位。
- `delivered` 后如果条件全部满足，supervisor 会自动关闭该 worker 的 tab（分支、worktree、结果、handoff、日志、材料全部保留）；任一条件不确定就保留 tab，并把原因写进 `status --worker` 的 `release` 字段。
- `handoff-failed` 会设置 `auto_suppressed`，不再自动交接，已完成的交接文档和现场都保留给主脑决策。

---

## 图 D：自动 worker 交接时序（上下文阈值触发）

```mermaid
sequenceDiagram
    autonumber
    participant SUP as supervisor 后台进程
    participant OLD as 旧会话 session N
    participant H as Herdr
    participant NEW as 新会话 session N+1
    participant ST as 管理目录

    SUP->>SUP: 采样上下文（总 token / 窗口占比）；样本必须属于当前 session，陈旧样本不触发
    SUP->>OLD: 用运行时中断序列暂停业务写入
    SUP->>OLD: 要求把交接文档写到 handoffs/handoff-NNN.md
    OLD->>ST: 写入 6 个必需小节：Progress / Decisions / Verification / Commits / Uncommitted work / Next steps
    SUP->>SUP: 只校验文档结构；不合格时允许且仅允许一次纠正提示
    SUP->>OLD: 再次确认已停止业务写入
    SUP->>H: 新建 tab 并启动同配置的替换 agent
    SUP->>NEW: 投递续接提示词，指向 handoff 文档
    NEW->>ST: 读取文档，继续同一工单
    SUP->>OLD: 结束旧会话（运行时退出序列）
    SUP->>ST: sessions += 新会话；工单 / 分支 / worktree / 配置保持不变
    Note over SUP,ST: 任一步失败 → 记 exception 事项，auto_suppressed=true，保留现场交主脑
```

要点：

- 自动交接由**工具**执行，无需主脑逐次批准；主脑只在失败、异常或想主动交接时介入（`handoff --reason`）。
- 工作成果通过**文档**交接给新会话，而不是恢复旧对话；这与 Herdr 的 live handoff / agent resume 是不同机制。
- 配置、分支、worktree 都不变，因此“同一工单仍在同一个 worker 名下”，只换会话。

---

## 图 E：交付 → 集成 → 清理（结果与资源如何处置）

```mermaid
flowchart TD
    R["worker 原子写入 result.json"] --> V{"record_result 校验：<br/>ticket / worker 身份 · status · plan_deviations · acceptance · verification<br/>head == 分支 HEAD · artifacts 存在且不越界"}
    V -->|"非法"| PF["protocol-failure，生成 exception 事项"]
    V -->|"暂不可读"| RR["transient：等待；settled 后只补报一次"] --> V
    V -->|"通过"| IT["durable item：delivered / failed / needs-decision"]

    IT --> REL{"自动释放条件全部成立？<br/>结果已持久化 · 所有注册会话确认退出<br/>· worktree 无未提交内容 · tab 只含本 worker 的 pane / agent"}
    REL -->|"成立"| CL["关闭注册的 tab；其余资源全部保留"]
    REL -->|"不成立或无法确认"| KEEP["保留 tab，原因记录在 status --worker.release"]

    IT --> BACK["主脑回填 execution.md，然后 ack"]
    CL --> BACK
    KEEP --> BACK

    BACK --> MG{"是代码成果？"}
    MG -->|"是"| M["主脑串行合并；记录 delivered SHA → integration SHA<br/>squash 等非祖先集成要保留显式映射"]
    MG -->|"否"| A["记录非代码成果的可接受位置"]
    M --> C1["stop：要求返回 business_stopped = true，现场保留"]
    A --> C1
    C1 --> C2["cleanup：必须给出明确决定<br/>--integrated &lt;sha&gt; 或 --disposition &lt;text&gt;"]
    C2 --> UC{"worktree 有未提交内容？"}
    UC -->|"有"| HD["--archive-uncommitted 存档，或经授权 --discard-uncommitted"]
    UC -->|"无"| RM["移除外加 worktree：只处理已注册、已拥有的资源"]
    HD --> RM
    RM --> BR["分支默认保留；仅 --delete-branch 才删除"]
```

---

## 关键不变量速查

| 说法 | 事实 |
| --- | --- |
| worker 交付了 | 只是 worker 的声明；结构校验通过不等于质量通过 |
| ack 了 | 只表示主脑处理过该事项；不改变交付或集成结论 |
| 终端 idle / done | 不是结果；settled 但无有效结果会走一次补报，再失败则 protocol-failure |
| wait 超时 | 只说明等待窗口内没有事项，对 worker 状态不作任何断言 |
| 已交付 | 不等于已集成；合并前后要在执行记录里分别留 SHA |
| worker 槽位释放 | 由生命周期终态决定；交接中的 worker 仍占一个槽位 |
| 自动关 tab | 不删分支、worktree、结果、handoff、日志、材料 |
| cleanup 删分支 | 默认不删；`--delete-branch` 才是显式决定，`--force-branch` 不是自动回退 |
| 旧执行记录 | 不迁移、不自动接管、不自动删除（第一版边界） |

## 谁在哪个文件里负责什么（给二次开发用）

| 关注点 | 位置 |
| --- | --- |
| 主脑工作流与职责边界 | `SKILL.md`、`CONTEXT.md` |
| 操作手册（命令、阈值、释放条件、清理决定） | `docs/plan-management/operations.md` |
| 执行记录模板（主脑唯一写者） | `docs/plan-management/execution-record.md` |
| worker 契约与 result.json 协议 | `docs/plan-management/plan-worker.md` |
| 冲突 / 行为修复与整体收口 | `docs/plan-management/repair-and-closeout.md`、`repair-brief.md` |
| 真实验收实验（A/B/C、修复、生命周期覆盖） | `docs/plan-management/validation.md` |
| CLI、supervisor、运行时适配、上下文阈值 | `bin/plan_manager.py`、`bin/get_context.py`、`bin/pi_context.mjs` |
| 确定性集成测试与运行时假件 | `tests/`（`export-ignore`，仅源码 checkout 可见） |
