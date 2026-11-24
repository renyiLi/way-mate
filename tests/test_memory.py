"""成长接口的测试：默认空实现必须零影响，注入点必须真能生效。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helpers import ScriptedAdapter, gate, registry  # noqa: E402  同目录测试工具
from test_protocol import NO_FINDINGS, PATCH  # noqa: E402

from dbw.contract import load_contract  # noqa: E402
from dbw.memory import (  # noqa: E402
    Episode,
    NullExperienceStore,
    NullMemoryHook,
    jaccard,
    render_experience,
    task_signature,
)
from dbw.protocol.machine import SeatAssignment, execute  # noqa: E402

CONTRACT = load_contract(ROOT / "fixtures" / "repo_a" / "contract.yaml")
TASK = {"task_id": "fix-parser-inline-comment", "issue": "行内注释未剥离"}


def test_null_store_is_silent():
    store = NullExperienceStore()
    store.record(Episode(run_id="r1", signature="s", protocol="2p.single", seats={}, outcome="accepted", rounds=1, tokens=10))
    assert store.recall("s") == ()
    assert store.summary() == {"recorded": 0, "recalled": 0}


def test_null_hook_injects_nothing():
    assert NullMemoryHook().before_propose(task=TASK, contract=CONTRACT) == ""


def test_signature_is_stable_and_discriminating():
    assert task_signature(CONTRACT, {"issue": "行内注释未剥离"}) == task_signature(
        CONTRACT, {"issue": "行内注释未剥离"}
    )
    assert task_signature(CONTRACT, {"issue": "行内注释未剥离"}) != task_signature(
        CONTRACT, {"issue": "任务图有环"}
    )


def test_jaccard_bounds():
    assert jaccard("a b c", "a b c") == 1.0
    assert jaccard("a b", "c d") == 0.0
    assert 0.0 < jaccard("a b c", "a b d") < 1.0


def test_render_experience_writes_lessons_not_patches():
    episodes = (
        Episode(
            run_id="r1",
            signature="s",
            protocol="2p.single",
            seats={"proposer": "deepseek"},
            outcome="escalated",
            rounds=3,
            tokens=900,
            gate_failures=("tests",),
            lesson="不要按 # 直接切分，URL 里的 # 会被截断",
        ),
    )
    text = render_experience(episodes)
    assert "历史经验" in text
    assert "教训" in text
    assert "tests" in text
    assert "def " not in text, "不得注入完整代码，否则成长退化成背题"


def test_runner_injects_memory_block_into_proposer_prompt():
    class FakeMemory:
        def before_propose(self, *, task, contract):
            return "## 同类任务的历史经验\n1. 教训：先校验 '=' 再切分"

    proposer = ScriptedAdapter("deepseek", [PATCH])
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(deepseek=proposer, kimi=ScriptedAdapter("kimi", [NO_FINDINGS])),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
        memory=FakeMemory(),
    )
    assert run.outcome.value == "accepted"
    user_prompt = proposer.calls[0].messages[-1].content
    assert "历史经验" in user_prompt
    assert "先校验 '=' 再切分" in user_prompt


def test_runner_without_memory_has_no_block():
    proposer = ScriptedAdapter("deepseek", [PATCH])
    run = execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(deepseek=proposer, kimi=ScriptedAdapter("kimi", [NO_FINDINGS])),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
    )
    assert run.outcome.value == "accepted"
    assert "历史经验" not in proposer.calls[0].messages[-1].content
