"""协议层公共封套：所有模型调用的统一信封。

设计要点：
- 一条消息 = 一个封套，封套里带合同哈希、座位、阶段、参与者。
- 任何结论都能回答"在哪版契约下、由谁、在第几轮、花了多少"作出的。
- 座位（Seat）固定三个，模型数量只影响"谁坐哪个座位"。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Seat(str, Enum):
    """固定座位。模型数量变化不增加座位，只改变排班。"""

    PROPOSER = "proposer"
    PROPOSER_B = "proposer_b"
    REVIEWER = "reviewer"
    ARBITER = "arbiter"


class Phase(str, Enum):
    PROPOSE = "propose"
    CRITIQUE = "critique"
    DECIDE = "decide"
    ESCALATE = "escalate"


class ProtocolId(str, Enum):
    TWO_P = "2p.single"        # 提案 + 复核，分歧交闸门
    THREE_P = "3p.single"      # 提案 + 复核 + 裁决
    THREE_P_DUAL = "3p.dual"   # 双提案 + 裁决（高风险任务专用）


class Outcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    cached: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def to_dict(self) -> dict[str, int]:
        return {"prompt": self.prompt, "completion": self.completion, "cached": self.cached, "total": self.total}


class BudgetExceeded(RuntimeError):
    """预算耗尽。这属于正常终止条件，不是异常事故。"""


@dataclass
class Budget:
    """运行级预算。任何一次调用前都要先问它。"""

    max_calls: int = 40
    max_tokens: int = 200_000
    max_wall_s: float = 1800.0
    calls: int = 0
    tokens: int = 0
    wall_s: float = 0.0

    def reserve_call(self) -> None:
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded(f"调用次数超限：{self.calls}/{self.max_calls}")
        self.calls += 1

    def consume(self, usage: TokenUsage) -> None:
        self.tokens += usage.total
        if self.tokens > self.max_tokens:
            raise BudgetExceeded(f"token 超限：{self.tokens}/{self.max_tokens}")

    def add_wall(self, seconds: float) -> None:
        self.wall_s += seconds
        if self.wall_s > self.max_wall_s:
            raise BudgetExceeded(f"耗时超限：{self.wall_s:.0f}s/{self.max_wall_s:.0f}s")

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "tokens": self.tokens,
            "max_tokens": self.max_tokens,
            "wall_s": round(self.wall_s, 3),
            "max_wall_s": self.max_wall_s,
        }


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@dataclass(frozen=True)
class Envelope:
    """一次模型输出的统一信封。"""

    run_id: str
    task_id: str
    interface: str
    protocol: ProtocolId
    phase: Phase
    seat: Seat
    sender: str
    participants: tuple[str, ...]
    contract_hash: str
    payload: dict[str, Any]
    parent: str | None = None
    evidence: tuple[str, ...] = ()
    usage: TokenUsage | None = None
    degraded: bool = False
    msg_id: str = field(default_factory=lambda: new_id("msg"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "interface": self.interface,
            "protocol": self.protocol.value,
            "phase": self.phase.value,
            "seat": self.seat.value,
            "sender": self.sender,
            "participants": list(self.participants),
            "parent": self.parent,
            "contract_hash": self.contract_hash,
            "evidence": list(self.evidence),
            "usage": self.usage.to_dict() if self.usage else None,
            "degraded": self.degraded,
            "payload": self.payload,
        }
