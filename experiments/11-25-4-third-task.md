# 11/25/4 · 第三条任务（tabulate #386）与提示词路径统一

**日期**：2026-11-25（Asia/Shanghai）
**任务**：[`tasks/tabulate-386`](../tasks/tabulate-386/task.json)
**上游**：astanin/python-tabulate PR #386，基线 `8ca340f5`，修复 4 行

## 目的

1. 验证任务集第三条能否用同一流水线跑通；
2. 顺带修掉一个方法问题：单轮后端与协议机此前各自拼提示词，导致"单轮 vs 2 人"不可比。

## 提示词路径统一（前置修复）

此前：

| 路径 | 用户提示 |
|---|---|
| `delivery.deliver_once` | `json.dumps(task)` |
| `ProtocolRunner._propose_prompt` | 任务 JSON + **完整 doD JSON** + "请给出你的产出" |

协议机多塞了一段验收契约，模型因此更谨慎。**这会让单轮与多轮的对比混入提示词变量。**

修法：新增 `render_propose_prompt(task, contract)` 作为唯一构造点，两处都调它；
并加一条测试断言协议机发出的提案提示与单轮后端**逐字相同**。

## 任务校验

- 旧源码 + 新测试：`2 failed`（`test_asciidoc`、`test_asciidoc_headerless`）
- 加 PR 源码改动：`178 passed, 21 skipped`

## 结果

| 后端 | 调用 | token | 耗时 | 改动 | 结果 |
|---|---|---|---|---|---|
| A（direct + edits） | 1 | 34206 | 1530 ms | 1 文件 / 4 行 | ✅ 四闸门全绿 |

模型给出的理由（节选自 `runs/audit.jsonl`）：

> asciidoc 列说明格式应为 `<align><width>`（如 `<6`），当前生成的是 `<width><align>`（如 `6<`）。

它从失败测试的期望输出里反推出了格式约定，没有靠猜。

## 结论

1. **流水线稳定**：三条任务、三个不同的 PR，`prepare_task` + `run_once` 一次都没改代码。
2. **该任务对 `deepseek-chat` 属"易偏中"**：需要从测试输出反推格式，但改动局限在一处。
3. **提示词统一后，单轮与协议的对比才成立**——后续所有 2 人 / 3 人实验必须在此修复之后。

## 局限

- 每格 1 次；
- 只测了 `deepseek-chat`，未测 flash / kimi / doubao；
- 三条任务全部来自同一个仓库，熟悉该仓库的模型可能有优势，不能外推到其它仓库。

## 任务集现状

| 任务 | 修复规模 | 后端 A | 后端 B |
|---|---|---|---|
| tabulate-366 | 1 行 | ✅ | ✅（10 步 / 19340 token） |
| tabulate-362 | 8 行 | ❌ | ❌（12 步） |
| tabulate-386 | 4 行 | ✅ | 未测 |

## 复现

```powershell
python scripts/prepare_task.py --task tasks/tabulate-386
python scripts/run_once.py --task tasks/tabulate-386 --provider deepseek --model deepseek-chat
```
