"""SEARCH/REPLACE 编辑格式的测试：匹配、歧义、换行风格、接口与执行循环的接线。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import load_contract  # noqa: E402
from dbw.edit_format import (  # noqa: E402
    DIVIDER_MARKER,
    REPLACE_MARKER,
    SEARCH_MARKER,
    Edit,
    EditError,
    apply_edit,
    apply_edits_to_workspace,
    compute_edits,
    parse_markers,
    parse_patch_text,
)
from dbw.protocol.interfaces import AuthoringInterface  # noqa: E402

SAMPLE = """def greet(name):
    return f"hi {name}"


def bye(name):
    return f"bye {name}"
"""


# ---------------------------------------------------------------- 基本匹配

def test_replaces_unique_fragment():
    edit = Edit(path="m.py", search='    return f"hi {name}"', replace='    return f"hello {name}"')
    out = apply_edit(SAMPLE, edit)
    assert "hello" in out
    assert 'f"hi {name}"' not in out


def test_reject_when_not_found():
    edit = Edit(path="m.py", search="完全不存在的一段", replace="x")
    with pytest.raises(EditError, match="第 1 行在文件里不存在"):
        apply_edit(SAMPLE, edit)


def test_reports_which_line_differs():
    """片段首行对得上但后面有差异时，要指出第几行不对。"""
    content = "def f(self, elem):\n    return 1\n"
    edit = Edit(path="m.py", search="def f(self, elem):\n    return 999", replace="x")
    with pytest.raises(EditError, match="第 2 行与文件不一致"):
        apply_edit(content, edit)


def test_reject_when_ambiguous():
    content = "x = 1\ny = 2\nx = 1\n"
    edit = Edit(path="m.py", search="x = 1", replace="x = 9")
    with pytest.raises(EditError, match="出现在 2 处"):
        apply_edit(content, edit)


def test_indentation_tolerance_but_verbatim_insert():
    content = "if x:\n        deep()\n"  # 8 空格缩进
    edit = Edit(path="m.py", search="if x:\n    deep()", replace="if x:\n    shallow()")
    out = apply_edit(content, edit)
    assert "shallow()" in out


def test_deletion_by_empty_replace():
    edit = Edit(path="m.py", search="def bye(name):\n    return f\"bye {name}\"", replace="")
    out = apply_edit(SAMPLE, edit)
    assert "bye" not in out
    assert "greet" in out


def test_crlf_file_keeps_crlf():
    content = "a = 1\r\nb = 2\r\n"
    edit = Edit(path="m.py", search="b = 2", replace="b = 3")
    out = apply_edit(content, edit)
    assert "\r\n" in out
    assert "b = 3\r\n" in out


# ---------------------------------------------------------------- 解析

def test_parse_markers_roundtrip():
    body = f"{SEARCH_MARKER}\nold_line\n{DIVIDER_MARKER}\nnew_line\n{REPLACE_MARKER}"
    search, replace = parse_markers(body)
    assert search == "old_line"
    assert replace == "new_line"


def test_parse_markers_rejects_broken_block():
    with pytest.raises(EditError):
        parse_markers(f"{SEARCH_MARKER}\n没有分隔行\n{REPLACE_MARKER}")


def test_parse_patch_text_handles_two_blocks_same_file():
    text = (
        "pkg/m.py\n"
        f"{SEARCH_MARKER}\na = 1\n{DIVIDER_MARKER}\na = 2\n{REPLACE_MARKER}\n"
        f"{SEARCH_MARKER}\nb = 1\n{DIVIDER_MARKER}\nb = 2\n{REPLACE_MARKER}\n"
    )
    edits = parse_patch_text(text)
    assert [e.path for e in edits] == ["pkg/m.py", "pkg/m.py"]
    assert [e.search for e in edits] == ["a = 1", "b = 1"]


# ---------------------------------------------------------------- 工作区

def test_compute_edits_does_not_write(tmp_path: Path):
    target = tmp_path / "m.py"
    target.write_text(SAMPLE, encoding="utf-8")
    result = compute_edits(tmp_path, [Edit("m.py", 'f"hi {name}"', 'f"hello {name}"')])

    assert result.ok
    assert "hello" in result.changed["m.py"]
    assert 'f"hi {name}"' in target.read_text(encoding="utf-8"), "compute 不应改盘"


def test_apply_edits_to_workspace_writes(tmp_path: Path):
    target = tmp_path / "m.py"
    target.write_text(SAMPLE, encoding="utf-8")
    result = apply_edits_to_workspace(tmp_path, [Edit("m.py", 'f"hi {name}"', 'f"hello {name}"')])

    assert result.ok and result.applied == 1
    assert "hello" in target.read_text(encoding="utf-8")


def test_any_failure_blocks_all_writes(tmp_path: Path):
    good = tmp_path / "a.py"
    good.write_text("x = 1\n", encoding="utf-8")
    bad = tmp_path / "b.py"
    bad.write_text("y = 1\n", encoding="utf-8")

    result = apply_edits_to_workspace(
        tmp_path,
        [Edit("a.py", "x = 1", "x = 2"), Edit("b.py", "不存在", "z")],
    )
    assert not result.ok
    assert good.read_text(encoding="utf-8") == "x = 1\n", "有一处失败就整体不写"


def test_path_outside_workspace_is_rejected(tmp_path: Path):
    work = tmp_path / "ws"
    work.mkdir()
    result = compute_edits(work, [Edit("../escape.py", "a", "b")])
    assert not result.ok
    assert any("工作区之外" in item for item in result.failures)


# ---------------------------------------------------------------- 接口接线

def _contract_for(tmp_path: Path):
    contract_file = tmp_path / "contract.yaml"
    contract_file.write_text(
        "task_id: t\nrepo: .\ndoD:\n  tests: [pytest]\n  diff: {max_files: 3, max_lines: 50, forbid: [tests/]}\n",
        encoding="utf-8",
    )
    return load_contract(contract_file)


def test_interface_accepts_edits_and_expands_to_full_files(tmp_path: Path):
    (tmp_path / "m.py").write_text(SAMPLE, encoding="utf-8")
    contract = _contract_for(tmp_path)

    patch = AuthoringInterface().parse_proposal(
        {"edits": [{"path": "m.py", "search": 'f"hi {name}"', "replace": 'f"hello {name}"'}], "rationale": "改问候"},
        contract,
    )

    assert list(patch.files) == ["m.py"]
    assert "hello" in patch.files["m.py"]
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == SAMPLE, "解析不应改盘"


def test_interface_rejects_edit_on_forbidden_path(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("x = 1\n", encoding="utf-8")
    contract = _contract_for(tmp_path)

    with pytest.raises(Exception, match="禁止修改"):
        AuthoringInterface().parse_proposal(
            {"edits": [{"path": "tests/t.py", "search": "x = 1", "replace": "x = 2"}], "rationale": "改测试"},
            contract,
        )


def test_interface_reports_unmatched_fragment_clearly(tmp_path: Path):
    (tmp_path / "m.py").write_text(SAMPLE, encoding="utf-8")
    contract = _contract_for(tmp_path)

    with pytest.raises(Exception, match="第 1 行在文件里不存在"):
        AuthoringInterface().parse_proposal(
            {"edits": [{"path": "m.py", "search": "根本不存在", "replace": "x"}], "rationale": "试试"},
            contract,
        )
