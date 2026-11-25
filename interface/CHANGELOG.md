# 变更公告

> 后端（码）改任何前端可见的东西，都在这里加一条。
> 格式固定：**日期 · 类型 · 影响面 · 变更 · 迁移提示**。
> 类型只有四种：`新增` / `修改` / `删除` / `语义变更`。

---

## 2026-11-25 · 新增 · 建立对接文档

- **影响面**：无（首次建立，前端还没开始对接）
- **变更**：新建 `interface/` 目录，含 `README.md`、`API.md`、`CHANGELOG.md`、`FRONTEND-BRIEF.md`
- **迁移**：无

同时公告两件已存在但此前没写下来的事：

1. **枚举 `outcome`** 的完整取值：`accepted` / `rejected` / `escalated` / `budget_exhausted` / `error`
2. **闸门固定四项、顺序不变**：`tests` / `lint` / `api` / `diff`；`all_green` 是唯一判定

---

## 2026-11-25 · 修改 · 响应前端首轮 8 条反馈

**影响面**：`runs/audit.jsonl` 的事件形状（向后兼容：只增字段、统一取值）。
**迁移**：前端可以删掉“多条取值路径”的兼容代码，只读 `run_end` 的固定字段。

| # | 反馈 | 结论 |
|---|---|---|
| 1 | 7 个 run 没有 `run_start` | **已修**：协议机与 runtime 的 `run_start` 都带 `task_id`；runtime 补上了原本缺的 `run_start` |
| 2 | `run_end` 六种形状 | **已修**：统一为 `{outcome, tokens, calls, duration_ms, rounds, reason}` |
| 3 | 没有时长字段 | **已修**：`run_end.duration_ms`（毫秒） |
| 4 | `signature` 39/51 | **已修**：`delivery` 之前发的是数组，改成与协议机一致的 `tests:1|lint:1|api:1|diff:0` 字符串 |
| 5 | `touched` 里出现 `"1640"` | **不是 bug**：模型在执行循环里误建了一个名为 `1640` 的空文件，diff 闸门如实记录。可以不展示 |
| 6 | `contract_hash` 是 JSON 片段 | **已修**：改为真 sha256 前 16 位（形如 `a3f9c2e1b7d40856`） |
| 7 | `run_id` 两种形态 | **已修**：统一为 `20261125T161230-a1b2c3`。⚠️ 时间戳部分是**本地时间**（Asia/Shanghai），事件里的 `ts` 仍是 UTC |
| 8 | `seats` 与 `model_response.model` 不一致 | **语义说明**（不改字段）：`seats.*` 是**座位标签**（配置里的 provider/model），`model_response.model` 是**服务端返回的模型名**，可能不同（如省略日期后缀）。建议前端分两列显示： 「席位」与「实际返回」|

### 新增：补丁内容可读

对应你第 7 节第 4 项（详情再深卡在字段）。现在每次运行会把补丁原文落盘：

```
runs/patches/<run_id>.json
{ "run_id": "...", "files": { "tabulate/__init__.py": "<完整文件内容>" }, "rationale": "..." }
```

`run_end` 里多了一个 `patch_path` 字段指向它。只读文件即可，不需要新接口。
注意 `runs/` 不入库，前端构建时要允许该文件缺失（旧 run 没有）。

## 前端反馈区

> 前端会话如果发现字段不够用、语义不清、拿不到数据，写在这里，不要自己造字段。

### 2026-11-25 · 首轮对接（`web/build-data.py` 读 `tasks/` + `runs/audit.jsonl`）

按方案 B 只读文件，页面已经能出数据。以下是**提质量**的项，不是阻塞项；
每条都附了可复现的计数或 run_id。

| # | 现象 | 证据 | 前端怎么将就 | 建议 |
|---|---|---|---|---|
| 1 | 53 个 run 里有 7 个没有 `run_start` | 其中 4 个连 `gate_report` 也没有 | 任务名从 `gate_report.report.task_id` 反推；都没有的显示 `—` | 所有事件都带 `task_id`，或在 `run_end` 补写 |
| 2 | `run_end` 有 6 种形状 | 见下 | 写多条取值路径 + 兜底 `null` | 见下 |
| 3 | 没有时长字段 | 45/53 能用 `max(ts)-min(ts)` 算出来 | 算不出的显示 `—` | `run_end` 直接给 `duration_ms` |
| 4 | `gate_report.signature` 只有 39/51 条有 | 12 条缺 | 闸门列只画四个色块，不显示 signature | 建议必填 |
| 5 | `diff.metrics.touched` 出现 `"1640"` | run `20261125T140413-1bcc6e` | 照原样不展示 | 疑似把行号当文件名，确认一下 |
| 6 | `run_start.contract_hash` 是截断的 JSON 片段 | 值形如 `{"doD": {"api": ` | 不展示，只当内部键 | 改名为 `contract_head`，或存真 hash |
| 7 | `run_id` 两种形态、两套时间 | `20261125T140357-762097`（内嵌本地时间）与 `run-fa1e88e72b`（无时间） | 统一按 `ts`（UTC）+08:00 换算 | 文档里写明时区与命名约定 |
| 8 | 协议运行里 `seats` 与 `model_response.model` 对不上 | 14 次带 `seats` 的运行中有 3 次：如 `run-93cef6ab27` 座位写 `deepseek-chat`，响应写 `deepseek-flash` | 前端两处都显示，答辩时会被问"到底哪个模型" | 座位里的模型名应与实际调用一致，或说明是"席位标签"|

第 2 条的六种 `run_end` 形状（括号里是条数）：

```
{outcome, budget, rounds}                    32
{outcome, budget, rounds, events}             4
{outcome, budget, rounds, reason}             2
{outcome, backend, calls, tokens}             3
{outcome, reason}                             4
{checks, events}                              3   ← 没有 outcome
```

前端取值顺序：`tokens = run_end.tokens → run_end.budget.tokens → Σ model_response.usage.total`；
`calls = run_end.calls → runtime_run.calls`；`provider/model = run_start → model_response → runtime_run.model`。
只要 `run_end` 统一成 `{outcome, tokens, calls, duration_ms, rounds?}`，上面三条路径都能删掉。

另：`API.md` §6 那 6 个「计划中」的端点，前端**没有**按它开发。
等 HTTP 服务起来，`GET /api/runs` 的返回形状可以直接复用 `web/data/site.json` 的 `runs[]`
（字段含义与派生规则写在 `web/README.md`），省得两边各写一套。
