# 工单 04 执行记录：双运行时上下文观测与自动 worker 交接

- 日期：2026-09-11
- 执行：工单 04 worker
- 授权配置（沿用工单 01／02／03 已确认值）：`provider=opencode-go`、`model=deepseek-v4.1-flash`、`thinking=max`（OpenCode 为 variant `max`），最大 2 个活跃 worker
- 状态：已交付（delivered）
- 代码基线：工单 03 提交 `53b277e`

## 1. 结论摘要

- `bin/plan_manager.py` 现在为每个 worker 维护会话序号与逐会话记录：`context_ref`、观测样本、handoff 记录与状态。观测、异常事项和交接都关联具体会话序号。
- 上下文观测复用 `bin/get_context.py`（Pi 走 session JSONL，OpenCode 走 `opencode.db`），并用 `HPM_CONTEXT_HELPER` 支持确定性替换。样本区分 `current`／`stale`／`pending`／`unobservable`；无法观测只形成可见异常事项，绝不按零用量触发交接。
- 默认阈值：达到 300,000 tokens 或窗口占用 80%；`--handoff-tokens`、`--handoff-pct`、`--context-window` 可配置。主动交接（`handoff` 子命令）与自动阈值走同一个 `attempt_handoff` 标准流程。
- 标准流程：暂停旧会话业务执行（Pi `escape`、OpenCode `escape escape`）→ 用运行时各自的 skill 调用方式要求写 handoff 文档到耐久管理目录 → 结构校验（六个必需小节，缺失允许一次补正）→ 在同一 worktree／branch 用已确认配置启动新 tab 会话 → 续接提示投递确认 → 才结束旧会话。旧会话结束前新会话不开始工作。
- 失败语义：文档缺失／结构不合格、替换启动失败、观测失败都形成可读取的待处理异常，保留文档、分支、worktree 与旧会话；成功交接只记录进展，不要求主脑决定。
- 确定性测试 64 → 85 个。真实冒烟：Pi 自动阈值（7 次连续交接后交付）、Pi 主动交接（1 次交接后交付）、OpenCode 自动阈值（2 次交接后交付）；三者结果 HEAD 与分支一致，8 个 Pi 会话与 3 个 OpenCode 会话的实际生效 provider／model／thinking／cwd 完全一致。
- **发现并修复计划外缺陷 E04：** Pi 会话模型注册表对该模型不返回 context window，导致 Pi 上下文完全不可观测（首次冒烟因此错过交接、靠结果补报完成）。`get_context.py` 增加 `pi --list-models` 目录回退后恢复。

## 2. 改动

| 文件 | 说明 |
| --- | --- |
| `bin/plan_manager.py` | 会话记录与序号（`sessions`／`session_index`）；`Herdr.agent_session_ref`／`wait_agent_session`／`wait_agent_gone`；上下文采样与分类、阈值判定、陈旧样本守卫；handoff 文档定位（含 OS 临时目录耐久副本回退）与结构校验；`attempt_handoff` 标准流程（暂停、请求文档、一次补正、启动替换、续接确认、结束旧会话、失败留存）；`handoff` 子命令；`status` 暴露会话、观测与交接历史；`start` 新增阈值参数并初始化交接配置 |
| `bin/get_context.py` | Pi 模型注册表没有 context window 时回退到 `pi --list-models` 目录（`window_source=pi --list-models`）；`--window` 显式覆盖标记为 `explicit --window` |
| `prompts/plan-worker.md` | 新增 “Session handoff” 一节：按指令调用 handoff skill、写指定绝对路径、六个必需小节、写完即结束本轮 |
| `tests/fakes/bin/get_context.py` | 新增确定性上下文适配器（ok／stale／error、spike 调用计数、window 覆盖） |
| `tests/fakes/bin/herdr` | agent 会话引用、`ctrl+c` 真实退场语义、`start_hard_fail_at` 永久启动失败 |
| `tests/fakes/scenario.py` | 新增 `handoff` 行为：阶段一产出、响应 handoff／correction／continuation 提示、写结构化文档（valid／invalid／invalid-first／missing／temp／working 模式）、连续交接计数、新会话读取文档并交付 |
| `tests/test_plan_manager.py` | 新增 19 个测试：观测记录、陈旧样本、不可观测异常、双运行时自动交接、连续交接、临时目录副本、文档补正与失败、替换失败、停止竞态、主动交接去重、失败重试复用文档、纯函数规则 |
| `tests/test_context.py` | 新增 Pi window 目录回退与尺寸解析测试 |

