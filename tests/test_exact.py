"""精确断言的测试：提取规则、正则排除、契约解析、注入位置、一致性检查。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import ContractError, load_contract  # noqa: E402
from dbw.exact import (  # noqa: E402
    ExactSpec,
    check_consistency,
    extract_from_paths,
    extract_from_source,
    is_literal,
    render_exact_block,
)
from dbw.protocol.interfaces import AuthoringInterface, ReviewInterface  # noqa: E402

FIXTURE_CONTRACT = ROOT / "fixtures" / "repo_a" / "contract.yaml"
FIXTURE_TESTS = ROOT / "fixtures" / "repo_a" / "tests"


# ---------------------------------------------------------------- 提取

def test_extracts_literal_from_fixture():
    specs, notes = extract_from_paths([FIXTURE_TESTS])
    assert len(specs) == 1
    spec = specs[0]
    assert spec.exception == "ValueError"
    assert spec.contains == "missing '='"
    assert spec.origin == "auto"
    assert spec.source.endswith("test_parser.py:25")
    assert notes == []


def test_regex_pattern_is_skipped_not_guessed():
    source = '''
import pytest
def test_x():
    with pytest.raises(ValueError, match="missing '='|missing equals"):
        pass
'''
    specs, notes = extract_from_source(source, "t.py")
    assert specs == [], "含正则元字符时不得当字面量收"
    assert any("元字符" in note for note in notes)


def test_type_only_assertion_is_skipped():
    source = """
import pytest
def test_x():
    with pytest.raises(ValueError):
        pass
"""
    specs, notes = extract_from_source(source, "t.py")
    assert specs == []
    assert any("没有 match" in note for note in notes)


def test_broken_test_file_is_reported_not_swallowed():
    specs, notes = extract_from_source("def broken(:\n", "bad.py")
    assert specs == []
    assert any("无法解析" in note for note in notes)


def test_is_literal_rejects_metachars():
    assert is_literal("missing '='")
    assert not is_literal("a.*b")
    assert not is_literal("(x)")


# ---------------------------------------------------------------- 契约

def test_contract_carries_exact_entries():
    contract = load_contract(FIXTURE_CONTRACT)
    assert len(contract.exact) == 1
    assert contract.exact[0].contains == "missing '='"
    assert contract.to_dict()["doD"]["exact"][0]["exception"] == "ValueError"


def test_invalid_origin_is_rejected(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "task_id: t\nrepo: .\ndoD:\n  tests: [pytest]\n"
        "  exact:\n    - {exception: ValueError, contains: \"x\", origin: robot}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="origin"):
        load_contract(path)


def test_empty_contains_is_rejected(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "task_id: t\nrepo: .\ndoD:\n  tests: [pytest]\n  exact:\n    - {exception: ValueError, contains: \"\"}\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError):
        load_contract(path)


# ---------------------------------------------------------------- 注入

def test_render_block_states_it_is_a_hard_constraint():
    text = render_exact_block((ExactSpec(exception="ValueError", contains="missing '='", source="t.py:25"),))
    assert "硬约束" in text
    assert "不要改写措辞" in text
    assert "`missing '='`" in text
    assert "t.py:25" in text


def test_render_empty_block_is_empty_string():
    assert render_exact_block(()) == ""


def test_authoring_prompt_contains_hard_constraint():
    contract = load_contract(FIXTURE_CONTRACT)
    prompt = AuthoringInterface().system_prompt(contract)
    assert "硬约束" in prompt
    assert "missing '='" in prompt


def test_review_prompt_contains_hard_constraint():
    contract = load_contract(FIXTURE_CONTRACT)
    prompt = ReviewInterface().system_prompt(contract)
    assert "硬约束" in prompt
    assert "missing '='" in prompt


# ---------------------------------------------------------------- 一致性

def test_fixture_contract_matches_tests():
    contract = load_contract(FIXTURE_CONTRACT)
    extracted, _notes = extract_from_paths([FIXTURE_TESTS])
    assert check_consistency(contract.exact, extracted) == []


def test_stale_contract_entry_is_reported():
    contract = load_contract(FIXTURE_CONTRACT)
    extracted, _ = extract_from_paths([FIXTURE_TESTS])
    stale = tuple(contract.exact) + (ExactSpec(exception="ValueError", contains="不再存在的串", origin="manual"),)
    problems = check_consistency(stale, extracted)
    assert any("测试里找不到" in problem for problem in problems)


def test_missing_contract_entry_is_reported():
    extracted, _ = extract_from_paths([FIXTURE_TESTS])
    problems = check_consistency((), extracted)
    assert any("契约没声明" in problem for problem in problems)
