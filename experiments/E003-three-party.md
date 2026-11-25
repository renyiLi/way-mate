# E003 · 3 人协议（含裁决座位）

**日期**：2026-11-24（Asia/Shanghai）
**脚本**：`scripts/run_protocol.py`
**任务**：`fixtures/repo_a`（`exact` 已摘掉）

## 目的

在 E002 的基础上加裁决座位，看三方比两方多花多少、是否改变结果。

## 设置

| 项 | 值 |
|---|---|
| 协议 | `3p.single` |
| 提案 | `deepseek/deepseek-chat` |
| 复核 | `ark/kimi-k3-1-pro-261024` |
| 裁决 | `ark/deepseek-v4-1-flash-261017` |
| 轮次上限 | 2 |

## 结果

```
第 1 轮  提案 1 份 · 复核 0 条（丢弃 1）· 阻断 0 · 闸门 全绿

结果    accepted（第 1 轮即全绿）
轮次    1     token 4900     调用 2 次
```

## 结论

**无法判断。** 三条原因：

1. 第 1 轮就通过，**修复循环与裁决座位都没有被触发**——这次运行测的是"一次就对"的情况，不是协议差异；
2. token 4900 与 E002 的 10286 不能直接比：两者轮次不同，且提示词路径不同；
3. 每格仅 1 次。

## 唯一的有效观察

在"一次就对"的任务上，3 人协议的两轮上限是浪费——它连裁决都没用上。
这正好指向成长层 L2 要解决的问题：**按任务难度决定开几个座位**，而不是一律开三方。

## 局限与后续

- 必须先把 `delivery.py` 与 `ProtocolRunner` 的提示词构造统一，否则跨实验不可比（已在 E004 后列为待办）；
- 需要难度更高的任务，让第 1 轮必然失败，才能测到裁决座位的边际贡献。

## 复现

```powershell
python scripts/run_protocol.py --strip-exact --rounds 2 `
  --proposer deepseek/deepseek-chat --reviewer ark/kimi-k3-1-pro-261024 `
  --arbiter ark/deepseek-v4-1-flash-261017
```
