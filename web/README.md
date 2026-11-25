# web/ · 演示外壳

> 前端会话（代号「壳」）的产物目录。只读 `tasks/` 与 `runs/`，不碰后端代码。

## 目录

```
web/
  index.html          工作台主页面（数据驱动）
  style.html          活样式表：颜色、字体、组件各状态
  build-data.py       构建期脚本：读 tasks/ 与 runs/audit.jsonl → data/site.{json,js}
  check.mjs           回归脚本：自起静态服务 + headless Chrome，跑真实交互断言
  tools/serve_web.py  本地预览服务器（只读静态文件）
  data/
    site.json         生成的数据（给工具、论文、人工检查）
    site.js           同一份数据包成 window.__DBW_DATA__（页面直接读，file:// 也能用）
  assets/
    styles.css        全部样式，设计规范见 DESIGN.md
    print.css         打印样式（media="print"，A4）
    app.js            渲染逻辑 + hash 路由
    fonts/            自托管的 Archivo / IBM Plex Mono
  preview/            截图
  DESIGN.md           色板、字体、版式、动效档位、Don'ts
```

## 跑起来

```bash
python web/build-data.py              # 先有数据（读 tasks/ 与 runs/）
python web/tools/serve_web.py         # 起预览；或直接双击 index.html
node web/check.mjs                    # 回归（需要本机 Chrome）
```

改了 `tasks/`、跑过新的 `runs/` 之后，重跑一次 `build-data.py` 再刷新页面。

## 回归脚本

`check.mjs` 自带静态服务 + fixture 注入 + headless Chrome（CDP），零依赖：

```bash
node web/check.mjs                   # 全部用例
node web/check.mjs --filter patch    # 只跑名字里带 patch 的
CHROME_PATH=/path/to/chrome node web/check.mjs
```

覆盖：概览数字、任务详情与时间轴、运行列表筛选与搜索、键盘（`/`、`↑↓`、`End`）、
运行详情闸门与时间线、运行对比、CSV 导出、补丁可读、**数据构建（新字段 + 降级补丁）**、
**预算余量**、**预算超限转朱砂**、缺失补丁文件不崩、打印媒体、未捕获异常。

「数据构建」这条不是跑浏览器：它在临时目录里造一份最小 `tasks/` + `audit.jsonl`（含
`duration_ms`、`tokens`、`calls`，以及一条 `patch_path` 指向不存在文件的 run），
真的执行 `python web/build-data.py --root <tmp>`，再断言产物——
所以它验的是构建脚本里「新字段优先、缺文件降级」的代码路径，不是页面 fixture。

fixture 是把真实 `site.json` 改一处再注入 `index.html`（替换 `data/site.js`），
所以能测"patch_path 指向的文件不存在"这类在真实数据里不好等到的状态。

## 页面结构（hash 路由）

不用框架、不用构建，`file://` 双击也能走完全部页面：

| 路由 | 视图 | 读的是 |
|---|---|---|
| `#/` | 概览：主张、判定板、任务摘要、闸门统计、最近 8 次运行 | `summary`、`gate_stats`、`runs[]`、`tasks[]` |
| `#/tasks` | 任务列表 | `tasks[]` |
| `#/tasks/<id>` | 任务详情：契约字段、契约原文、运行时间轴 + 运行表、按模型汇总 | `tasks[]` + `runs[]` 按 `task_id` 过滤 |
| `#/runs` | 运行列表：按结果筛选（全部 / 交付成立 / 驳回 / 未记录） | `runs[]` |
| `#/runs/<id>` | 运行详情：元信息、闸门逐项判定、预算余量、补丁（有则显示）、事件时间线 | `runs[].gate`、`runs[].budget`、`runs[].patch`、`runs[].timeline` |
| `#/runs/compare?a=<id>&b=<id>` | 两次运行对比：元信息与四类闸门逐行对照 | `runs[]` 里的两条 |

路由不存在时回到概览；`task` / `run` 找不到时给"找不到 + 回列表"的提示，不白屏。

运行列表的状态放在 URL 里，刷新、分享、后退都能还原：

