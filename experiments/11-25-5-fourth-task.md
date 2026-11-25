# 11/25/5 · 第四条任务（tabulate #410）与任务梯子成形

**日期**：2026-11-25（Asia/Shanghai）
**任务**：[`tasks/tabulate-410`](../tasks/tabulate-410/task.json)
**上游**：astanin/python-tabulate PR #410，基线 `26ccb780`，修复为删除 10 行 / 新增 4 行

## 目的

继续扩任务集，并把难度拉开：此前 2 条能过、1 条全挂，缺少"部分能做对"的中间地带。

## 任务校验

- 旧源码 + 新测试：`4 failed, 176 passed, 21 skipped`
- 加 PR 源码改动：`180 passed, 21 skipped`

四条失败测试：`test_github`、`test_github_multiline`、`test_github_with_colalign`、
`test_simple_headerless_with_sep_line_with_padding_in_tablefmt`。

## 结果

| 后端 | 调用 | token | 耗时 | 改动 | 结果 |
|---|---|---|---|---|---|
| A（direct + edits） | 1 | 34243 | 1192 ms | 1 文件 / 2 行 | **未通过**（4 条测试过 3 条） |

模型抓到了主问题（github 格式应使用带冒号的分隔行生成器），改了 2 行，
**4 条测试过了 3 条**，`test_github_with_colalign` 或简单格式那条仍红。

## 结论

这条比 11/25/3 更有价值：它落在"改对了一部分"的中间地带。

- 11/25/3（#362）：完全没抓到根因 → 全红
- 11/25/5（#410）：抓到了主问题但没覆盖全部用例 → 3/4 绿

**闸门在这里的价值最明显**：如果只看"测试从 4 红变 1 红"，很容易误判为"基本修好了"。
四类闸门 + 指定测试节点，把"部分正确"精确地判成了"不可交付"。

## 任务集现状（梯子已具雏形）

| 任务 | 修复规模 | 后端 A | 后端 B |
|---|---|---|---|
| tabulate-366 | 1 行 | ✅ | ✅ |
| tabulate-386 | 4 行 | ✅ | 未测 |
| tabulate-410 | 14 行 | ❌（3/4 用例绿） | 未测 |
| tabulate-362 | 8 行 | ❌ | ❌ |

2 条能过、2 条不能过，其中 1 条是"部分对"。可用于：

- 单轮通过率基线（易档应接近 100%，难档应接近 0）；
- 2 人 / 3 人协议的救援实验（需要难档样本）。

## 局限

- 每格 1 次；
- 四条任务全部来自 tabulate 一个仓库，难度分布不代表真实分布；
- 未测 kimi / doubao / flash。

## 复现

```powershell
python scripts/prepare_task.py --task tasks/tabulate-410
python scripts/run_once.py --task tasks/tabulate-410 --provider deepseek --model deepseek-chat
```
