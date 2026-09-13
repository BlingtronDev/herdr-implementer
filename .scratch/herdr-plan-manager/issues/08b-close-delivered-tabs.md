# 08-R2: 正常交付后自动关闭 worker tab

**Status:** integrated（2026-09-13 核实：随 08 进入 main，项目提交 `759d721`；副本修复提交为 `6d0a7e0`，真实双运行时自动关闭及成果保留证据已纳入。）

**Blocked by:** 07、08-R1（当前工作区实现与测试基线可用；随 08 一起交付）

## 用户确认（2026-09-13）

用户指出 worker 完成后 Herdr tab 仍存在，并选择：正常交付后自动关闭；有效 delivered 结果已保存、会话停止业务且无未保存内容后，自动关闭该 worker 的 tab；保留分支、worktree 和结果。失败或异常现场继续保留。

## 已定位的缺口

监督进程在 settled 后记录结果就退出；`stop_registered_sessions` 明确不关闭 TUI；tab 关闭仅在 `cleanup` 中，后者还删除 worktree。这使完成的 tab 长期滞留，终端释放与磁盘清理被不必要地绑定。

## 范围与验收

- 对 Pi／OpenCode 的有效 `delivered`，先耐久记录交付，再确认业务停止、安全退出 TUI 并关闭其登记 tab；只有 idle/done、没有结果或结果无效时不得自动关闭。
- 不依赖 ack／成果集成／worktree 清理才能释放已完成终端；自动关闭不改变 delivered、ack、integration 的含义。
- 自动关闭仅作用于该 worker 拥有的已登记终端。若 tab/pane 中已出现其他占用者或额外未登记 pane，不关闭他人资源。运行中的旧／新交接会话不得被误关。
- 采用保守可判断的条件：有未提交／未跟踪内容、Git 状态不能确认、仍有业务写入或退出失败时保留 tab，状态可见地说明原因。关闭失败保留已交付成果；不可把交付改为业务失败或无限重试。
- 分支、worktree、结果、日志、材料及未提交文件均保留；清理磁盘仍需明确 cleanup 决定。
- 停止失败或异常工单保留终端。已交付且监督已退出的旧记录可以通过再次 `stop` 复用同一关闭路径，便于处理本轮 7 个遗留 worker；避免仅为此引入另一套关闭协议。
- 持久记录终端关闭／保留原因，可从 status 查询；遵守当前单写入者和 supervisor/stop 协调方式。重复 stop／已关闭 tab 应幂等，正常关闭不得产生伪造的 agent-exited/supervisor-missing。
- 确定性验证至少覆盖双运行时成功交付自动关闭、结果先到而仍 working 时不提前关闭、无有效结果及失败保留、脏 worktree 保留、关闭失败／外来占用保留、重复 stop 和后续 cleanup 仍能工作。
- 在隔离 worktree 实现并提交最小补丁，运行相关测试与完整测试；更新当前操作文档，历史冒烟记录仍按当时行为保留。主脑使用新实现完成真实双运行时冒烟和遗留 tab 处理。

## 材料与权限

本工单依据新重建方案第 10 节“正常结束且确认无未保存工作时可以关闭会话”，不得加载旧固定批次流程作为建设依据。使用已确认 Pi `opencode-go / deepseek-v4.1-flash / max` 配置。仅修改所分配一次性项目副本内的 bin/tests/prompts/docs；共享工单、原项目 checkout 和真实旧 worker 现场由主脑维护。遇到不可推断的行为取舍写入 needs-decision 结果。

## 验证结果

- Worker 全量 144 passed；释放/保留/竞态/cleanup 专项 38 passed。审查发现的严格查询、退出后复查、逐 tab 复查、stop 单写入者与异常占用响应问题均已修正并覆盖。
- 新 Pi 与 OpenCode worker 均在正常交付后自动关闭 tab，无需 ack、stop、集成或 cleanup；实际 provider/model/thinking/cwd 一致，分支、worktree、结果保留。
- 本轮 7 个旧已交付 worker 经重复 stop 关闭 tab，HEAD、worktree 状态与结果哈希不变，重复 stop 幂等；实现 worker 的 tab 也已关闭。
- 当前 5 个变更文件与已验证副本逐字节相同，复用完整测试证据；Python 语法与 git diff 检查通过。
- 完整证据及状态字段：[08-R2 证据索引](../evidence/08b-close-delivered-tabs/README.md)。