```
#/runs?outcome=rejected            只看驳回
#/runs?q=kimi                      关键字：run_id / 任务 / 模型 / 协议 / 后端
#/runs?q=tabulate&outcome=rejected 两个条件叠加
```

快捷键（只在运行列表）：`/` 聚焦搜索、`Esc` 清空、`↑↓` 在行间移动、`Enter` 打开、`Home/End` 跳到首末行。
任务详情与运行详情里 `c` 直接对比：任务页比最近两次，运行页比上一次同任务运行。

对比不用手填 id：运行详情页顶部有「与上一次同任务运行对比」，自动取这个任务的上一条运行。

## 导出 CSV

运行列表的「导出 CSV」导的是**当前筛选**（关键字 + 结果），固定 17 列：

```
run_id, started_at, ended_at, task_id, provider, model, protocol, backend,
outcome, tokens, calls, duration_ms, all_green, tests, lint, api, diff
```

闸门列取值 `pass` / `fail` / 空（没有判定）。文件带 UTF-8 BOM + CRLF，Excel 直接打开不乱码。
导出在浏览器里生成，不经过任何服务端。

## 预算余量

`run_end.budget` 里的上限画成三条细条：**调用 / token / 耗时**。

- 达到 80% 转安全黄，超过 100% 转朱砂
- 只渲染「有已用量、也有上限」的项；一条都凑不齐就不显示这一节
- 已用量优先取 `budget.*`，缺了用 run 顶层的 `calls` / `tokens` 兜底
- `budget` 是明细，顶层字段才是权威值（后端约定，见 CHANGELOG）

## 补丁

`run_end.patch_path` 指向 `runs/patches/<run_id>.json`，构建时读进来：

```json
{ "files": { "tabulate/__init__.py": "<完整文件内容>" }, "rationale": "…" }
```

- 单文件最多 30k 字符、合计 60k，超出截断并标 `truncated`
- **正文只随快照导出最近 3 份**（合计 240 KB 上限），更早的只留路径与字数（`content_omitted`），
  否则一份 170k 的补丁就能把 `site.json` 撑到 1 MB
- 文件不存在时 `patch.available = false`，页面显示路径与原因，不当成错误
- 旧 run 既没有 `patch_path` 也没有对应文件时 `patch = null`，页面不出现「补丁」一节

## 打印

`assets/print.css` 以 `media="print"` 加载，不参与屏幕渲染：

- A4，四周 14/12mm；隐藏顶栏、页脚、警示胶带、筛选条与面包屑
- 保留闸门灯、色条、印章（`print-color-adjust: exact`），否则纸上全是空白格
- 表格行、闸门行、时间线条目、任务卡都设为 `break-inside: avoid`，不跨页切断
- 详情页多一行打印页眉：对象 id + 数据快照时间 + 数据源，便于归档引用
- 开发时想用屏幕看纸上效果：`index.html?print=1#/runs/<run_id>`（把 print.css 临时改成 `media="all"`）

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
- `runs[].timeline` —— 每个事件一个条目，白名单字段：`ts/event/seat/attempt/phase/model/provider/latency_ms/tokens/degraded/stop_reason/claim_done/all_green/gate_total/gate_failed`。**不含 `raw_text`、prompt、`metrics.tail`**
- `tasks[].runs` / `tasks[].last_run` —— 按 `task_id` 关联审计记录得出的计数
- `summary.runs_with_gate` —— 有闸门报告的运行数（不是 `gate_report` 事件总数）

## 空态

- 没有 `data/site.js`：页面显示"还没有生成数据"+ 生成命令
- 有数据但 `runs/` 为空（新克隆的仓库）：判定板显示"还没有运行记录"，表格给出跑一次的命令
- 单个 run 缺 `run_start` / `run_end` / 闸门报告：对应格子显示 `—`，不补零、不推断
- 筛选无结果：表格里给"换个关键字，或点「全部」"，不显示空表头

## 约定

- UTF-8 无 BOM，LF，中文全角标点，缩进 2 空格，类名 kebab-case
- 不写 `src/dbw/`、`tasks/`、`experiments/`
- 缺字段写进 `interface/CHANGELOG.md` 的「前端反馈区」，不在前端造字段
