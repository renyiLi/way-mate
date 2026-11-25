"""执行循环：模型 → bash → 观测 → 再模型，直到提交或触限。

改编自 mini-swe-agent 的 `agents/default.py`（MIT，见同目录 NOTICE）。
改动：
- 去掉 jinja2 模板与 pydantic 配置，模板改用 f-string；
- 模型层换成本项目的 `ModelAdapter`，顺带获得能力协商与降级登记；
- 预算改成显式的 `Budget`，超支是正常出口而非异常；
- 连续格式错误的处理保留原语义（超过上限即终止）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..protocol.base import AdapterError, Message, ModelRequest, ModelAdapter
from ..protocol.envelope import Budget, BudgetExceeded
from .base import (
    STOP_BUDGET,
    STOP_FORMAT_ERROR,
    STOP_STEP_LIMIT,
    STOP_SUBMITTED,
    STOP_WALL_TIME,
)
from .local_env import SUBMIT_SENTINEL, LocalEnvironment, Observation
from ..edit_format import Edit, EditError, apply_edits_to_workspace, parse_markers

# 认两种块：bash 执行、write:<path> 写文件。
# 原项目只靠 bash heredoc 改文件，那在 Windows 的 cmd 下不成立，
# 模型会陷入"只能读、没法写"的死循环。
_FENCE = re.compile(r"```([A-Za-z0-9_.\-/\\:]*)[ \t]*\r?\n(.*?)```", re.DOTALL)
_WRITE_PREFIX = "write:"
_EDIT_PREFIX = "edit:"
_BASH_TAGS = {"", "bash", "sh", "shell", "cmd", "powershell", "ps1"}

SYSTEM_TEMPLATE = """你是一个在代码仓库里工作的执行代理。

你有两种动作，每次只输出其中一种：

1) 执行命令（看文件、跑测试）：
```bash
pytest -q
```
2) 写文件（给整份新内容，不要给 diff）：
```write:相对路径/文件名.py
<这个文件的完整新内容>
```

规则：
- 每次只输出**一个**代码块，不要输出解释文字；
- 改已有文件优先用 edit 块，只给要替换的那几行：
```edit:相对路径/文件名.py
<<<<<<< SEARCH
（原文，逐字存在且唯一）
=======
（替换成什么）
>>>>>>> REPLACE
```
- 新建文件或整份重写才用 write 块；不要用 sed / heredoc / python -c 改文件；
- 命令在仓库根目录执行，你会看到输出与退出码；
- 任务完成时执行：echo {sentinel}
- 不要修改禁止改动的路径：{forbid}
- 不要执行联网命令，不要删除 .git。
"""

INSTANCE_TEMPLATE = """任务：
{task}

验收契约摘要：
{contract_digest}

