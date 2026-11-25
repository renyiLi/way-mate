# 后端接口面

> 状态图例：**已实现** = 代码里存在且能跑；**计划中** = 只有设计，别当它已存在。

## 0. 先说清楚现状

**后端目前没有 HTTP 服务。** 它是一个 CLI + Python 库：

| 入口 | 作用 | 状态 |
|---|---|---|
| `scripts/prepare_task.py` | 按配方准备一个真实任务工作区 | 已实现 |
| `scripts/run_once.py` | 单轮交付（后端 A） | 已实现 |
| `scripts/run_runtime.py` | 多步执行（后端 B） | 已实现 |
| `scripts/run_protocol.py` | 2 人 / 3 人协议 | 已实现 |
| `scripts/experiment_exact.py` | 对照实验 | 已实现 |
| `runs/audit.jsonl` | 全部运行的结构化日志 | 已实现 |

所以前端**现在**能做的只有两件事：

1. 读 `runs/audit.jsonl`（JSONL，一行一个事件）来渲染运行记录；
2. 读 `tasks/<id>/task.json` 与 `contract.yaml` 来渲染任务与契约。

要等后端起 HTTP 服务，才谈得上"点按钮跑一次"。§6 是那份接口的草案。

## 1. 闸门结果（渲染判定的核心）

**已实现** · 来源：`src/dbw/gates.py`

```json
{
  "task_id": "tabulate-366",
  "all_green": false,
  "results": [
    { "name": "tests", "ok": false,
      "detail": "`pytest ...` 退出码 1；失败 1，通过 4",
      "metrics": { "exit_code": 1, "counts": {"failed": 1, "passed": 4}, "tail": "…" } },
    { "name": "lint", "ok": true,
      "detail": "问题总数 4（基线 4，新增 0，上限 0）",
      "metrics": { "count": 4, "baseline": 4, "new": 0, "max_new": 0 } },
    { "name": "api",  "ok": true, "detail": "契约未冻结接口", "metrics": {} },
    { "name": "diff", "ok": false,
      "detail": "改动 1 个文件 / 5 行（上限 3 / 40）",
      "metrics": { "files": 1, "lines": 5, "touched": ["tabulate/__init__.py"], "forbidden": [] } }
  ]
}
```

渲染要点：

- `results` 固定四项、顺序固定：`tests / lint / api / diff`；
- `all_green` 是**唯一**的"可交付"判定，前端不要自己算；
- `metrics` 是给详情面板用的，字段随闸门类型不同，不要写死类型。

## 2. 一次交付的结果