未改动：旧 `bin/dispatcher.py`、`SKILL.md`、`schema/`；未实现并发额度与 wait／ack（05）、cleanup（06）、主脑动态排程（07）。

## 3. 接口与行为

```bash
# 启动时配置阈值（默认 300000 / 0.8）
python3 bin/plan_manager.py start ... \
  [--handoff-tokens 300000] [--handoff-pct 0.8] [--context-window 0]

# 主动交接：与自动阈值同一条标准流程；返回 requested，进度看 status
python3 bin/plan_manager.py handoff --repo <repo> --worker <worker-id> --reason "<为什么交接>"

# 查看会话、观测与交接事实
python3 bin/plan_manager.py status --repo <repo> --worker <worker-id>
```

- 会话序号从 1 递增；初始 agent 为 `<worker-id>`，后续为 `<worker-id>-s<N>`（≤32 字符，符合 Herdr agent 命名规则）。
- 交接文档固定在 `<management>/workers/<worker>/handoffs/handoff-<N>.md`（耐久位置）。若运行时把文档写到 OS 临时目录，工具在替换会话前复制耐久副本并记录 `document_source=temp-copy`。
- 文档结构要求六节：`Progress`、`Decisions`、`Verification`、`Commits`、`Uncommitted work`、`Next steps`。结构缺失先发送一次明确补正提示；仍不可用则形成 `handoff-document-invalid` 异常。校验基于结构与可读性，不靠字数。
- 失败异常码：`handoff-settle-failed`、`handoff-document-timeout`、`handoff-document-invalid`、`handoff-session-exited`、`handoff-replacement-failed`、`handoff-old-session-exit-failed`、`context-unobservable`、`handoff-failed`（未分类交接错误）。
- 交接失败后 `auto_suppressed=true`，不再自动重触发；主脑可再次执行 `handoff` 重试。若耐久文档已存在且结构合格，重试直接复用它，不重复请求文档。
- 交接成功清除自动抑制；新会话的首次观测正常参与阈值判断（因此阈值低于新会话起始上下文时会连续交接，见第 6 节）。
- 观测相关环境变量（默认值）：`HPM_CONTEXT_POLL_SECONDS=15`、`HPM_HANDOFF_WAIT_SECONDS=900`、`HPM_HANDOFF_SETTLE_SECONDS=15`、`HPM_HANDOFF_SETTLE_TIMEOUT_SECONDS=20`、`HPM_HANDOFF_CORRECTION_SECONDS=30`；`HPM_CONTEXT_HELPER` 可替换上下文适配器。

## 4. 确定性测试

```bash
python3 -m pytest tests/test_plan_manager.py -q   # 44 passed
python3 -m pytest tests/ -q                       # 85 passed
```

覆盖矩阵：

| 验收点 | 测试 |
| --- | --- |
| 可解释观测（current／window／source／session 关联） | `test_context_observation_records_interpretable_sample` |
| 陈旧样本不触发 | `test_stale_context_sample_never_triggers_a_handoff`、`test_context_trigger_and_staleness_rules`、`test_stale_sample_guard_ignores_old_sessions` |
| 不可观测不按零用量、形成异常 | `test_unobservable_context_is_an_exception_not_zero_usage` |
| 双运行时自动阈值交接与真实续接 | `test_context_threshold_triggers_automatic_handoff[pi/opencode]` |
| 连续交接仍属同一工单／worker | `test_multiple_handoffs_stay_one_worker` |
| OS 临时目录文档耐久副本 | `test_handoff_document_is_copied_from_os_temp` |
| 结构校验与一次补正 | `test_invalid_handoff_document_gets_one_correction`、`test_handoff_document_validation_requires_structure` |
| 文档失败形成异常并保留现场 | `test_handoff_document_failure_is_a_pending_exception`、`test_handoff_document_timeout_retains_scene` |
| 替换启动失败保留旧会话与文档 | `test_replacement_start_failure_retains_scene_and_old_session[pi/opencode]` |
| 停止与交接竞争 | `test_stop_during_handoff_aborts_without_replacement` |
| 主动交接与去重 | `test_explicit_handoff_request_is_not_repeated`、`test_handoff_request_rejected_for_terminal_worker` |
| 失败后重试复用文档 | `test_handoff_retry_reuses_valid_document` |
| Pi window 目录回退 | `tests/test_context.py::PiWindowTests` |

## 5. 真实冒烟

环境：Herdr 0.8.2、Pi 0.85.1、OpenCode 1.18.30（`logs/versions.txt`），Herdr Pi／OpenCode 官方集成均为 current。所有一次性仓库与管理目录均在 `/tmp/opencode/` 下（OpenCode `external_directory` 白名单覆盖）。

