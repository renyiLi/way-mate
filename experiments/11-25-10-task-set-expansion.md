# 11/25/10 · 任务集扩到 7 条，仍然没有难档

**日期**：2026-11-25（Asia/Shanghai）
**脚本**：`scripts/baseline.py --tasks ... --reps 3`

## 目的

把任务集从 4 条扩到 7 条（新增 3 条来自第二个仓库 more-itertools），并复测档位。

## 新增的三条

| 任务 | 主题 | 修复 | 我的预判 |
|---|---|---|---|
| moreitertools-1342 | `chunked_even` 入参校验 | 5 行 | 易 |
| moreitertools-1311 | `all_unique` 忽略假值 key | 1 行改写 | **难**（以为模型只改表面） |
| moreitertools-1309 | `bucket` 假值 validator 被替换 | 1 行改写 | **难**（同上） |

## 结果

| 任务 | 通过 | 平均 token | 平均耗时 |
|---|---|---|---|
| moreitertools-1342 | **3/3** | 55685 | 1702 ms |
| moreitertools-1311 | **3/3** | 74320 | 2508 ms |
| moreitertools-1309 | **3/3** | 55468 | 1938 ms |

总通过 **9/9**，用时 51 秒。

## 结论

1. **难度预判错了**：两条"假值缺陷"我以为会落在难档，模型一次就写出了
   `if key is None` / `if validator is None`。这类"`or` vs `is None`"的模式，现在的模型很熟。
2. **七条任务的分布**：全部落在易/中，**没有一条 0/3**。
3. **成本仍由文件大小决定**：13311 那条 token 高（74320），因为 `more.py` 更大；
   与任务难度无关，与上下文体积相关——再次印证 11/25/6 的观察。

## 目前的任务集

| 任务 | 仓库 | 通过 | 档位 |
|---|---|---|---|
| tabulate-366 | tabulate | 3/3 | 易 |
| tabulate-386 | tabulate | 3/3 | 易 |
| tabulate-410 | tabulate | 2/3 | 中 |
| tabulate-362 | tabulate | 1/3 | 中 |
| moreitertools-1342 | more-itertools | 3/3 | 易 |
| moreitertools-1311 | more-itertools | 3/3 | 易 |
| moreitertools-1309 | more-itertools | 3/3 | 易 |

## 一个明确的判断

**"单点小修"这类任务已经被当前模型吃透了**，无论来自哪个仓库。
要造出 0/3，必须换任务形态：

| 方向 | 例子 | 为什么可能更难 |
|---|---|---|
| 大改动 | `#1248` numeric_range 一致性（+89/−15） | 需要同时理解多处交互 |
| 跨文件 | 修复涉及 2 个以上模块 | 先要定位到哪个模块 |
| 反直觉缺陷 | 行为看起来对、只是边界错 | 模型容易"改对一半"（如 tabulate-410） |

下一步用 more-itertools 里改动量最大的 `#1248` 试一次，看能不能造出第一条 0/3。

## 复现

```powershell
python scripts/baseline.py --tasks moreitertools-1342,moreitertools-1311,moreitertools-1309 --reps 3
```
