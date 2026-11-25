# 11/25/11 · 第一条 0/3 难档任务（more-itertools #1248）

**日期**：2026-11-25（Asia/Shanghai）
**任务**：[`tasks/moreitertools-1248`](../tasks/moreitertools-1248/task.json)
**上游**：more-itertools PR #1248，基线 `30ada7dd`，修复 38 增 / 18 删，涉及 4 处

## 目的

前七条任务全部是"单点小修"，模型 3/3 或 2/3 通过。按 11/25/10 的判断换形态：
选 more-itertools 里改动量最大的候选（+89/−15），看能否造出 0/3。

## 任务

`numeric_range` 在步长无法精确表示时（如浮点 0.1），
`__contains__` 与 `len()` 的结论和它实际迭代出的元素对不上。

校验：旧源码 `2 failed, 592 passed`；加修复 `594 passed`。

## 结果

| 次 | 通过 | token | 耗时 | 失败闸门 |
|---|---|---|---|---|
| 1 | ❌ | — | — | tests |
| 2 | ❌ | — | — | tests |
| 3 | ❌ | — | — | tests |
| 合计 | **0/3** | 55535（均值） | 3344 ms | tests |

首轮的具体失败：模型改了 **19 行**（`__contains__` 改用 divmod、`__len__` 调整除法），
两条测试**仍然红**——属于典型的"改对一半"。

## 结论

1. **第一条难档任务成立**（0/3），任务集终于有了完整梯形。
2. **难度与改动量相关**：前七条修复 1–14 行，这条 56 行、4 处交互，模型抓不全。
3. **耗时也翻倍**（3344 ms vs 前七条 1500–2500 ms），与 token 无关（token 反而更低，因为只发 `more.py` 的一小部分？实际是该文件已被前面任务加载过缓存）。

## 为什么这条重要

此前所有"协作实验"都没有意义——任务太简单，单轮就能过，多轮无从体现。

有了 0/3 的样本，"2 人 / 3 人协议能不能救回单轮做不出来的任务"才是一个真问题。

## 任务集现状（8 条）

| 任务 | 通过 | 档位 |
|---|---|---|
| tabulate-366 | 3/3 | 易 |
| tabulate-386 | 3/3 | 易 |
| moreitertools-1342 | 3/3 | 易 |
| moreitertools-1311 | 3/3 | 易 |
| moreitertools-1309 | 3/3 | 易 |
| tabulate-410 | 2/3 | 中 |
| tabulate-362 | 1/3 | 中 |
| **moreitertools-1248** | **0/3** | **难** |

## 复现

```powershell
python scripts/prepare_task.py --task tasks/moreitertools-1248
python scripts/baseline.py --tasks moreitertools-1248 --reps 3
```
