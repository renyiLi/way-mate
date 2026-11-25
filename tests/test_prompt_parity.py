"""提示词一致性测试：单轮后端与协议机必须发同一段用户提示。

这条测试守的是一个实验方法问题：如果两条路径各自拼提示词，
"单轮 vs 2 人协议"的对比就混进了提示词变量，数据不可比。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helpers import ScriptedAdapter, gate, registry  # noqa: E402

from dbw.contract import load_contract  # noqa: E402
from dbw.protocol.interfaces import render_propose_prompt  # noqa: E402
from dbw.protocol.machine import SeatAssignment, execute  # noqa: E402

CONTRACT = load_contract(ROOT / "fixtures" / "repo_a" / "contract.yaml")
TASK = {"task_id": CONTRACT.task_id, "issue": "行内注释未剥离", "repo_layout": ["pkg/parser.py"]}
PATCH = {
    "files": {"pkg/parser.py": "def parse_kv(text: str) -> dict[str, str]:\n    return {}\n"},
    "rationale": "最小改动",
}


def test_render_contains_task_and_contract():
    text = render_propose_prompt(TASK, CONTRACT)
    assert "行内注释未剥离" in text
    assert "验收契约" in text
    assert "missing '='" in text, "契约里的精确断言必须出现在提案提示里"


def test_runner_sends_the_shared_prompt():
    proposer = ScriptedAdapter("deepseek", [PATCH])
    execute(
        interface="authoring",
        task=TASK,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(deepseek=proposer, kimi=ScriptedAdapter("kimi", [{"findings": []}])),
        evaluate=lambda patch, outcome: gate(True),
        contract=CONTRACT,
        max_repair_rounds=0,
    )

    sent = proposer.calls[0].messages[-1].content
    assert sent == render_propose_prompt(TASK, CONTRACT), (
        "协议机发出的提案提示必须与单轮后端完全一致，否则实验不可比"
    )


def test_second_round_keeps_full_context():
    """第二轮仍要发整份文件：SEARCH/REPLACE 要求原文逐字一致。

    曾试过省 token 抽掉 current_file，结果第二轮开始猜片段，
    在 5000 行的 more.py 上三次全败（实验 11/25/12）。
    """
    big = "x" * 4000
    task = {**TASK, "current_file": big}
    proposer = ScriptedAdapter("deepseek", [PATCH])
    run = execute(
        interface="authoring",
        task=task,
        seats=SeatAssignment("deepseek", "kimi"),
        registry=registry(deepseek=proposer, kimi=ScriptedAdapter("kimi", [{"findings": []}])),
        evaluate=lambda patch, outcome: gate(False),
        contract=CONTRACT,
        max_repair_rounds=1,
    )

    assert len(run.rounds) == 2
    assert big in proposer.calls[0].messages[-1].content, "第一轮必须给出完整文件"
    assert big in proposer.calls[1].messages[-1].content, "第二轮也必须给出，否则模型会猜片段"