**已实现** · 来源：`src/dbw/delivery.py`

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "outcome": "accepted",
  "reason": "闸门全绿",
  "files": ["tabulate/__init__.py"],
  "rationale": "github 格式未列入 multiline_formats…",
  "tokens": 33529,
  "latency_ms": 2051,
  "degraded": false,
  "run_id": "20261125T115421-f0efdd",
  "contract_hash": "…",
  "error": "",
  "gate": { "…见上…" }
}
```

`outcome` 取值：`accepted` / `rejected` / `escalated` / `budget_exhausted` / `error`。
**注意**：`rejected` 里包含了"提案不合法"和"闸门没过"两种情况，用 `reason` 区分。

## 3. 协议运行（2 人 / 3 人）

**已实现** · 来源：`src/dbw/protocol/machine.py`

```json
{
  "run_id": "…",
  "interface": "authoring",
  "protocol": "2p.single",
  "seats": {
    "proposer": "deepseek/deepseek-chat",
    "reviewer": "ark/kimi-k3-1-pro-261024",
    "arbiter": null,
    "proposer_b": null,
    "protocol": "2p.single",
    "participants": ["deepseek/deepseek-chat", "ark/kimi-k3-1-pro-261024"]
  },
  "self_played": false,
  "outcome": "accepted",
  "reason": "第 2 轮闸门全绿",
  "budget": { "calls": 4, "max_calls": 40, "tokens": 10286, "max_tokens": 200000, "wall_s": 61.2, "max_wall_s": 1800.0 },
  "rounds": [
    { "index": 1, "proposal_count": 1, "findings": [], "dropped": ["F2 没有证据"],
      "blockers_after_arbitration": 0, "gate": { "…" }, "gate_signature": "tests:0|lint:1|api:1|diff:1", "note": "" }
  ]
}
```

`protocol` 取值：`2p.single` / `3p.single` / `3p.dual`。
`self_played: true` 表示同一个模型坐了多个座位——**前端要标出来**，否则实验数据会被误读。

## 4. 审计事件

**已实现** · 文件：`runs/audit.jsonl`（每行一个 JSON，append-only，**不入库**）

| event | 关键字段 |
|---|---|
| `run_start` | `run_id, task_id, git_sha, provider, model, contract_hash, capabilities` |
| `model_request` | `seat, provider, phase, prompt_chars` |
| `model_response` | `seat, model, usage{...}, latency_ms, degraded, request_digest, raw_digest, raw_text` |
| `gate_report` | `attempt, report{…§1…}, signature` |
| `runtime_run` | `backend, stop_reason, claim_done, calls, tokens, files, transcript[]` |
| `run_end` | `outcome, reason, budget, rounds` |

前端渲染"一次运行的完整时间线"，就是按 `run_id` 把这几类事件按 `ts` 排序。

## 5. 任务与契约（静态文件，可直接读）

**已实现** · `tasks/<id>/task.json`

```json
{
  "id": "tabulate-366",
  "repo": "https://github.com/astanin/python-tabulate",
  "base_sha": "537d7b03…",
  "title": "github 格式的多行单元格渲染错乱",
  "statement": "…给模型看的任务描述…",
  "target_test": "test/test_output.py::test_github_multiline",
  "target_files": ["tabulate/__init__.py"],
  "tier": "easy",
  "verified": { "red_without_fix": "1 failed, 175 passed", "green_with_fix": "176 passed" }
}
```

**已实现** · `tasks/<id>/contract.yaml`

```yaml
task_id: tabulate-366
repo: .
doD:
  tests: [{cmd: "python -m pytest test/test_output.py::test_github_multiline -q", timeout_s: 120}]
  lint: {tool: ruff, max_new: 0}
  api: {frozen: []}
  exact: [{exception: ValueError, contains: "missing '='", source: "tests/test_parser.py:25", origin: auto}]
  diff: {max_files: 3, max_lines: 40, forbid: [test/]}
loop: {repair_rounds: 3, stop_on: all_green, on_exhausted: escalate_to_human}
```

## 6. 计划中的 HTTP 接口（**尚未实现**）

状态全部为 `计划中`。字段可能变，前端只能按"预留"处理，不能当已实现。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/tasks` | 任务列表（读 `tasks/*/task.json` 汇总） |
| GET | `/api/tasks/{id}` | 任务详情（task.json + contract.yaml） |
| POST | `/api/runs` | 起一次交付 `{task_id, provider, model, backend, budget}` |
| GET | `/api/runs/{run_id}` | 运行详情（§2 + §3 + §4 合并） |
| GET | `/api/runs` | 运行历史（从 audit.jsonl 聚合） |
| GET | `/api/runs/{run_id}/stream` | 运行中的事件流（SSE） |

起服务这件事本身还没做。前端若现在就要联调，只能读文件，或者由后端临时提供静态 JSON。

## 7. 前端可以依赖的稳定性承诺

| 承诺 | 说明 |
|---|---|
| 闸门名称固定 | `tests / lint / api / diff`，顺序不变 |
| `all_green` 是唯一判定 | 前端不得自行推断"是否可交付" |
| `outcome` 枚举稳定 | 新增值会在 `CHANGELOG.md` 提前公告 |
| 审计事件名稳定 | 同上 |
| `run_id` 全局唯一 | 可作前端的主键 |

不承诺的：`metrics` 内部字段、`reason` 的文案、`rationale` 的长度——这些是给人看的，会变。
