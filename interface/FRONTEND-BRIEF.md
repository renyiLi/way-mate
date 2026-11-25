# 前端会话指令

> 给"前端（壳）"这个会话的操作说明。开工前按顺序读完，再动手。

## 1. 开工顺序

```
COLLABORATION.md         ← 三方职责与边界（先读这个）
interface/README.md      ← 规则：谁写什么、编码要求
interface/API.md         ← 后端现在有什么、没有什么
interface/CHANGELOG.md   ← 有没有影响你的变更，以及「前端反馈区」
```

三份都读完再改代码。**`API.md` 里标"计划中"的接口不能当已实现用。**

## 2. 你的边界

| 可以做 | 不可以做 |
|---|---|
| 改 `web/` 下的一切 | 改 `src/dbw/`（判定逻辑归后端） |
| 读 `tasks/`、`runs/` | 往 `tasks/`、`experiments/` 里写 |
| 往 `CHANGELOG.md` 的「前端反馈区」写 | 改 `CHANGELOG.md` 的公告部分 |
| 自己造 mock 数据用于开发 | 把 mock 当真实字段写进代码 |

**不要自己发明后端字段。** 缺什么，写进「前端反馈区」，等后端加。

## 3. 工具优先级

```
OPS 原生工具  >  ops_bash  >  pwsh 7  >  powershell 5.1
```

| 任务 | 用什么 |
|---|---|
| 读文件某几行 | `ops_inspect_local_file`（不要 cat） |
| 搜内容 | `ops_grep`（不要 grep/rg/findstr） |
| 按路径找文件 | `ops_glob`（不要 find/ls/dir） |
| 新建文件 | `ops_write_file`（必须给 `expectedRevision: "absent"`） |
| 改几处文本 | `ops_edit_file`（必须给读到的 `revision`，`oldText` 要唯一） |
| 预览 | `ops_diff` |
| 跑命令、git、python | `ops_bash`（MSYS Bash，路径写 `/e/随行工作台`） |
| Windows 系统操作 | `pwsh` 绝对路径（见下） |

## 4. PowerShell 7 用法

本机装的是 7.7.0 Core，**不在沙箱 bash 的 PATH 里**，要么补 PATH 要么用绝对路径：

```bash
export PATH="$PATH:/c/Users/23812/AppData/Local/Programs/PowerShell/7.7.0"
pwsh -NoProfile -NonInteractive -Command '[Console]::OutputEncoding=[Text.Encoding]::UTF8; ...'
```

老 cmdlet 在 7 里不兼容时，回退 `powershell.exe`（5.1）。

## 5. git

**推送一律用用户自己的 Git**，沙箱自带的那份读不到凭据：

```bash
/e/Git/cmd/git.exe add web/
/e/Git/cmd/git.exe commit -m "feat(web): ..."
GIT_TERMINAL_PROMPT=0 /e/Git/cmd/git.exe push origin main
```

**禁止 `git add -A`。** 工作区里可能同时有后端、论文、前端三方的改动，
整目录 add 会把别人的文件卷进你的提交（这个错误已经发生过一次）。

只 add 你自己改的路径。

## 6. 编码要求（与实验记录一致）

| 项 | 约定 |
|---|---|
| 编码 | UTF-8，**无 BOM** |
| 换行 | **LF** |
| 中文标点 | 全角 |
| HTML/CSS/JS | 缩进 2 空格；类名 kebab-case |

**不要用 Python 文本模式写文件**（`Path.write_text` 会把 `\n` 翻成 CRLF），
在 Windows 上会造成"整文件改动"的假 diff。用 `ops_write_file` / `ops_edit_file`。

## 7. 现在能接什么

后端**还没有 HTTP 服务**，只有 CLI。所以现阶段可选的两条路：

| 路线 | 做法 | 风险 |
|---|---|---|
| A. 纯静态 | 手写 JSON 放在 `web/data/`，按 `API.md` 的结构 | 字段要人工同步，容易漂 |
| B. 只读真实文件 | 构建时读 `tasks/*/task.json` 与 `runs/audit.jsonl` 生成静态数据 | 需要一个小脚本；`runs/` 不入库，换机器要重跑 |

推荐 B：数据真、字段天然对得上，还能在论文里写"前端读的是真实运行记录"。

## 8. 提交信息格式

```
feat(web): 任务列表与运行详情两个视图
fix(web): 闸门状态色在深色背景下对比度不足
docs(web): 补充数据来源说明
```

改完把影响写进 `interface/CHANGELOG.md` 的「前端反馈区」，别改公告区。
