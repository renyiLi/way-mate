"""执行后端（runtime）的公共约定。

座位是固定的，后端是可换的：
    direct          —— 一次生成整份补丁（便宜、秒级）
    runtime         —— 交给一个执行循环在工作区里迭代（贵、能啃难活）

**任何后端都必须归一化成 Patch**，否则复核与闸门要跟着改，可插拔就成了空话。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# 执行过程中可能出现的终止原因。闸门不看这个字段，只看 Patch。
STOP_SUBMITTED = "submitted"          # 模型主动声明完成
STOP_STEP_LIMIT = "step_limit"        # 步数用尽
STOP_BUDGET = "budget"                # token / 调用预算耗尽
STOP_FORMAT_ERROR = "format_error"    # 连续输出不合格式
STOP_WALL_TIME = "wall_time"          # 超时


@dataclass(frozen=True)
class WorkerResult:
    """执行后端的统一返回值。"""

    patch: dict[str, str]
    """改动后每个文件的完整内容。空字典表示模型没改任何文件。"""

    stop_reason: str
    """STOP_* 之一。仅供审计与调试。"""

    claim_done: bool
    """模型是否自称完成。**仅作参考，不参与判定**——闸门照跑。"""

    transcript: list[dict] = field(default_factory=list)
    """嵌套轨迹，原样写入审计。"""

    tokens: int = 0
    calls: int = 0


class WorkerRuntime(Protocol):
    name: str

    def run(self, *, workspace: Path, task: str, contract_digest: str, budget) -> WorkerResult: ...
