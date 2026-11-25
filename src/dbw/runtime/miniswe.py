"""把执行循环包装成 `WorkerRuntime`：交给它一个工作区，拿回一个 Patch。

这一层是本项目自己的部分：
- 契约摘要渲染（模型不知道契约，就得告诉它）；
- 工作区快照与差异提取；
- 终止原因归一化。

循环本身改编自 mini-swe-agent（MIT，见 NOTICE）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contract import Contract
from ..protocol.base import ModelAdapter
from ..protocol.envelope import Budget
from .base import STOP_STEP_LIMIT, WorkerResult
from .local_env import LocalEnvironment
from .loop import ExecutionLoop
from .workspace import diff, snapshot


def render_contract_digest(contract: Contract) -> str:
    """把契约压成几行短摘要。太长会挤占上下文，太短模型会漏。

    只放"模型可能违反"的硬约束：逐字断言、改动预算、禁止路径、验收命令。
    """
    lines: list[str] = []
    if contract.exact:
        lines.append("必须逐字出现的字符串（改措辞即失败）：")
        lines.extend(f"- {spec.exception} 的消息要包含 {spec.contains!r}" for spec in contract.exact)
    lines.append(f"改动上限：{contract.diff.max_files} 个文件 / {contract.diff.max_lines} 行。")
    if contract.diff.forbid:
        lines.append(f"禁止修改：{', '.join(contract.diff.forbid)}")
    lines.append("验收命令：" + "；".join(spec.cmd for spec in contract.tests))
    return "\n".join(lines)


@dataclass
class MiniSweRuntime:
    """本地执行后端。先用本地环境，容器化以后替换 `LocalEnvironment` 即可。"""

    model: ModelAdapter
    name: str = "miniswe"
    timeout_s: float = 30.0
    wall_time_s: float = 300.0
    max_format_errors: int = 3
    max_output_tokens: int = 1024
    step_limit: int | None = None

    def run(
        self,
        *,
        workspace: Path,
        task: str,
        contract_digest: str = "",
        budget: Budget | None = None,
        forbid: str = "",
    ) -> WorkerResult:
        workspace = Path(workspace)
        budget = budget or Budget()
        before = snapshot(workspace)

        loop = ExecutionLoop(
            model=self.model,
            env=LocalEnvironment(workspace, timeout_s=self.timeout_s),
            # 步数是安全网，不是预算：预算耗尽要能作为独立出口报告出来，
            # 所以默认上限放在 50，让 Budget 先说话。
            step_limit=self.step_limit or 50,
            max_format_errors=self.max_format_errors,
            wall_time_s=self.wall_time_s,
            max_output_tokens=self.max_output_tokens,
        )
        result = loop.run(task=task, contract_digest=contract_digest, forbid=forbid, budget=budget)

        after = snapshot(workspace)
        return WorkerResult(
            patch=diff(before, after),
            stop_reason=result.stop_reason or STOP_STEP_LIMIT,
            claim_done=result.claim_done,
            transcript=result.transcript,
            tokens=result.tokens,
            calls=result.calls,
        )

    def run_contract(self, *, workspace: Path, task: str, contract: Contract, budget: Budget | None = None) -> WorkerResult:
        """便捷入口：直接从契约渲染摘要与禁止路径。"""
        return self.run(
            workspace=workspace,
            task=task,
            contract_digest=render_contract_digest(contract),
            budget=budget,
            forbid=", ".join(contract.diff.forbid),
        )