### 5.1 Pi 自动阈值：8 会话 7 次交接后交付

- 仓库 `/tmp/opencode/hpm-exp04-pi/repo`，管理目录 `/tmp/opencode/hpm-exp04-pi/manager`，`--handoff-tokens 1`。
- 会话 1 首个完成回合观测 17,942 tokens，触发自动交接；此后每会话首个回合都超过 1 token 阈值，因此连续交接 7 次，最终会话 8 交付。`state.json`：`sessions[1..7] status=replaced`、`sessions[8] status=ended`，`handoff.history` 7 条全部 `trigger=tokens`，结果 HEAD `65470fb` 与分支一致（`logs/pi/state.json`、`logs/pi/result.json`、`logs/pi/git-log.txt`）。
- 旧会话全部退场：8 个 agent 中仅最后交付会话在冒烟结束时仍存活，其余 `herdr agent get` 均为 gone。
- 交接文档 7 份都写入 `handoffs/` 且结构合格；`handoff-001.md` 准确记录阶段一提交 `bac798a` 与阶段二待办，最终交付摘要写明 “Phase 2 completed after context handoff … Phase 1 (plan commit bac798a) was preserved”（`logs/pi/handoffs/`、`logs/pi/result.json`）。
- 每个新会话都是新 tab、同 worktree、同 branch；旧 tab 在新会话确认后才停旧 TUI（`logs/pi/status-delivered.json`、`logs/pi-config/session-config.jsonl`）。

### 5.2 Pi 主动交接：标准流程单次交接后交付

- 仓库 `/tmp/opencode/hpm-exp04-pi2/repo`，默认阈值；worker 启动 8 秒后执行 `handoff --reason "single proactive handoff demo"`（`logs/pi-proactive/handoff-request.json`）。
- `state.json`：会话 1 交接记录 `trigger=requested`、`status=completed`；会话 2 交付，观测 19,648 tokens；结果 HEAD `c969194`（`logs/pi-proactive/state.json`、`result.json`、`supervisor.log`）。
- 主动交接与自动交接共用同一流程代码路径，证明标准过程不依赖触发来源。

### 5.3 OpenCode 自动阈值：3 会话 2 次交接后交付

- 仓库 `/tmp/opencode/hpm-exp04-oc/repo`，管理目录在 `/tmp/opencode/hpm-exp04-oc/manager`，`--handoff-tokens 1`；启动 `prompt_attempts=2`（E02 的投递确认生效）。
- 会话 1 观测 13,808、会话 2 观测 15,429 后交接，会话 3 交付；结果 HEAD `f76e150` 与分支一致，`smoke.txt` 内容为 `SMOKE04-OC-CONTENT-7b2e`（`logs/oc/state.json`、`result.json`、`smoke.txt`、`git-log.txt`）。
- 两份交接文档结构合格，`handoff-001.md` 明确“阶段一完成、阶段二未开始”（`logs/oc/handoffs/`）。

### 5.4 跨会话配置一致性（权威证据）

- Pi：从 8 个会话 JSONL 提取 `model_change`／`thinking_level_change`／`session.cwd`，全部为 `opencode-go / deepseek-v4.1-flash / max`，cwd 全部为同一 worktree（`logs/pi-config/session-config.jsonl`）。
- OpenCode：只读查询 `opencode.db` 的 `message` 表，3 个会话的 assistant 行全部为 `provider=opencode-go`、`model=deepseek-v4.1-flash`、`variant=max`、`agent=build`，`path.cwd` 全部为同一 worktree（`logs/oc/session-config.jsonl`）。
- 结论：会话替换保持相同 worktree、branch、provider、model、thinking，未出现静默替换或默认模型回退。

### 5.5 首次 Pi 冒烟的观测缺陷（E04 复现现场）

`logs/pi-attempt1-unobservable/` 保留修复前现场：`status-unobservable.json` 显示 `context={state: unobservable, error: "Pi model registry has no context window"}` 与 `context-unobservable` 异常事项；监督层因此无法触发交接，只发了一次结果补报，worker 在补报引导下完成阶段二并交付（`supervisor.log`）。修复后 5.1 的自动交接恢复正常。

## 6. 计划外情况（待主脑汇总到总览 README）

### E04：Pi 会话模型注册表不提供 context window，Pi 上下文观测不可用

