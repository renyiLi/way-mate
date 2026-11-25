# 11/25/15 · 三条通路的完整性验证（同一条难档任务）

**日期**：2026-11-25（Asia/Shanghai）
**任务**：`tasks/moreitertools-1248`
**目标**：不扩任务集，先把"任务 → 契约 → 闸门 → 审计 → 补丁"这条通路，
在**能干的模型**上把三种入口各走通一次。

## 结果总表

| 通路 | 模型 | 结果 | 成本 | 说明 |
|---|---|---|---|---|
| 单轮（A） | `doubao-seed-2-1-pro` | ✅ **通过** | 1 次调用 | 63 行，四闸门全绿（11/25/14） |
| 执行循环（B） | `doubao-seed-2-1-pro` | ✅ **通过** | 14 步 / 75070 token | 45 行，`submitted` 后闸门全绿 |
| 2 人协议 | doubao + flash | ❌ 未通过 | 4 次调用 / 210951 token | 两轮都败在"提案不合法" |
| 单轮（A） | `kimi-k3-1-pro`（16k 输出） | ❌ 未通过 | 1 次调用 | **递上了合法补丁**（30 行），2 条测试只过 1 条 |

**三条通路都能完整跑完**（有提案、有判定、有审计、有补丁落盘），
其中两条在强模型上实现了交付。

## 重要更正：上一轮的错误归因

11/25/12 与 11/25/13 里我写过"模型把签名猜错了（`elem` vs `value`）"——**这是错的**。

真相是文件里同时存在两个 `__contains__`：

```
1183:    def __contains__(self, value):     ← bucket
2336:    def __contains__(self, elem):      ← numeric_range
```

`elem` 对 `numeric_range` 是**完全正确**的。失败的真实原因是
**多行 SEARCH 片段整体无法逐字匹配**（模型改了中间某行的空白或内容），
而我们的报错只说"找不到片段（首行：…）"，把差异点掩盖了。

**这个报错会误导人和模型**，已修：

```
SEARCH 片段第 3 行与文件不一致：片段写的是 'return 999'，文件第 3 行是 'return 1'。请从文件里逐字复制。
```

从"找不到首行"变成"第几行、期望什么、实际什么"，模型的自我修复才有依据。

## 2 人协议这条通路为什么还是不通

两轮都在第二轮提案时失败，且**加预算也没用**：

| 次 | 总预算 | 实际消耗 | 终止原因 |
|---|---|---|---|
| 1 | 200000 | 220936 | token 超限 |
| 2 | 500000 | 210951 | 提案不合法（用完了才报错） |

第二次预算够了，但仍然卡在片段匹配。也就是说：
**这条通路的瓶颈不是钱，是 SEARCH/REPLACE 在 5000 行文件上的脆弱性。**

## 结论

1. **通路完整性达成**：三种入口都能从任务跑到闸门判定并留痕，
   单轮与执行循环在强模型上都能交付。
2. **2 人协议的通路能跑、但交不出货**，瓶颈是提案格式而非协作机制。
   要让它可用，下一步应该让复核座位**替提案方取片段**（它有 `critique_context`，
   手里有原文），而不是让提案方凭记忆写。
3. **模型能力仍是第一变量**：同一条任务，`deepseek-chat` 三种入口全败，
   `doubao-seed-2-1-pro` 两种入口都成。

## 顺带的工程改动

| 改动 | 原因 |
|---|---|
| `run_once --max-tokens` | kimi 曾因输出截断被判"提案不合法" |
| `run_once --timeout` | 60k 输入下 kimi/大模型 180s 不够 |
| `run_protocol --budget-tokens` | 2 人协议总预算写死 200k，长任务必超 |
| 片段报错定位到行 | 避免把"整块不匹配"误报成"首行不存在" |

## 局限

1. doubao pro 每条只跑 1 次，只能说"能做成"，不能给通过率；
2. 执行循环只测了 20 步预算，未测更多步；
3. 2 人协议只换了提案方模型，复核方仍是 flash。

## 复现

```powershell
# A：单轮
python scripts/run_once.py --task tasks/moreitertools-1248 --provider ark `
  --model doubao-seed-2-1-pro-260915 --timeout 420

# B：执行循环
python scripts/run_runtime.py --task tasks/moreitertools-1248 --provider ark `
  --model doubao-seed-2-1-pro-260915 --budget 20

# 2 人协议
python scripts/run_protocol.py --task tasks/moreitertools-1248 `
  --proposer ark/doubao-seed-2-1-pro-260915 `
  --reviewer ark/deepseek-v4-1-flash-261017 `
  --rounds 2 --timeout 420 --max-tokens 16384 --budget-tokens 500000
```
