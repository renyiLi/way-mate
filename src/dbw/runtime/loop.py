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
from .local_env import SUBMIT_SENTINEL, LocalEnvironment

# 只认围栏代码块，避免把解释性文字当命令执行
_FENCE = re.compile(r"```(?:bash|sh|shell)?\s*\r?\n(.*?)```", re.DOTALL)

SYSTEM_TEMPLATE = """你是一个在代码仓库里工作的执行代理。

你可以通过输出一个 bash 代码块来执行命令：

```bash
pytest -q
```

规则：
- 每次只输出**一个** bash 代码块，不要输出解释文字；
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


def extract_command(text: str) -> str | None:
    """从模型输出里取出一条命令。取不到返回 None。

    优先认围栏代码块。没有围栏时只接受"单行且不含中文"的输出——
    中文单行几乎一定是解释性文字，当命令执行会把散文喂给 shell。
    """
    blocks = [block.strip() for block in _FENCE.findall(text or "")]
    blocks = [block for block in blocks if block]
    if blocks:
        return blocks[0]
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    if len(lines) == 1 and not lines[0].endswith(("?", "？", ":")) and not _has_cjk(lines[0]):
        return lines[0]
    return None


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
    ) -> None:
        self.model = model
        self.env = env
        self.step_limit = step_limit
        self.max_format_errors = max(1, max_format_errors)
        self.wall_time_s = wall_time_s
        self.max_output_tokens = max_output_tokens

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

            command = extract_command(response.text)
            history.append(Message("assistant", response.text))

            if command is None:
                format_errors += 1
                if format_errors >= self.max_format_errors:
                    result.stop_reason = STOP_FORMAT_ERROR
                    return result
                history.append(Message("user", "没有找到 bash 代码块。请只输出一个 bash 代码块，不要别的文字。"))
                continue

            format_errors = 0
            observation = self.env.execute(command)
            history.append(Message("user", observation.render()))
            result.transcript.append(
                {
                    "step": result.calls,
                    "command": command[:500],
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