- 发现时间：2026-09-11（同日修复并验证）
- 关联工单：[04](../../issues/04-worker-context-handoff.md)；影响 04 的“Pi 可读取上下文观测值”与阈值触发
- 状态：已修复并验证（待主脑确认是否写入共享 README 并登记为已解决）
- 预期与实际：预期 `bin/pi_context.mjs` 能从 Pi 模型注册表读出 `contextWindow`。实际对 `opencode-go/deepseek-v4.1-flash` 返回 `window: null`，`get_context.py` 报 “Pi model registry has no context window”，Pi 观测全部为不可观测（首次冒烟现场见 `logs/pi-attempt1-unobservable/`）。`pi --list-models` 同模型 `context` 列为 `1M`，说明目录数据存在、仅会话内注册表缺失。
- 影响：修复前 Pi 无法按阈值自动交接，只能靠监督层的结果补报推动；OpenCode 不受影响。
- 处置：`bin/get_context.py` 在模型注册表没有窗口时回退查询 `pi --list-models`（与启动校验同一来源），输出增加 `window_source` 以便区分窗口来源；`--window` 显式覆盖优先并标记 `explicit --window`。真实冒烟窗口 `1048576`、`pct=2.26%`，自动交接恢复；`tests/test_context.py::PiWindowTests` 覆盖回退与尺寸解析。
- 后续工单／计划变更：无新增工单。建议 09 端到端验收在“非默认 provider”场景保留该回退验证。

### E05：阈值低于新会话起始上下文时会连续自动交接（观察，非缺陷）

- 发现时间：2026-09-11
- 关联工单：[04](../../issues/04-worker-context-handoff.md)；影响演示与阈值使用建议
- 状态：已确认现象，处置建议待主脑决定（当前无代码抑制）
- 预期与实际：把 `--handoff-tokens` 设为 1（只为演示）时，新会话读完合同与交接文档后的首个回合已超过阈值，于是每完成一个回合就再次交接：Pi 演示连续交接 7 次，OpenCode 连续 2 次，最终都交付。默认 300K／80% 阈值不会出现该现象（新会话起始约 13K–21K tokens）。
- 影响：仅影响明显低于新会话种子上下文的阈值配置；不违反“连续多次交接仍属于同一工单执行”，也不产生并发写入。风险是主脑若误设极低阈值，工单会被反复交接而进展缓慢。
- 处置与负责人：待主脑决定是否需要在 07 的排程文档中提示阈值下限（例如不低于预期的合同＋交接文档上下文），或在工具层加入抑制策略。工单 04 按验收要求保留可配置阈值，不额外改变触发政策。
- 解决与验证：不需要代码修复；默认阈值与主脑配置的正常使用不受影响。

### 工单 03 的 E02／E03 仍未见汇总

- 证据 README（`../03-opencode-worker-delivery/README.md` 第 6 节）已记录 E02（OpenCode 首条投递丢弃，已修复）与 E03（管理目录 `external_directory` 权限依赖，待决策），但共享总览 README 目前只有 E01。本工单未改动共享 README，提示主脑补记。

## 7. 验收条件核对

