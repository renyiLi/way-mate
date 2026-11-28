"""协议机的行为测试：2 人 / 3 人 / 双轨 / 审计 / 协作 / 预算。

全部用脚本化假适配器，不花一分钱、不发一次网络请求。
这些测试就是论文第 6 章"协议行为正确性"那一节的证据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import load_contract  # noqa: E402
from dbw.gates import GateReport, GateResult  # noqa: E402
from dbw.protocol import (  # noqa: E402
    Budget,
    Capabilities,
    ModelResponse,
    Outcome,
    ProtocolId,
    ProviderRegistry,
    SeatAssignment,
    TokenUsage,
    dag_gate,
    execute,
    extract_json,
)
from dbw.protocol.interfaces import AuthoringInterface, get_interface, parse_findings  # noqa: E402

CONTRACT = load_contract(ROOT / "fixtures" / "repo_a" / "contract.yaml")

PATCH = {
    "files": {"pkg/parser.py": "def parse_kv(text: str) -> dict[str, str]:\n    return {}\n"},
    "rationale": "最小改动：先让用例不再报错",
}
NO_FINDINGS = {"findings": []}
BLOCKER = {
    "findings": [
        {
            "fid": "F1",
            "severity": "BLOCKER",
            "gate": "tests",
            "claim": "parse_kv 永远返回空字典",
            "evidence": "tests/test_parser.py::test_basic_pairs 期望 {'a':'1'}",
        }
    ]
}


class ScriptedAdapter:
    """按剧本返回响应的假供应商。剧本用完后重复最后一条。"""

    def __init__(self, provider: str, script: list, model: str = "scripted") -> None:
        self.provider = provider
        self.model = model
        self.script = list(script)
        self.calls: list = []

    def capabilities(self) -> Capabilities:
        return Capabilities(supports_json_schema=True)

    def complete(self, request) -> ModelResponse:
        self.calls.append(request)
        if not self.script:
            raise AssertionError(f"{self.provider} 的剧本已用尽")
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return ModelResponse(
            model=self.model,
            text=text,
            usage=TokenUsage(prompt=100, completion=50),
            request_digest=request.digest(),
        )


def registry(**adapters: ScriptedAdapter) -> ProviderRegistry:
    reg = ProviderRegistry()
    for name, adapter in adapters.items():
        reg.register(name, adapter)
    return reg


def gate(ok: bool) -> GateReport:
    return GateReport(task_id="t", results=[GateResult("tests", ok, "假闸门" if not ok else "通过")])


TASK = {"task_id": "fix-parser-inline-comment", "issue": "a=1  # note 被解析成 '1  # note'"}


# ---------------------------------------------------------------- 座位与协议编号

def test_protocol_ids_follow_seat_table():
    assert SeatAssignment("deepseek", "kimi").protocol() is ProtocolId.TWO_P
    assert SeatAssignment("deepseek", "kimi", arbiter="codex").protocol() is ProtocolId.THREE_P
    assert (
        SeatAssignment("deepseek", "kimi", arbiter="codex", proposer_b="kimi").protocol()
        is ProtocolId.THREE_P_DUAL
    )


def test_dual_requires_arbiter():
    with pytest.raises(ValueError, match="裁决"):
        SeatAssignment("a", "b", proposer_b="c").validate()


# ---------------------------------------------------------------- 2 人协议

def test_two_party_accepts_when_gate_green():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ACCEPTED
    assert run.protocol is ProtocolId.TWO_P
    assert len(run.rounds) == 1
    assert run.self_played is False


def test_two_party_escalates_when_gate_never_moves():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda patch, outcome: gate(False),
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ESCALATED
    assert "无变化" in run.reason
    # 第 1 轮建立基准，第 2 轮计数 1，第 3 轮计数 2 触发终止
    assert len(run.rounds) == 3


def test_same_model_in_two_seats_is_flagged():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "deepseek"),
        registry=registry(deepseek=ScriptedAdapter("deepseek", [PATCH, NO_FINDINGS])),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
    )
    assert run.self_played is True, "同模型扮两角必须在实验数据里标注"


# ---------------------------------------------------------------- 3 人协议

def test_arbiter_can_reject_baseless_blocker_and_gate_wins():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi", arbiter="codex"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [BLOCKER]),
            codex=ScriptedAdapter(
                "codex",
                [{"verdicts": [{"fid": "F1", "decision": "rejected", "reason": "证据引用的是不存在的用例"}]}],
            ),
        ),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ACCEPTED
    assert run.protocol is ProtocolId.THREE_P
    assert run.rounds[0].blockers_after_arbitration == 0
    assert "驳回 1 条" in run.rounds[0].note


def test_arbiter_upheld_blocker_blocks_delivery():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi", arbiter="codex"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [BLOCKER]),
            codex=ScriptedAdapter(
                "codex",
                [{"verdicts": [{"fid": "F1", "decision": "upheld", "reason": "用例确实会失败"}]}],
            ),
        ),
        evaluate=lambda patch, outcome: gate(False),
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ESCALATED
    assert run.rounds[0].blockers_after_arbitration == 1


def test_dual_track_lets_arbiter_pick_a_proposal():
    seen: list[dict] = []

    def evaluate(patch, outcome):
        seen.append(patch.files)
        return gate(True)

    second = {
        "files": {"pkg/parser.py": "def parse_kv(text: str) -> dict[str, str]:\n    raise NotImplementedError\n"},
        "rationale": "另一条独立思路",
    }
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi", arbiter="codex", proposer_b="kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [second, {"choice": 1, "reason": "B 更贴近契约"}, NO_FINDINGS]),
            codex=ScriptedAdapter("codex", [{"choice": 1, "reason": "B 更贴近契约"}]),
        ),
        evaluate=evaluate,
        contract=CONTRACT,
    )
    assert run.protocol is ProtocolId.THREE_P_DUAL
    assert run.rounds[0].proposal_count == 2
    assert "NotImplementedError" in seen[0]["pkg/parser.py"], "选中 B 后应把 B 交给闸门"


# ---------------------------------------------------------------- 审计接口

def test_review_interface_drops_findings_without_evidence():
    findings, dropped = parse_findings(
        {
            "findings": [
                {"fid": "F1", "severity": "BLOCKER", "gate": "tests", "claim": "有证据", "evidence": "tests/test_parser.py::test_inline_comment_stripped"},
                {"fid": "F2", "severity": "MAJOR", "gate": "lint", "claim": "没有证据", "evidence": ""},
                {"fid": "F3", "severity": "MINOR", "gate": "风格", "claim": "gate 非法", "evidence": "x"},
            ]
        }
    )
    assert [f.fid for f in findings] == ["F1", "F3"]
    assert findings[1].gate == "unknown", "未知 gate 保留结论，降为 unknown"
    assert any("没有证据" in d for d in dropped)
    assert any("gate" in d for d in dropped)


def test_blocker_with_unknown_gate_is_downgraded():
    """未挂到契约上的结论不能阻断交付，但仍保留信息。"""
    findings, dropped = parse_findings(
        {
            "findings": [
                {"fid": "F1", "severity": "BLOCKER", "gate": "root_cause", "claim": "根因不对", "evidence": "a.py:12"}
            ]
        }
    )
    assert [f.fid for f in findings] == ["F1"]
    assert findings[0].severity == "MAJOR"
    assert findings[0].gate == "unknown"
    assert any("BLOCKER" in d for d in dropped)


def test_finding_keeps_category_field():
    findings, _ = parse_findings(
        {
            "findings": [
                {
                    "fid": "F1",
                    "severity": "MAJOR",
                    "gate": "tests",
                    "claim": "覆盖不足",
                    "evidence": "test/test_output.py::test_github",
                    "category": "测试覆盖",
                }
            ]
        }
    )
    assert findings[0].category == "测试覆盖"
    assert findings[0].to_dict()["category"] == "测试覆盖"


def test_review_workload_verifies_its_own_findings():
    captured: list = []

    def evaluate(deliverable, outcome):
        captured.append(outcome)
        return gate(True)

    run = execute(
        interface="review",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [BLOCKER]),
            kimi=ScriptedAdapter(
                "kimi",
                [{"verdicts": [{"fid": "F1", "decision": "upheld", "reason": "用例确实存在"}]}],
            ),
        ),
        evaluate=evaluate,
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ACCEPTED
    assert len(captured[0].blockers) == 1
    assert captured[0].blockers[0].gate == "tests"


# ---------------------------------------------------------------- 协作接口

def test_collab_cycle_is_caught_by_gate():
    cyclic = {
        "nodes": [
            {"id": "T1", "goal": "改解析器", "dod": ["pytest tests/test_parser.py"], "depends_on": ["T2"]},
            {"id": "T2", "goal": "改测试", "dod": ["pytest tests/test_parser.py"], "depends_on": ["T1"]},
        ]
    }
    run = execute(
        interface="collab",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [cyclic]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda dag, outcome: dag_gate(dag),
        contract=CONTRACT,
    )
    assert run.outcome is not Outcome.ACCEPTED
    assert run.rounds[0].gate["all_green"] is False
    failed = [r["name"] for r in run.rounds[0].gate["results"] if not r["ok"]]
    assert "diff" in failed


def test_collab_acyclic_passes_gate():
    ok = {
        "nodes": [
            {"id": "T1", "goal": "改解析器", "dod": ["pytest tests/test_parser.py"], "depends_on": []},
            {"id": "T2", "goal": "回归", "dod": ["pytest tests/"], "depends_on": ["T1"]},
        ]
    }
    run = execute(
        interface="collab",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [ok]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda dag, outcome: dag_gate(dag),
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ACCEPTED


# ---------------------------------------------------------------- 健壮性

def test_malformed_json_is_retried_once_then_succeeds():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", ["这不是 JSON", PATCH]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
    )
    assert run.outcome is Outcome.ACCEPTED


def test_budget_exhaustion_is_a_normal_ending():
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
        budget=Budget(max_calls=1),
    )
    assert run.outcome is Outcome.BUDGET_EXHAUSTED


def test_extract_json_handles_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('解释在前 {"a": 2} 解释在后') == {"a": 2}
    with pytest.raises(Exception):
        extract_json("完全没有 JSON")


def test_authoring_rejects_forbidden_paths_before_gate():
    with pytest.raises(Exception, match="禁止修改"):
        AuthoringInterface().parse_proposal(
            {"files": {"tests/test_parser.py": "def test_x(): pass\n"}, "rationale": "改测试"},
            CONTRACT,
        )


def test_get_interface_rejects_unknown_name():
    with pytest.raises(KeyError):
        get_interface("nope")


def test_audit_records_contract_hash(tmp_path: Path):
    """审计的 run_start 必须记录契约哈希，否则论文无法说明每次运行用的哪版契约。"""
    from dbw.audit import AuditLog
    from dbw.contract import contract_hash

    log = AuditLog(tmp_path / "audit.jsonl")
    execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(
            deepseek=ScriptedAdapter("deepseek", [PATCH]),
            kimi=ScriptedAdapter("kimi", [NO_FINDINGS]),
        ),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
        audit=log,
    )
    log.close()

    events = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    start = next(event for event in events if event["event"] == "run_start")
    assert start["contract_hash"] == contract_hash(CONTRACT)
