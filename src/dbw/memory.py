"""成长能力接口（预留）。

成长性待定，但接口先留出来，且默认是空实现——接入与不接入，系统行为完全一致。

一条不可越过的边界：
    成长只能改变"怎么干活"，**不能改变"什么算干完"**。
契约只能由人确认和修改；系统若有权改验收标准，闸门就从客观裁判退化成自证。

所以本接口只做两件事：
1. 记录（record）：把一次运行沉淀成 Episode；
2. 召回（recall）：按任务指纹取回相似经验，渲染成一段提示词文本。

召回注入的是**教训**，不是完整成功补丁——注入答案会让模型背题，
在留出任务集上立刻原形毕露。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contract import Contract


@dataclass(frozen=True)
class Episode:
    """一次运行的沉淀。字段刻意保守：只存能解释清楚的东西。"""

    run_id: str
    signature: str
    protocol: str
    seats: dict[str, str]
    outcome: str
    rounds: int
    tokens: int
    gate_failures: tuple[str, ...] = ()
    lesson: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "signature": self.signature,
            "protocol": self.protocol,
            "seats": dict(self.seats),
            "outcome": self.outcome,
            "rounds": self.rounds,
            "tokens": self.tokens,
            "gate_failures": list(self.gate_failures),
            "lesson": self.lesson,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Episode":
        return cls(
            run_id=str(data.get("run_id", "")),
            signature=str(data.get("signature", "")),
            protocol=str(data.get("protocol", "")),
            seats=dict(data.get("seats") or {}),
            outcome=str(data.get("outcome", "")),
            rounds=int(data.get("rounds") or 0),
            tokens=int(data.get("tokens") or 0),
            gate_failures=tuple(data.get("gate_failures") or ()),
            lesson=str(data.get("lesson", "")),
            extra=dict(data.get("extra") or {}),
        )


def token_set(text: str) -> frozenset[str]:
    """把签名拆成词集合。规则切分，不引入分词依赖。"""
    return frozenset(part for part in text.replace("|", " ").replace("-", " ").split() if part)


def jaccard(left: str, right: str) -> float:
    """相似度。几十条样本时，这个比向量检索更可解释。"""
    a, b = token_set(left), token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def task_signature(contract: Contract, task: dict[str, Any]) -> str:
    """任务指纹：契约规模 + 问题关键词。刻意不包含 task_id，避免同名任务被误判为同类。"""
    parts = [
        f"tests{len(contract.tests)}",
        f"files{contract.diff.max_files}",
        f"lines{contract.diff.max_lines}",
        f"frozen{len(contract.api.frozen)}",
        f"forbid{len(contract.diff.forbid)}",
    ]
    issue = " ".join(str(task.get("issue", "")).split())[:160]
    raw = "|".join(parts) + "|" + issue
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ExperienceStore(Protocol):
    """经验库。L1 实现要满足：追加写、按指纹召回、可统计。"""

    def record(self, episode: Episode) -> None: ...

    def recall(self, signature: str, k: int = 3) -> tuple[Episode, ...]: ...

    def summary(self) -> dict[str, int]: ...


class NullExperienceStore:
    """默认实现：不记、不召回。行为与未接入时完全一致。"""

    def record(self, episode: Episode) -> None:
        return None

    def recall(self, signature: str, k: int = 3) -> tuple[Episode, ...]:
        return ()

    def summary(self) -> dict[str, int]:
        return {"recorded": 0, "recalled": 0}


class MemoryHook(Protocol):
    """协议机在提案前调用。返回空串表示"不注入任何东西"。"""

    def before_propose(self, *, task: dict[str, Any], contract: Contract) -> str: ...


class NullMemoryHook:
    """默认钩子：什么都不注入。"""

    def before_propose(self, *, task: dict[str, Any], contract: Contract) -> str:
        return ""


EXPERIENCE_HEADER = "## 同类任务的历史经验（仅供参考，不是规则）"


def render_experience(episodes: tuple[Episode, ...], limit: int = 3) -> str:
    """把经验渲染成提示词区块。

    只写"发生了什么 + 教训"，不贴完整成功补丁——注入答案等于让模型背题。
    """
    if not episodes:
        return ""
    lines = [EXPERIENCE_HEADER]
    for index, episode in enumerate(episodes[:limit], start=1):
        gates = "、".join(episode.gate_failures) or "无"
        head = f"{index}. [{episode.outcome}, {episode.rounds} 轮, 失败闸门：{gates}]"
        lines.append(head)
        if episode.lesson:
            lines.append(f"   教训：{episode.lesson}")
    return "\n".join(lines)