现在开始。第一步通常是先看看仓库里有什么。
"""


@dataclass
class LoopResult:
    stop_reason: str
    claim_done: bool
    submission: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    calls: int = 0


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


@dataclass(frozen=True)
class Action:
    kind: str      # "bash" | "write"
    body: str
    target: str = ""


def parse_action(text: str) -> Action | None:
    """从模型输出里取出一个动作。取不到返回 None。"""
    for tag, body in _FENCE.findall(text or ""):
        tag = tag.strip()
        lowered = tag.lower()
        if lowered.startswith(_WRITE_PREFIX):
            target = tag[len(_WRITE_PREFIX):].strip()
            if target:
                return Action(kind="write", body=body, target=target)
            continue
        if lowered.startswith(_EDIT_PREFIX):
            target = tag[len(_EDIT_PREFIX):].strip()
            if target:
                return Action(kind="edit", body=body, target=target)
            continue
        if lowered in _BASH_TAGS:
            command = body.strip()
            if command:
                return Action(kind="bash", body=command)
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    if len(lines) == 1 and not lines[0].endswith(("?", "？", ":")) and not _has_cjk(lines[0]):
        return Action(kind="bash", body=lines[0])
    return None


def extract_command(text: str) -> str | None:
    """只要 bash 命令（兼容旧接口）。写文件动作返回 None。"""
    action = parse_action(text)
    return action.body if action and action.kind == "bash" else None


class ExecutionLoop:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        env: LocalEnvironment,
        step_limit: int = 15,
        max_format_errors: int = 3,
        wall_time_s: float = 300.0,
        max_output_tokens: int = 1024,
        readonly_nudge_after: int = 3,
    ) -> None:
        self.model = model
        self.env = env
        self.step_limit = step_limit
        self.max_format_errors = max(1, max_format_errors)
        self.wall_time_s = wall_time_s
        self.max_output_tokens = max_output_tokens
        self.readonly_nudge_after = max(2, readonly_nudge_after)
        self._readonly_streak = 0

    def run(self, *, task: str, contract_digest: str, forbid: str, budget: Budget) -> LoopResult:
        system = SYSTEM_TEMPLATE.format(sentinel=SUBMIT_SENTINEL, forbid=forbid or "（无限制）")
        user = INSTANCE_TEMPLATE.format(task=task, contract_digest=contract_digest or "（无）")

        history: list[Message] = [Message("system", system), Message("user", user)]
        result = LoopResult(stop_reason=STOP_STEP_LIMIT, claim_done=False)
        started = time.perf_counter()
        format_errors = 0

        while True:
            if result.calls >= self.step_limit:
                result.stop_reason = STOP_STEP_LIMIT
                return result
            if time.perf_counter() - started > self.wall_time_s:
                result.stop_reason = STOP_WALL_TIME
                return result

            try:
                budget.reserve_call()
            except BudgetExceeded:
                result.stop_reason = STOP_BUDGET
                return result

            try:
                response = self.model.complete(
                    ModelRequest(
                        messages=tuple(history),
                        max_tokens=self.max_output_tokens,
                        temperature=0.0,
                        timeout_s=120.0,
                        idempotency_key=f"loop:{budget.calls}:{result.calls}",
                    )
                )
            except AdapterError as exc:
                result.stop_reason = STOP_FORMAT_ERROR
                result.transcript.append({"step": result.calls, "error": f"{type(exc).__name__}: {exc}"})
                return result

            result.calls += 1
            result.tokens += response.usage.total
            try:
                budget.consume(response.usage)
            except BudgetExceeded:
                result.stop_reason = STOP_BUDGET
                return result

            action = parse_action(response.text)
            history.append(Message("assistant", response.text))

            if action is None:
                format_errors += 1
                if format_errors >= self.max_format_errors:
                    result.stop_reason = STOP_FORMAT_ERROR
                    return result
                history.append(
                    Message("user", "没有找到可识别的代码块。请输出一个 bash 块或一个 write:路径 块，不要别的文字。")
                )
                continue

            format_errors = 0
            if action.kind == "write":
                observation = self._write(action)
                label = f"write:{action.target}"
                self._readonly_streak = 0
            elif action.kind == "edit":
                observation = self._edit(action)
                label = f"edit:{action.target}"
                self._readonly_streak = 0
            else:
                observation = self.env.execute(action.body)
                label = action.body
                self._readonly_streak += 1

            history.append(Message("user", observation.render()))
            result.transcript.append(
                {
                    "step": result.calls,
                    "command": label[:500],
                    "returncode": observation.returncode,
                    "timed_out": observation.timed_out,
                    "output_chars": len(observation.output),
                    "tokens": response.usage.total,
                }
            )

            if observation.submitted:
                result.stop_reason = STOP_SUBMITTED
                result.claim_done = True
                result.submission = observation.submission
                return result

            # 探测预算：连续只读不写就催一下。
            # 真实仓库上模型容易陷入"一直看、不动手"，这条能把预算从阅读里救出来。
            if self._readonly_streak >= self.readonly_nudge_after:
                history.append(
                    Message(
                        "user",
                        f"你已经连续 {self._readonly_streak} 步只读不写。"
                        "请立即用 write 块修改你认为需要改的文件；如果你已经知道改哪里，现在就改。",
                    )
                )
                self._readonly_streak = 0

    def _edit(self, action: Action) -> Observation:
        try:
            search, replace = parse_markers(action.body)
        except EditError as exc:
            return Observation(output=f"edit 块格式不对：{exc}", returncode=1)

        result = apply_edits_to_workspace(self.env.workspace, [Edit(action.target, search, replace)])
        if result.failures:
            return Observation(output="；".join(result.failures), returncode=1)
        return Observation(output=f"已修改 {action.target}（1 处）", returncode=0)

    def _write(self, action: Action) -> Observation:
        workspace = self.env.workspace.resolve()
        target = (workspace / action.target).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            return Observation(output=f"拒绝写入工作区之外的路径：{action.target}", returncode=1)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(action.body, encoding="utf-8")
        except OSError as exc:
            return Observation(output=f"写入失败：{exc}", returncode=1)
        return Observation(output=f"已写入 {action.target}（{len(action.body)} 字符）", returncode=0)
