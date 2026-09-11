# SMOKE04-OC 阶段一计划

## 目标
最终完成标准：`smoke.txt` 包含以下一行（含末尾换行）并已提交到当前分支 `hpm/w-smoke04-oc-0d12ab`，随后按合同写出 delivered 结果。

```
SMOKE04-OC-CONTENT-7b2e
```

## 已读材料
- 合同 `/tmp/opencode/hpm-exp04-oc/manager/workers/w-smoke04-oc-0d12ab/contract.md`
- 材料 `/tmp/opencode/hpm-exp04-oc/manager/workers/w-smoke04-oc-0d12ab/materials/01-spec.md`

## 阶段一（本轮，已完成）
1. 阅读合同与材料，确认范围仅为当前 worktree。
2. 将本计划写入 `notes/step1.md`。
3. 提交 `notes/step1.md`，提交信息说明这是阶段一的计划。
4. 立即结束本轮：不创建 `smoke.txt`，不写 `result.json`。

## 阶段二（交接后的新会话执行）
1. 读取交接文档，确认 base SHA `5fe5d19223cbd144e2b9255b612a657e08f45662` 与分支 `hpm/w-smoke04-oc-0d12ab`，确认 `smoke.txt` 尚不存在。
2. 创建 `smoke.txt`，内容为 `SMOKE04-OC-CONTENT-7b2e` 加一个换行（即 `printf 'SMOKE04-OC-CONTENT-7b2e\n' > smoke.txt`）。
3. 提交 `smoke.txt` 到当前分支。
4. 运行检查：确认文件内容与换行、`git status` 干净、记录 HEAD SHA。
5. 按合同原子写出 `result.json`（先写临时兄弟文件再 rename），status 为 `delivered`，`head` 为真实分支 HEAD，`artifacts` 包含 `smoke.txt`。

## 约束提醒
- 只写 worktree 内文件和 `result.json`；不改主检出、计划、材料快照、管理目录。
- 不建/删/切换分支或 worktree。
- 不使用交互式提问 UI。
