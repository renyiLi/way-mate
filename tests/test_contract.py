"""契约解析器的单元测试：确保"契约写错就立刻报错"，而不是被静默忽略。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import Contract, ContractError, load_contract  # noqa: E402

FIXTURE = ROOT / "fixtures" / "repo_a" / "contract.yaml"


def test_loads_fixture_contract():
    contract = load_contract(FIXTURE)
    assert isinstance(contract, Contract)
    assert contract.task_id == "fix-parser-inline-comment"
    assert contract.repo.is_dir()
    assert len(contract.tests) == 1
    assert contract.tests[0].cmd.startswith("pytest")
    assert contract.api.frozen == ("pkg.parser.parse_kv",)
    assert "tests/" in contract.diff.forbid
    assert contract.loop.repair_rounds == 3


def test_pytest_command_uses_current_interpreter():
    contract = load_contract(FIXTURE)
    argv = contract.tests[0].argv
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]


def test_unknown_field_is_rejected(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "task_id: t\nrepo: .\ndoD:\n  test:\n    - cmd: pytest\n",
        encoding="utf-8",
    )
    with pytest.raises(ContractError, match="未知字段"):
        load_contract(path)


def test_missing_tests_is_rejected(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("task_id: t\nrepo: .\ndoD: {}\n", encoding="utf-8")
    with pytest.raises(ContractError, match="doD.tests"):
        load_contract(path)


def test_bad_yaml_is_rejected(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("task_id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ContractError, match="YAML"):
        load_contract(path)


def test_to_dict_round_trips_core_fields():
    contract = load_contract(FIXTURE)
    data = contract.to_dict()
    assert data["task_id"] == contract.task_id
    assert data["doD"]["diff"]["max_lines"] == contract.diff.max_lines
    assert data["doD"]["api"]["frozen"] == list(contract.api.frozen)