- [x] Pi、OpenCode 均能读取可解释的当前上下文观测值，区分累计消耗／有效调用／陈旧样本／不可观测；无法观测不按零用量处理（`session.context` 的 `state/total/window/pct/freshness/source/observed_at`；`test_context_observation_records_interpretable_sample`、`test_unobservable_context_is_an_exception_not_zero_usage`、真实冒烟 `logs/pi/state.json`、`logs/oc/state.json`）。
- [x] 默认 300K tokens 或窗口 80% 触发，阈值可配置；主动交接走同一标准流程（`start --handoff-tokens/--handoff-pct`；`handoff` 子命令与自动触发共用 `attempt_handoff`；5.1、5.2、5.3）。
- [x] 触发后暂停旧业务执行与等待，确认可交互，使用已验证的 skill 调用方式；交接材料包含进展、决定、验证、提交、未提交内容和下一步（`settle_session_for_handoff`；Pi `/skill:handoff`、OpenCode `skill` 工具提示；文档六节结构；`logs/pi/handoffs/handoff-001.md`、`logs/oc/handoffs/handoff-001.md`）。
- [x] 替换前确认文档可读并保存耐久位置；结构缺失允许一次有限补正，不靠字数判定（`validate_handoff_document`、`locate_handoff_document`、一次 correction；`test_invalid_handoff_document_gets_one_correction`、`test_handoff_document_validation_requires_structure`、`test_handoff_document_is_copied_from_os_temp`）。
- [x] 新对话收到原合同与交接文档，保持相同 worktree／branch／provider／model／thinking 并实际续接（`continuation_prompt`；真实冒烟摘要与 `logs/pi-config/session-config.jsonl`、`logs/oc/session-config.jsonl`）。
- [x] 旧会话停止业务写入后新会话才开始工作；新会话启动与投递确认后旧会话才结束，不允许并写（doc 确认后再次 settle → 启动新 tab → 续接确认 → `end_session`；`test_context_threshold_triggers_automatic_handoff`、`test_stop_during_handoff_aborts_without_replacement`）。
- [x] 会话序号递增，观测、等待与异常关联具体会话；旧采样与旧事件不触发新会话重复交接；连续交接仍属同一工单执行（`sessions`／`session_index`／`sample_is_current`／请求 id 去重；`test_multiple_handoffs_stay_one_worker`、`test_stale_sample_guard_ignores_old_sessions`、`test_explicit_handoff_request_is_not_repeated`）。
- [x] 文档生成、观测或替换失败形成可读取待处理异常，保留文档、分支与现场；成功交接只记录进展（`record_handoff_failure`／`mark_context_unobservable`；`test_handoff_document_failure_is_a_pending_exception`、`test_handoff_document_timeout_retains_scene`、`test_replacement_start_failure_retains_scene_and_old_session`、`test_handoff_retry_reuses_valid_document`）。
- [x] 双运行时可控低阈值真实续接演示；确定性模拟覆盖陈旧事件、替换失败、停止与交接竞争（5.1、5.3；`test_stale_*`、`test_replacement_*`、`test_stop_during_handoff_aborts_without_replacement`）。

## 8. 未决与边界

- 工单 05：并发额度、wait-any、ack 不在本次范围；本次事项已带稳定 id、会话关联与 `acked_at` 占位。
- 工单 06：cleanup 不在本次范围。交接会创建新 tab 并保留旧 tab（旧 TUI 退出后为 shell），由后续 cleanup 决定关闭策略；`handoff-old-session-exit-failed` 异常会让主脑看到未能退场的旧会话。
- 工单 07：主脑动态排程与 SKILL.md 未改动。
- E01（Pi 阻塞 UI 不报 blocked）未在本次改变；交接的暂停步骤按“settle 或中断”处理，不依赖 `blocked`。
- 阈值政策（E05）未加抑制；默认阈值下不受影响。
- 观测适配仍依赖 `bin/get_context.py` 的 Pi／OpenCode 路径；Codex 分支保留但不在第一版范围。

## 9. 证据索引

| 路径 | 内容 |
| --- | --- |
| `logs/versions.txt` | Herdr／Pi／OpenCode 版本与集成状态 |
| `logs/deterministic/test-plan-manager.txt`、`test-all.txt` | 44／85 个测试通过 |
| `logs/pi-attempt1-unobservable/` | E04 修复前现场：不可观测样本、异常事项、监督日志、结果补报后交付 |
| `logs/pi/` | Pi 自动阈值冒烟：start、最终 status、state、supervisor 日志、7 份交接文档、结果、git log／status、smoke.txt |
| `logs/pi-proactive/` | Pi 主动交接冒烟：交接请求、state、supervisor 日志、交接文档、结果、最终 status |
| `logs/oc/` | OpenCode 自动阈值冒烟：start、最终 status、state、supervisor 日志、2 份交接文档、结果、DB 会话配置、git log／status、smoke.txt、step1.md |
| `logs/pi-config/session-config.jsonl` | 8 个 Pi 会话的 provider／model／thinking／cwd |
| `logs/oc/session-config.jsonl` | 3 个 OpenCode 会话的 provider／model／variant／agent／cwd |

## 10. 冒烟资源清理记录

- 2026-09-11（本地）：三个冒烟 worker 的 TUI 会话用运行时对应方式退出（Pi `ctrl+c ×2`、OpenCode `ctrl+c`），关闭全部实验 tab；确认 `wJ` workspace 仅剩原有 `wJ:t1`、`wJ:t2`、`wJ:t3`，无遗留实验 agent。
- 移除实验 worktree 与分支 `hpm/w-smoke04-pi-9b62e6`、`hpm/w-smoke04-pi-a5885f`、`hpm/w-smoke04-pi2-5d1f27`、`hpm/w-smoke04-oc-0d12ab`；删除一次性仓库与管理目录 `/tmp/opencode/hpm-exp04-pi`、`/tmp/opencode/hpm-exp04-pi2`、`/tmp/opencode/hpm-exp04-oc`。未操作其他 tab、workspace 或用户会话；证据已复制到本目录。
