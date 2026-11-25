# web/ · 演示外壳

> 前端会话（代号「壳」）的产物目录。只读 `tasks/` 与 `runs/`，不碰后端代码。

## 目录

```
web/
  index.html          工作台主页面（数据驱动）
  style.html          活样式表：颜色、字体、组件各状态
  build-data.py       构建期脚本：读 tasks/ 与 runs/audit.jsonl → data/site.{json,js}
  data/
    site.json         生成的数据（给工具、论文、人工检查）
    site.js           同一份数据包成 window.__DBW_DATA__（页面直接读，file:// 也能用）
  assets/
    styles.css        全部样式，设计规范见 DESIGN.md
    app.js            渲染逻辑：把数据画成页面
    fonts/            自托管的 Archivo / IBM Plex Mono
  preview/            截图
  DESIGN.md           色板、字体、版式、动效档位、Don'ts
```

## 跑起来

```bash
python web/build-data.py      # 先有数据（读 tasks/ 与 runs/）
python scripts/serve_web.py   # 起预览；或直接双击 index.html
```

改了 `tasks/`、跑过新的 `runs/` 之后，重跑一次 `build-data.py` 再刷新页面。

## 数据从哪来（方案 B）

后端没有 HTTP 服务（`interface/API.md` §0），所以页面不联网、不 fetch，也不按
`API.md` §6 那 6 个「计划中」的端点开发。数据在**构建期**由 `build-data.py` 生成：

| 输出字段 | 来源 | 说明 |
|---|---|---|
| `tasks[]` | `tasks/*/task.json` | 只取 id / title / repo / tier / statement / target_test / target_files / forbidden / verified / PR |
| `tasks[].contract` | `tasks/*/contract.yaml` | 解析后的 YAML + 原文，供折叠查看 |
| `runs[]` | `runs/audit.jsonl` | 按 `run_id` 聚合；每行一个事件，缺哪类事件就留空，不猜 |
| `runs[].gate` | `gate_report.report` | 只用 API.md §1 的结构；`metrics.tail` 丢弃（体积大、前端用不到） |
| `gate_stats[]` | 全部 `gate_report` | 每个闸门被判定多少次、失败多少次 |
| `summary` | 聚合 | 任务数、运行数、结果分布、token 合计、模型清单 |

页面**只用** API.md §7 承诺的稳定面：闸门固定四项与顺序、`all_green` 是唯一判定、
`outcome` 枚举、审计事件名、`run_id` 主键。`detail` / `reason` 这类"给人看、会变"的
文本只做展示，不参与任何判断。

### 派生字段（原文里没有，脚本算出来的）

这些字段不是后端承诺的接口字段，改动时只影响页面：

- `runs[].started_at` / `ended_at` —— 取该 run 全部事件的 `ts` 最小/最大值，转成 +08:00
- `runs[].duration_ms` —— 仅当 `run_start` 与 `run_end` 都存在时才算，否则 `null`（不编数字）
- `runs[].tokens` —— `run_end.tokens` → `run_end.budget.tokens` → `model_response.usage.total` 求和
- `tasks[].runs` / `tasks[].last_run` —— 按 `task_id` 关联审计记录得出的计数
- `summary.runs_with_gate` —— 有闸门报告的运行数（不是 `gate_report` 事件总数）

## 空态

- 没有 `data/site.js`：页面显示"还没有生成数据"+ 生成命令
- 有数据但 `runs/` 为空（新克隆的仓库）：判定板显示"还没有运行记录"，表格给出跑一次的命令
- 单个 run 缺 `run_start` / `run_end` / 闸门报告：对应格子显示 `—`，不补零、不推断

## 约定

- UTF-8 无 BOM，LF，中文全角标点，缩进 2 空格，类名 kebab-case
- 不写 `src/dbw/`、`tasks/`、`experiments/`
- 缺字段写进 `interface/CHANGELOG.md` 的「前端反馈区」，不在前端造字段
