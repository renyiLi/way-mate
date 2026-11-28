"""导出脚本的纯函数测试：分组规则与失败归因。

不依赖 audit 日志，也不需要跑任何实验。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_runs as ex  # noqa: E402


def record(run_id: str, protocol: str = "single-shot", task_id: str = "t", seats=None) -> dict:
    return {
        "run_id": run_id,
        "protocol": protocol,
        "task_id": task_id,
        "seats": seats or {},
        "gate": None,
        "outcome": "",
        "reason": "",
    }


def test_classifies_main_experiment():
    assert ex.classify_experiment(record("20261128T005047-239822")) == (
        "11/28/1",
        "主实验（每任务 5 次）",
    )


def test_classifies_first_round_and_retest():
    first = ex.classify_experiment(record("20261126T145401-aaaaaa", task_id="tabulate-366"))
    assert first == ("11/26/3", "首轮（每任务 3 次）")

    retest = ex.classify_experiment(record("20261126T150114-6e2b89", task_id="moreitertools-1285"))
    assert retest == ("11/26/3", "重测（1285，diff 上限 80→120）")

    # 同一时间窗口里的其它任务不属于重测
    other = ex.classify_experiment(record("20261126T150143-7f0660", task_id="tabulate-366"))
    assert other is None


def test_classifies_dual_variants():
    same = record(
        "20261127T160608-bb894b",
        protocol="3p.dual",
        seats={"proposer": "deepseek/deepseek-chat", "proposer_b": "deepseek/deepseek-chat"},
    )
    assert ex.classify_experiment(same) == ("11/27/3", "同模型双提案")

    cross = record(
        "20261127T161510-584b9d",
        protocol="3p.dual",
        seats={"proposer": "deepseek/deepseek-chat", "proposer_b": "ark/doubao"},
    )
    assert ex.classify_experiment(cross) == ("11/27/3", "跨模型双提案")


def test_ignores_dual_runs_before_the_batch():
    """16:00 之前的双提案属于 11/27/1，不属于 11/27/3。"""
    early = record(
        "20261127T143725-cef92c",
        protocol="3p.dual",
        seats={"proposer": "deepseek/deepseek-chat", "proposer_b": "ark/doubao"},
    )
    assert ex.classify_experiment(early) is None


def test_fail_reason_prefers_reason_over_gate():
    rec = record("x")
    rec["outcome"] = "rejected"
    rec["reason"] = "输出不合法：提案不合法"
    rec["gate"] = {"all_green": False, "results": [{"name": "tests", "ok": False}]}
    assert ex.fail_reason(rec) == "提案不合法"


def test_fail_reason_lists_multiple_gates():
    rec = record("x")
    rec["outcome"] = "rejected"
    rec["gate"] = {
        "all_green": False,
        "results": [{"name": "tests", "ok": False}, {"name": "lint", "ok": False}],
    }
    assert ex.fail_reason(rec) == "测试未通过；lint 新增问题"


def test_gate_cell_distinguishes_not_entered():
    rec = record("x")
    rec["gate"] = None
    assert ex.gate_cell(rec, "tests") == "未进入闸门"


def test_contract_cell_marks_old_format_as_missing():
    rec = record("x")
    rec["contract_hash"] = '{"doD": {"api": '
    assert ex.contract_cell(rec).startswith("缺失")
    rec["contract_hash"] = "55a9ff96988144b2"
    assert ex.contract_cell(rec) == "55a9ff96988144b2"
