"""协议机：提案 → 复核 → 裁决 → 闸门。

一台状态机同时实现 2 人协议、3 人协议、3 人双轨协议，
差别只在 SeatAssignment 这张排班表，不在流程代码。

铁律：模型之间可以争论，但**只有闸门能宣布交付成立**。
复核说有问题而闸门全绿 → 交付成立（模型可能误报）；
复核说没问题而闸门红 → 交付不成立（模型可能漏报）。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..contract import Contract
from ..gates import GateReport
from .base import (
    Message,
    ModelRequest,
    ModelResponse,
    PayloadError,
    ProviderRegistry,
    extract_json,
)
from .envelope import (
    Budget,
    BudgetExceeded,
    Envelope,
    Outcome,
    Phase,
    ProtocolId,
    Seat,
    new_id,
)
from .interfaces import (
    ARBITER_RULES,
    CHOICE_SCHEMA,
    VERDICTS_SCHEMA,
    Finding,
    InvalidOutput,
    ReviewOutcome,
    apply_verdicts,
    get_interface,
    render_propose_prompt,
)

Evaluator = Callable[[Any, ReviewOutcome], GateReport]


@dataclass(frozen=True)
class SeatAssignment:
    """排班表。模型数量只决定座位怎么分，不决定流程。"""

    proposer: str
    reviewer: str
    arbiter: str | None = None
    proposer_b: str | None = None

    def protocol(self) -> ProtocolId:
        if self.proposer_b and self.arbiter:
            return ProtocolId.THREE_P_DUAL
        if self.arbiter:
            return ProtocolId.THREE_P
        return ProtocolId.TWO_P

    def participants(self) -> tuple[str, ...]:
        names = [self.proposer, self.reviewer]
        if self.proposer_b:
            names.append(self.proposer_b)
        if self.arbiter:
            names.append(self.arbiter)
        return tuple(dict.fromkeys(names))

    def validate(self) -> None:
        if self.proposer_b and not self.arbiter:
            raise ValueError("双轨协议必须有裁决座位")
        if not self.proposer or not self.reviewer:
            raise ValueError("提案与复核座位不可为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposer": self.proposer,
            "proposer_b": self.proposer_b,
            "reviewer": self.reviewer,
            "arbiter": self.arbiter,
            "protocol": self.protocol().value,
            "participants": list(self.participants()),
        }


@dataclass
class RoundRecord:
    index: int
    proposal_count: int = 0
    deliverable: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    blockers_after_arbitration: int = 0
    gate: dict[str, Any] | None = None
    gate_signature: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "proposal_count": self.proposal_count,
            "deliverable": self.deliverable,
            "findings": self.findings,
            "dropped": self.dropped,
            "blockers_after_arbitration": self.blockers_after_arbitration,
            "gate": self.gate,
            "gate_signature": self.gate_signature,
            "note": self.note,
        }


@dataclass
class ProtocolRun:
    run_id: str
    interface: str
    seats: SeatAssignment
    contract_hash: str
    rounds: list[RoundRecord] = field(default_factory=list)
    outcome: Outcome = Outcome.ESCALATED
    reason: str = ""
    budget: Budget = field(default_factory=Budget)
    transcripts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def protocol(self) -> ProtocolId:
        return self.seats.protocol()

    @property
    def self_played(self) -> bool:
        """同一个模型兼任多个座位。实验中必须单独标注，否则对比是假的。"""
        seat_count = 2 + (1 if self.seats.proposer_b else 0) + (1 if self.seats.arbiter else 0)
        return len(self.seats.participants()) < seat_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "interface": self.interface,
            "protocol": self.protocol.value,
            "seats": self.seats.to_dict(),
            "self_played": self.self_played,
            "contract_hash": self.contract_hash,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "budget": self.budget.to_dict(),
            "rounds": [r.to_dict() for r in self.rounds],
            "transcripts": self.transcripts,
        }


def contract_hash(contract: Contract) -> str:
    raw = json.dumps(contract.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ProtocolRunner:
    """把一次交付跑完：多轮提案、复核、裁决，每轮都过闸门。"""

    def __init__(
        self,
        *,
        interface: str,
        task: dict[str, Any],
        seats: SeatAssignment,
        registry: ProviderRegistry,
        evaluate: Evaluator,
        contract: Contract,
        audit: Any = None,
        budget: Budget | None = None,
        run_id: str | None = None,
        max_repair_rounds: int | None = None,
        no_improvement_limit: int = 2,
        memory: Any = None,
    ) -> None:
        seats.validate()
        self.spec = get_interface(interface)
        self.task = task
        self.seats = seats
        self.registry = registry
        self.evaluate = evaluate
        self.contract = contract
        self.audit = audit
        self.memory = memory
        self.budget = budget or Budget()
        self.run_id = run_id or new_id("run")
        self.hash = contract_hash(contract)
        self.max_attempts = (max_repair_rounds if max_repair_rounds is not None else contract.loop.repair_rounds) + 1
        self.no_improvement_limit = max(1, no_improvement_limit)

        self.run = ProtocolRun(
            run_id=self.run_id,
            interface=interface,
            seats=seats,
            contract_hash=self.hash,
            budget=self.budget,
        )
        self._seq = 0
        self._feedback: list[str] = []

    # ------------------------------------------------------------ 对外

    def run_protocol(self) -> ProtocolRun:
        self._emit("run_start", task=self.task, seats=self.seats.to_dict(), interface=self.spec.name)
        try:
            self._loop()
        except BudgetExceeded as exc:
            self._finish(Outcome.BUDGET_EXHAUSTED, str(exc))
        except InvalidOutput as exc:
            self._finish(Outcome.REJECTED, f"输出不合法：{exc}")
        return self.run

    # ------------------------------------------------------------ 主循环

    def _loop(self) -> None:
        previous_signature: str | None = None
        stalled = 0

        for attempt in range(1, self.max_attempts + 1):
            record = RoundRecord(index=attempt)
            self.run.rounds.append(record)

            deliverable, proposals = self._propose(attempt)
            record.proposal_count = proposals
            record.deliverable = self._describe(deliverable)

            outcome = self._review(deliverable)
            record.findings = [item.to_dict() for item in outcome.items]
            record.dropped = list(outcome.dropped)

            blockers = outcome.blockers
            if blockers and self.seats.arbiter:
                blockers, rejected = self._arbitrate(blockers)
                record.note = f"裁决驳回 {len(rejected)} 条阻断结论"
            record.blockers_after_arbitration = len(blockers)

            report = self.evaluate(deliverable, outcome)
            record.gate = report.to_dict()
            signature = self._signature(report)
            record.gate_signature = signature
            self._emit("gate_report", attempt=attempt, report=report.to_dict(), signature=signature)

            if report.all_green:
                self._finish(Outcome.ACCEPTED, f"第 {attempt} 轮闸门全绿")
                return

            stalled = stalled + 1 if signature == previous_signature else 0
            previous_signature = signature

            if stalled >= self.no_improvement_limit:
                self._finish(Outcome.ESCALATED, f"连续 {stalled} 轮闸门结果无变化，提前终止以免无效消耗")
                return

            # 反馈必须带失败明细：只说"失败 1 条"，下一轮等于让模型盲猜
            lines = [f"第 {attempt} 轮未通过，未通过的闸门："]
            for result in report.failures:
                lines.append(f"- {result.name}: {result.detail}")
                tail = (result.metrics or {}).get("tail")
                if tail:
                    lines.append("  最近输出：")
                    lines.extend(f"    {row}" for row in str(tail).splitlines()[-12:])
            if blockers:
                lines.append("复核提出的阻断问题：")
                lines.extend(f"- [{b.gate}] {b.claim}" for b in blockers)
            self._feedback = lines

        self._finish(Outcome.ESCALATED, f"{self.max_attempts} 轮内未能全绿，交人工处理")

    # ------------------------------------------------------------ 三个阶段

    def _propose(self, attempt: int) -> tuple[Any, int]:
        schema = self.spec.propose_schema()
        system = self.spec.system_prompt(self.contract)
        base_prompt = self._propose_prompt()

        # 成长接口的唯一注入点：没有实现时返回空串，行为不变
        if self.memory is not None:
            block = self.memory.before_propose(task=self.task, contract=self.contract) or ""
            if block:
                base_prompt = f"{block}\n\n{base_prompt}"
                self._emit("memory_injected", chars=len(block))

        if self._feedback:
            base_prompt = base_prompt + "\n\n" + "\n".join(self._feedback)

        first = self._ask_parsed(
            self.seats.proposer, Seat.PROPOSER, Phase.PROPOSE, system, base_prompt, schema,
            lambda payload: self.spec.parse_proposal(payload, self.contract),
        )

        if not self.seats.proposer_b:
            return first, 1

        second_prompt = base_prompt + "\n\n另一名提案者已独立给出方案，请给出你自己的独立方案（不要参考对方）。"
        second = self._ask_parsed(
            self.seats.proposer_b, Seat.PROPOSER_B, Phase.PROPOSE, system, second_prompt, schema,
            lambda payload: self.spec.parse_proposal(payload, self.contract),
        )

        chosen_index = self._choose(first, second)
        return (first if chosen_index == 0 else second), 2

    def _review(self, deliverable: Any) -> ReviewOutcome:
        prompt = (
            f"{self._task_block()}\n\n"
            f"待复核的交付物：\n{self.spec.critique_context(deliverable)}\n\n"
            "请逐条列出对契约的违反。"
        )
        return self._ask_parsed(
            self.seats.reviewer, Seat.REVIEWER, Phase.CRITIQUE,
            self.spec.system_prompt(self.contract), prompt, self.spec.review_schema(),
            lambda payload: self.spec.parse_review(payload, self.contract, deliverable),
        )

    def _arbitrate(self, blockers: tuple[Finding, ...]) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
        system = f"{ARBITER_RULES}\n\n当前契约验收条件：{json.dumps(self.contract.to_dict()['doD'], ensure_ascii=False)}"
        prompt = (
            f"任务：{self._task_block()}\n\n"
            f"复核座位提出了 {len(blockers)} 条阻断结论，请逐条裁决：\n"
            + json.dumps([b.to_dict() for b in blockers], ensure_ascii=False, indent=2)
        )
        assert self.seats.arbiter is not None
        return self._ask_parsed(
            self.seats.arbiter, Seat.ARBITER, Phase.DECIDE, system, prompt, VERDICTS_SCHEMA,
            lambda payload: apply_verdicts(blockers, payload),
        )

    def _choose(self, first: Any, second: Any) -> int:
        system = f"{ARBITER_RULES}\n\n当前契约验收条件：{json.dumps(self.contract.to_dict()['doD'], ensure_ascii=False)}"
        prompt = (
            f"任务：{self._task_block()}\n\n"
            f"候选 A：\n{self._describe(first)}\n{self.spec.critique_context(first)}\n\n"
            f"候选 B：\n{self._describe(second)}\n{self.spec.critique_context(second)}\n\n"
            "按契约选择更可能一次通过的那个，输出 choice（0 表示 A，1 表示 B）。"
        )
        assert self.seats.arbiter is not None

        def parse(payload: Any) -> int:
            if not isinstance(payload, dict) or not isinstance(payload.get("choice"), int):
                raise InvalidOutput("choice 必须是整数")
            if payload["choice"] not in (0, 1):
                raise InvalidOutput(f"choice 只能是 0 或 1，实际 {payload['choice']}")
            return int(payload["choice"])

        return self._ask_parsed(self.seats.arbiter, Seat.ARBITER, Phase.DECIDE, system, prompt, CHOICE_SCHEMA, parse)

    # ------------------------------------------------------------ 调用与解析

    def _ask_parsed(self, provider: str, seat: Seat, phase: Phase, system: str, user: str, schema: dict, parse: Callable[[Any], Any]) -> Any:
        last_error: Exception | None = None
        prompt = user
        for _attempt in range(2):
            text, _response = self._call(provider, seat, phase, system, prompt, schema)
            try:
                payload = extract_json(text)
                return parse(payload)
            except PayloadError as exc:
                last_error = exc
                prompt = user + f"\n\n注意：你上一次的输出不合法（{exc}）。请只输出符合要求的 JSON，不要任何其他文字。"
        raise InvalidOutput(f"{provider}/{seat.value} 连续两次输出不合法：{last_error}")

    def _call(self, provider: str, seat: Seat, phase: Phase, system: str, user: str, schema: dict) -> tuple[str, ModelResponse]:
        self.budget.reserve_call()
        self._seq += 1
        self._emit("model_request", seat=seat.value, provider=provider, phase=phase.value, prompt_chars=len(system) + len(user))

        request = ModelRequest(
            messages=(Message("system", system), Message("user", user)),
            response_schema=schema,
            max_tokens=4096,
            temperature=0.2,
            timeout_s=120.0,
            idempotency_key=f"{self.run_id}:{seat.value}:{phase.value}:{self._seq}",
        )
        started = time.perf_counter()
        response = self.registry.get(provider).complete(request)
        elapsed = time.perf_counter() - started

        self.budget.consume(response.usage)
        self.budget.add_wall(elapsed)
        self._record_envelope(provider, seat, phase, response)
        return response.text, response

    def _record_envelope(self, provider: str, seat: Seat, phase: Phase, response: ModelResponse) -> None:
        envelope = Envelope(
            run_id=self.run_id,
            task_id=str(self.task.get("task_id", "")),
            interface=self.spec.name,
            protocol=self.seats.protocol(),
            phase=phase,
            seat=seat,
            sender=provider,
            participants=self.seats.participants(),
            contract_hash=self.hash,
            payload={},
            usage=response.usage,
            degraded=response.degraded,
        )
        record = {
            "envelope": envelope.to_dict(),
            "model": response.model,
            "finish_reason": response.finish_reason,
            "latency_ms": response.latency_ms,
            "request_digest": response.request_digest,
            "raw_digest": response.raw_digest,
            "raw_text": response.text,
            "degraded_reason": response.degraded_reason,
        }
        self.run.transcripts.append(record)
        self._emit("model_response", **{k: v for k, v in record.items() if k != "envelope"})

    # ------------------------------------------------------------ 工具

    def _task_block(self) -> str:
        return json.dumps(self.task, ensure_ascii=False, indent=2)

    def _propose_prompt(self) -> str:
        # 与实际交付共用同一个构造点，否则实验对比会混入提示词变量
        return render_propose_prompt(self.task, self.contract)

    def _describe(self, deliverable: Any) -> dict[str, Any]:
        if hasattr(deliverable, "to_dict"):
            return {"kind": type(deliverable).__name__, "payload": deliverable.to_dict()}
        if isinstance(deliverable, tuple):
            return {
                "kind": "list",
                "payload": [d.to_dict() if hasattr(d, "to_dict") else str(d) for d in deliverable],
            }
        return {"kind": type(deliverable).__name__, "payload": str(deliverable)}

    @staticmethod
    def _signature(report: GateReport) -> str:
        return "|".join(f"{r.name}:{int(r.ok)}" for r in report.results)

    def _finish(self, outcome: Outcome, reason: str) -> None:
        self.run.outcome = outcome
        self.run.reason = reason
        self._emit(
            "run_end",
            outcome=outcome.value,
            reason=reason,
            budget=self.budget.to_dict(),
            rounds=len(self.run.rounds),
        )

    def _emit(self, event: str, **fields: Any) -> None:
        if self.audit is not None and hasattr(self.audit, "emit"):
            self.audit.emit(event, run_id=self.run_id, **fields)


def execute(*, interface: str, task: dict[str, Any], seats: SeatAssignment, registry: ProviderRegistry,
            evaluate: Evaluator, contract: Contract, **kwargs: Any) -> ProtocolRun:
    """一行跑完一个协议。"""
    return ProtocolRunner(
        interface=interface, task=task, seats=seats, registry=registry,
        evaluate=evaluate, contract=contract, **kwargs,
    ).run_protocol()
