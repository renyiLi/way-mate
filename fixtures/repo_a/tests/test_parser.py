"""示例仓库的验收用例。这些文件属于"禁止修改"路径，改它们不能代替改代码。"""

import pytest

from pkg.parser import parse_kv


def test_empty_text():
    assert parse_kv("") == {}


def test_basic_pairs():
    assert parse_kv("a=1\nb=2") == {"a": "1", "b": "2"}


def test_comment_line_ignored():
    assert parse_kv("# comment\na=1") == {"a": "1"}


def test_inline_comment_stripped():
    assert parse_kv("a=1  # note") == {"a": "1"}


def test_missing_equals_raises():
    with pytest.raises(ValueError, match="missing '='"):
        parse_kv("a=1\nbroken")
