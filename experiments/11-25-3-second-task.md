# 11/25/3 · 第二条任务（tabulate #362）与两个后端的失败

**日期**：2026-11-25（Asia/Shanghai）
**任务**：[`tasks/tabulate-362`](../tasks/tabulate-362/task.json)
**上游**：astanin/python-tabulate PR #362，基线 `537d7b03`，正确修复为 `casted_cell = str(cell)`（1 行新增 / 7 行删除）

## 目的

任务集扩到第二条，验证流水线能否复用；顺带看难度上升后两个后端的表现。

## 任务校验

- 旧源码 + 新测试：`1 failed, 10 passed, 3 skipped`（`AttributeError: 'bool' object has no attribute 'splitlines'`）
- 加 PR 源码改动：`11 passed, 3 skipped`

## 结果

| 后端 | 调用 | token | 改动 | 结果 |
|---|---|---|---|---|
| A（direct + edits） | 1 | 33516 | 1 文件 / 5 行 | **未通过**（tests 红） |
| B（runtime + edit 块） | 12 | 19084 | 1 个空文件 `1640` | **未通过**（tests 红） |

### A 的失败方式

模型没有改根因，而是把原表达式重排了一遍，再补一句 `casted_cell = str(casted_cell)`：

```python
casted_cell = (
    str(cell)
    if _isnumber(cell)
    else _type(cell, numparse)(cell)     # ← 异常仍在这里抛
)
casted_cell = str(casted_cell)           # ← 在异常之后，永远执行不到
```

实际报错仍是 `TypeError: NoneType takes no arguments`，因为 `_type(None, ...)` 返回 `NoneType`，随后被当成构造器调用。

**失败模式从 11/25/1 的"写不完"变成了"改错地方"**——编辑格式解决了前者，解决不了后者。

### B 的失败方式

12 步里 6 步是 `grep -n "def _type" -A 40 ...` 这类阅读，始终没输出 `edit` 块。
另外它在工作区留下了一个空文件 `1640`（大概率是某个重定向写错），被 diff 闸门算作 1 个改动。

## 结论

1. **流水线可复用**：同一套 `prepare_task` + `run_once` 换一个 recipe 就能跑第二条任务，没有改代码。
2. **该任务对当前模型属"难档"**：两个后端都失败，且失败原因不同（改错地方 / 只读不写）。
3. **A 的能力边界清晰了**：它能正确处理"已知目标文件 + 单点小改"，不能处理"需要重新推理局部逻辑"的修复。

## 局限

- 每格 1 次；
- B 的预算只有 12 步，第 12 步仍在阅读，不能断定"再多几步也修不好"；
- 未测其它模型（kimi / doubao）。

## 后续

- B 提到 20 步复测，确认是"预算不足"还是"能力不足"；
- 把该任务作为**难档样本**，留给 2 人 / 3 人协议实验——单轮修不好、多轮可能修好，正是协作实验需要的素材。

## 复现

```powershell
python scripts/prepare_task.py --task tasks/tabulate-362
python scripts/run_once.py    --task tasks/tabulate-362 --provider deepseek --model deepseek-chat
python scripts/run_runtime.py --task tasks/tabulate-362 --provider deepseek --model deepseek-chat --budget 12
```
