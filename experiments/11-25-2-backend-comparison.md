# 11/25/2 · 后端 A 与后端 B 在同一真实任务上的对比

**日期**：2026-11-25（Asia/Shanghai）
**任务**：[`tasks/tabulate-366`](../tasks/tabulate-366/task.json)（tabulate #366，正确修复 1 行）
**脚本**：`scripts/run_once.py`（后端 A）、`scripts/run_runtime.py`（后端 B）

## 目的

补上 11/25/1 遗留的疑点：执行循环（后端 B）加上 `edit` 块之后能不能收敛。
顺带比较两种后端在同一任务上的成本。

之前的假设是"循环贵 10–50 倍"，需要实测检验。

## 设置

| 项 | 后端 A（direct） | 后端 B（runtime） |
|---|---|---|
| 形态 | 一次调用产出全部改动 | 多步：bash 探查 + `edit` 块 + 自测 |
| 提示上下文 | 任务 + 失败输出 + **目标文件全文** | 任务 + 失败输出（**不给目标文件**） |
| 步数上限 | — | 12 |
| 模型 | `deepseek/deepseek-chat` | 同 |
| 编辑格式 | SEARCH/REPLACE（edits） | SEARCH/REPLACE（`edit:` 块） |

## 结果

| 指标 | A | B |
|---|---|---|
| 结论 | 交付成立 | 交付成立 |
| 闸门 | 四类全绿，1 文件 / 1 行 | 相同 |
| 模型调用 | 1 次 | 10 次 |
| token | 33529 | **19340（−42%）** |
| 是否需告知目标文件 | **需要** | 不需要（自己 grep 出来的） |

后端 B 的轨迹（10 步）：

```
1   ls && grep -n "github" tabulate.py
2   ls tabulate && grep -rn "github" tabulate/
4   sed -n 495,530p tabulate/__init__.py
5   grep -n "multiline" tabulate/__init__.py
6   sed -n 715,760p tabulate/__init__.py      ← 找到 multiline_formats
7   edit:tabulate/__init__.py                 ← 直接改那一行
8   pytest 单条测试
9   pytest 全量测试
10  echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
终止 submitted · 改动 1 个文件
```

## 结论

1. **执行循环 + `edit` 块可以收敛**：10 步内定位、修改、自测、提交，未再出现 11/25/1 里"只读不写"的死循环。
   关键差异是有了 `edit` 块——之前只能整份 `write`，模型凑不齐内容就一直在读。
2. **"循环一定更贵"的假设不成立**：本任务上 B 比 A 省 42% token。
   原因是 A 必须把 1000 行文件塞进上下文，而 B 每次只读几十行。
3. **B 不依赖"告知目标文件"**：A 需要 recipe 里给 `target_files`，这等于先验提示；B 自己 grep 出来，更接近真实使用。

## 局限

1. 每格仅 1 次，未重复；
2. 任务极简单（1 行修复），B 的优势可能随任务变复杂而收窄甚至反转；
3. 未统计墙钟时间——B 有 10 次串行调用，实际耗时明显长于 A。

## 后续要测的

- 每格 ≥3 次；
- 加一个需要改多个文件的难任务，检验 A/B 的成本反转点；
- 记录墙钟时间，作为"省 token 但更慢"的代价。

## 复现

```powershell
python scripts/prepare_task.py --task tasks/tabulate-366 --no-verify
python scripts/run_once.py    --task tasks/tabulate-366 --provider deepseek --model deepseek-chat
python scripts/run_runtime.py --task tasks/tabulate-366 --provider deepseek --model deepseek-chat --budget 12
```
