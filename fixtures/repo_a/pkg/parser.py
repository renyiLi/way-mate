"""配置文本的 key=value 解析器（示例仓库，用于演示闸门判定）。

这是一个刻意带有缺陷的版本，`tests/test_parser.py` 中有两条用例会失败。
"""

from __future__ import annotations


def parse_kv(text: str) -> dict[str, str]:
    """解析 key=value 形式的配置文本。

    规则：空行与整行注释（以 # 开头）忽略；其余每行必须包含 "="。
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result
