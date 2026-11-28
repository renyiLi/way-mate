"""把前置部分、第 1–7 章与参考文献合并成论文正文，并自动生成目录。

设计原则：**合并稿是产物，不是源文件**。修改请改各章，然后重新运行本脚本。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "docs" / "论文-前置部分.md"
OUT = ROOT / "docs" / "论文-正文.md"
CHAPTERS = [
    (1, "docs/论文-第1章-绪论.md"),
    (2, "docs/论文-第2章-相关技术与研究现状.md"),
    (3, "docs/论文-第3章-需求分析.md"),
    (4, "docs/论文-第4章-系统设计.md"),
    (5, "docs/论文-第5章-系统实现.md"),
    (6, "docs/论文-第6章-实验与结果分析.md"),
    (7, "docs/论文-第7章-总结与展望.md"),
]
REFS = ROOT / "docs" / "论文-参考文献.md"
TOC_HEAD = "## 目录"

HEADING = re.compile(r"^(#{1,2})\s+(.*)$")


def split_front(text: str) -> tuple[str, str]:
    """把前置部分拆成「目录之前」与「目录之后」，目录由本脚本重新生成。"""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == TOC_HEAD)
    except StopIteration:
        raise SystemExit("前置部分里找不到「## 目录」") from None
    end = start + 1
    while end < len(lines) and not lines[end].startswith("# "):
        end += 1
    return "\n".join(lines[:start]).rstrip(), "\n".join(lines[end:]).strip()


def toc_for(chapter_titles: list[tuple[int, str, list[str]]]) -> str:
    rows = ["- 摘要", "- Abstract"]
    for number, title, sections in chapter_titles:
        rows.append(f"- {title}")
        rows.extend(f"  - {section}" for section in sections)
    rows.append("- 参考文献")
    return "\n".join(rows)


def collect() -> tuple[list[tuple[int, str, list[str]]], list[str]]:
    collected = []
    bodies = []
    for number, rel in CHAPTERS:
        text = (ROOT / rel).read_text(encoding="utf-8").strip()
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip()
        sections = [
            m.group(2).strip()
            for line in lines
            if (m := HEADING.match(line)) and m.group(1) == "##"
        ]
        collected.append((number, title, sections))
        bodies.append(text)
    return collected, bodies


def main() -> int:
    front_top, _ = split_front(FRONT.read_text(encoding="utf-8"))
    chapters, bodies = collect()
    toc = toc_for(chapters)
    note = (
        "> 本文件由前置部分、第 1–7 章与参考文献的章节文件合并生成；"
        "修改请改各章源文件，然后重新执行 `python scripts/build_thesis.py`。"
    )
    references = REFS.read_text(encoding="utf-8").strip()

    merged = "\n\n".join(
        [front_top, TOC_HEAD, toc, "---", note, "---", *bodies, references]
    ).strip() + "\n"
    OUT.write_bytes(merged.encode("utf-8"))

    words = sum(len(line) for line in merged.splitlines() if not line.startswith("#"))
    print(f"已生成 {OUT.relative_to(ROOT)}：{merged.count(chr(10))} 行，约 {words} 字符（不含标题行）")
    print(f"目录收录 {sum(1 + len(s) for _, _, s in chapters) + 3} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
