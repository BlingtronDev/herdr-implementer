# 08-R1: 稳定陈旧上下文样本测试

**Status:** integrated（2026-09-13 核实：随 08 进入 main，项目提交 `759d721`；副本修复提交为 `93c02f7`。）

**Blocked by:** 07（现有工具和测试已集成）；本项是 08 验证中 E11 引出的必要补充。

## 目标

诊断并修复 `tests/test_plan_manager.py::test_stale_context_sample_never_triggers_a_handoff` 的 `sessions[0].context is None` 失败，保持“陈旧采样即使超过阈值也不触发交接”的有效断言。

## 范围与验收

- 先复现：`python3 -m pytest tests/test_plan_manager.py::test_stale_context_sample_never_triggers_a_handoff -q`；完整输出在 08 证据目录 `logs/checks/test-all.json`，112 passed / 1 failed。
- 区分三个解释：交付先于采样、适配器没输出样本、结果收集覆盖样本。用受控实验或时序证据确认根因。
- 优先仅修正测试夹具/同步；不要用放宽断言、盲目增加固定 sleep 或修改生产交付语义来掩盖问题。观察陈旧样本时 worker 必须仍在执行，确认未触发交接，再按测试意图停止或完成。
- 尽量复用已有慢 worker/状态等待机制，不添加通用测试框架。
- 运行相关上下文单测，并对修复测试重复验证；报告实际次数与结果。
- 在分配的一次性仓库 worktree 中提交最小修复；主脑审阅交付后将补丁纳入 08 交付工作区。完整项目检查由主脑组织。

## 权限与材料

仅修改分配 worktree 的测试；生产行为若确实有缺陷，先报告证据及必要修复范围。原项目 checkout 和共享工单为只读。遵循 worker 合同，结果报告包括根因、前后测试输出和修改 HEAD。

## 结果

- 根因：快速模拟交付在首次采样前转为 idle，监督进程直接收集结果并退出，未产生 context；延迟交付和 slow 对照证明适配器正常、结果收集不覆盖已有样本。
- 修复：仅 `tests/test_plan_manager.py`，使用既有 slow worker；等待 stale 样本及同会话后续采样，确认仍 working、无结果、无交接，最后显式 stop。没有降低原断言或修改生产代码。
- 验证：修复前 4 次有 2 次失败；修复后单测连续 5/5 通过，上下文相关 7 passed，模块 70 passed，全量 **113 passed in 310.20s**。故意去掉 stale 保护的变异副本被修复测试捕获。
- 主脑审阅并原样纳入补丁；字节一致性核对及本地单测通过（4.80 秒）。完整输出、原生配置、交付与补丁：[08 证据 logs/test-repair](../evidence/08-integration-repair-and-closeout/logs/test-repair/)。
- Worker `w08-stale-test-fix` 已停止并保留副本分支/worktree；当前项目没有声称已集成该提交。
