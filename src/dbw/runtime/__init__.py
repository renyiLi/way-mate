"""执行后端：把"怎么把活干出来"和"算不算干完"分开。

- `direct`（默认）：一次生成整份补丁，便宜；
- `MiniSweRuntime`：交给执行循环在工作区里迭代，贵但能啃难活。

两者都产出 `Patch`，所以复核与闸门不需要知道后端是谁。
"""

from .base import (
    STOP_BUDGET,
    STOP_FORMAT_ERROR,
    STOP_STEP_LIMIT,
    STOP_SUBMITTED,
    STOP_WALL_TIME,
    WorkerResult,
    WorkerRuntime,
)
from .local_env import SUBMIT_SENTINEL, LocalEnvironment, Observation
from .loop import ExecutionLoop, LoopResult, extract_command
from .miniswe import MiniSweRuntime, render_contract_digest
from .workspace import diff, snapshot

__all__ = [
    "STOP_BUDGET",
    "STOP_FORMAT_ERROR",
    "STOP_STEP_LIMIT",
    "STOP_SUBMITTED",
    "STOP_WALL_TIME",
    "SUBMIT_SENTINEL",
    "ExecutionLoop",
    "LocalEnvironment",
    "LoopResult",
    "MiniSweRuntime",
    "Observation",
    "WorkerResult",
    "WorkerRuntime",
    "diff",
    "extract_command",
    "render_contract_digest",
    "snapshot",
]
