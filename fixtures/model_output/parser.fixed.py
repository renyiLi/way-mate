"""配置文本的 key=value 解析器（示例仓库，用于演示闸门判定）。

这是修复后的正确版本，用于演示"补丁 → 闸门全绿 → 可交付"。
"""

from __future__ import annotations

import re

# 仅把"空白 + #"视为行内注释，保留 URL 中紧邻字符的 #（如 http://x/#frag）
_INLINE_COMMENT = re.compile(r"\s+#")


def parse_kv(text: str) -> dict[str, str]:
    """解析 key=value 形式的配置文本。

    规则：空行与整行注释（以 # 开头）忽略；行内注释以"空白 + #"起始；
    其余每行必须包含 "="，否则抛出带行号的 ValueError。
    """
    result: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"line {lineno}: missing '=' in {raw!r}")
        key, value = line.split("=", 1)
        value = _INLINE_COMMENT.split(value, maxsplit=1)[0]
        result[key.strip()] = value.strip()
    return result
