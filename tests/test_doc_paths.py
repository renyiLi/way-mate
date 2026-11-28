"""文档路径检查：论文与接口文档里带目录前缀的路径必须真实存在。

动机：论文里最容易出的错是"引用了不存在的文件"。
`mine_task.py` 曾经只在文档里存在而没有实现，这类问题肉眼很难发现。
这里只检查带前缀的路径（src/ scripts/ tests/ interface/ web/ tasks/ experiments/ docs/），
不带前缀的裸文件名（如 `task.json`）属于泛指，不检查。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(
    r"`((?:src|scripts|tests|interface|web|tasks|experiments|docs)/[A-Za-z0-9_./-]+)`"
)

# 描述上游仓库自身的路径，不是本仓库的文件
ALLOWLIST = {
    "src/minisweagent",
}


def doc_files() -> list[Path]:
    return sorted(ROOT.glob("docs/*.md")) + sorted(ROOT.glob("interface/*.md"))


def referenced_paths() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in doc_files():
        text = path.read_text(encoding="utf-8")
        for match in PATTERN.finditer(text):
            found.append((match.group(1).rstrip("/"), path.name))
    return found


def test_referenced_paths_exist():
    refs = referenced_paths()
    assert refs, "没有扫描到任何路径引用，检查正则或文档目录"

    missing = []
    for ref, doc in refs:
        if ref in ALLOWLIST or any(ref.startswith(prefix) for prefix in ALLOWLIST):
            continue
        if (ROOT / ref).exists():
            continue
        missing.append(f"{ref}（出现在 {doc}）")
    assert not missing, "文档引用了不存在的路径：" + "；".join(missing)
