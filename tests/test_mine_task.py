"""mine_task 的解析逻辑测试：pytest 输出 → 失败用例 node id。

不需要真的跑 pytest，也不需要仓库。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mine_task import parse_failed_nodes  # noqa: E402


def test_parses_plain_failed_lines():
    output = """
FAILED tests/test_more.py::PeekableTests::test_slicing - AssertionError
FAILED tests/test_more.py::BucketTests::test_keys - AssertionError
2 failed, 10 passed
"""
    assert parse_failed_nodes(output) == [
        "tests/test_more.py::PeekableTests::test_slicing",
        "tests/test_more.py::BucketTests::test_keys",
    ]


def test_normalises_windows_separators():
    output = "FAILED tests\\test_more.py::Class::test_x - AssertionError"
    assert parse_failed_nodes(output) == ["tests/test_more.py::Class::test_x"]


def test_parses_subtest_failures_to_parent_node():
    output = """
SUBFAILED(max_count=-1, items=['a']) tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count
SUBFAILED(max_count=0, items=[]) tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count
"""
    assert parse_failed_nodes(output) == [
        "tests/test_more.py::ConstrainedBatchesTests::test_nonpositive_max_count"
    ]


def test_ignores_non_failure_lines_and_dedupes():
    output = """
PASSED tests/test_more.py::Class::test_ok
FAILED tests/test_more.py::Class::test_a - boom
FAILED tests/test_more.py::Class::test_a - boom
1 failed
"""
    assert parse_failed_nodes(output) == ["tests/test_more.py::Class::test_a"]


def test_returns_empty_when_all_green():
    assert parse_failed_nodes("767 passed, 21194 subtests passed in 28s") == []
