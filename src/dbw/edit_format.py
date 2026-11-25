"""SEARCH/REPLACE 编辑格式：让模型只输出"改哪几行"，而不是重写整个文件。

为什么必须换格式：真实仓库里一个文件常常上千行，
让模型重写整份内容时，输出会在 max_tokens 处被截断，
写出来的文件语法都不对。实测在 tabulate（约 1000 行的 __init__.py）上，
整文件重写产生了 3001 行改动、pytest 连收集都失败。

匹配策略：
- 按行比较，且忽略每行首尾空白（模型常把缩进写歪一点）；
- 但**替换内容原样插入**，不做任何"智能修正"；
- 匹配到 0 处或多处都报错，绝不猜——猜错比报错危险得多；
- 换行风格随原文件（Windows 工作区是 CRLF，模型给的是 LF）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SEARCH_MARKER = "<<<<<<< SEARCH"
DIVIDER_MARKER = "======="
REPLACE_MARKER = ">>>>>>> REPLACE"

# 供提示词引用，避免模型自己发明标记
FORMAT_HELP = f"""用 SEARCH/REPLACE 块描述改动，每块只包含"要替换的原文"和"替换后的内容"：

{SEARCH_MARKER}
（原文件里逐字存在的一小段，通常 3-8 行）
{DIVIDER_MARKER}
（替换成什么）
{REPLACE_MARKER}

规则：
- SEARCH 片段必须在文件里唯一出现，且尽量短；
- 不要写省略号，不要把整个文件塞进来；
- 一次可以给多个块，但每块只能对应一处改动。"""


class EditError(ValueError):
    """编辑无法安全应用。message 会直接回给模型，必须写清楚哪里不对。"""


@dataclass(frozen=True)
class Edit:
    path: str
    search: str
    replace: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "search": self.search, "replace": self.replace}


@dataclass(frozen=True)
class EditResult:
    changed: dict[str, str]
    applied: int
    failures: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failures


def _newline_of(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"


def apply_edit(content: str, edit: Edit) -> str:
    """把一处改动应用到文件内容上。失败抛 EditError。

    两步：
    1. 先按原样子串匹配（最保真，能改行内片段、保留一切字节）；
    2. 匹配不到时退回按行匹配（容忍缩进与换行风格差异）。
    """
    if not edit.search.strip():
        raise EditError(f"{edit.path}: SEARCH 片段为空")

    exact = content.count(edit.search)
    if exact == 1:
        return content.replace(edit.search, edit.replace, 1)
    if exact > 1:
        raise EditError(
            f"{edit.path}: SEARCH 片段出现在 {exact} 处，无法确定改哪一处。请把片段写长一些，使其唯一。"
        )

    lines = content.splitlines(keepends=True)
    target = [line.strip() for line in edit.search.splitlines()]
    if not target:
        raise EditError(f"{edit.path}: SEARCH 片段为空")

    haystack = [line.strip() for line in lines]
    hits = [
        index
        for index in range(len(haystack) - len(target) + 1)
        if haystack[index : index + len(target)] == target
    ]

    if not hits:
        # 定位到具体哪一行不一致——只说“找不到”会误导模型（和排查的人）
        first_line = target[0]
        starts = [index for index, line in enumerate(haystack) if line == first_line]
        if not starts:
            raise EditError(
                f"{edit.path}: SEARCH 片段的第 1 行在文件里不存在：{first_line!r}。"
                "请从文件里逐字复制要替换的代码。"
            )
        start = starts[0]
        for offset, want in enumerate(target):
            position = start + offset
            actual = haystack[position] if position < len(haystack) else None
            if actual != want:
                raise EditError(
                    f"{edit.path}: SEARCH 片段第 {offset + 1} 行与文件不一致："
                    f"片段写的是 {want!r}，文件第 {position + 1} 行是 {actual!r}。"
                    "请从文件里逐字复制。"
                )
        raise EditError(
            f"{edit.path}: 找不到 SEARCH 片段（首行：{first_line!r}）。"
            "请确保片段与文件内容逐字一致，不要包含行号或省略号。"
        )
    if len(hits) > 1:
        raise EditError(
            f"{edit.path}: SEARCH 片段出现在 {len(hits)} 处，无法确定改哪一处。"
            "请把片段写长一些，使其唯一。"
        )

    start = hits[0]
    newline = _newline_of(lines[start])
    block = [f"{line}{newline}" for line in edit.replace.splitlines()] if edit.replace.strip() else []
    return "".join(lines[:start] + block + lines[start + len(target) :])


def apply_edits(content: str, edits: list[Edit]) -> str:
    for edit in edits:
        content = apply_edit(content, edit)
    return content


_PATH_LINE = re.compile(r"^\s*([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)\s*$")


def parse_patch_text(text: str) -> list[Edit]:
    """解析 aider 风格的文本：先是文件路径行，紧跟一个或多个 SEARCH/REPLACE 块。

    也接受"块前没有路径行"的形式——此时从之前的路径延续。
    """
    lines = text.replace("\r\n", "\n").split("\n")
    edits: list[Edit] = []
    current_path: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        match = _PATH_LINE.match(line)
        if match and index + 1 < len(lines) and lines[index + 1].strip().startswith(SEARCH_MARKER):
            current_path = match.group(1).replace("\\", "/")

        if not stripped.startswith(SEARCH_MARKER):
            index += 1
            continue

        if current_path is None:
            raise EditError(f"第 {index + 1} 行：SEARCH 块之前没有指定文件路径")

        # 收集 SEARCH 段
        index += 1
        search_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != DIVIDER_MARKER:
            if lines[index].strip().startswith(REPLACE_MARKER):
                raise EditError(f"第 {index + 1} 行：缺少 {DIVIDER_MARKER} 分隔行")
            search_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise EditError(f"{current_path}: SEARCH 块没有闭合")

        index += 1  # 跳过 =======
        replace_lines: list[str] = []
        while index < len(lines) and not lines[index].strip().startswith(REPLACE_MARKER):
            replace_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise EditError(f"{current_path}: REPLACE 块没有闭合（缺少 {REPLACE_MARKER}）")

        edits.append(
            Edit(
                path=current_path,
                search="\n".join(search_lines).strip("\n"),
                replace="\n".join(replace_lines).strip("\n"),
            )
        )
        index += 1

    return edits


def parse_markers(body: str) -> tuple[str, str]:
    """解析一个 SEARCH/REPLACE 块（不含文件路径行）。

    执行循环里用：模型输出的 edit:路径 块只有标记，没有路径行。
    """
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or not lines[0].strip().startswith(SEARCH_MARKER):
        raise EditError(f"编辑块必须以 {SEARCH_MARKER} 开头")

    index = 1
    search_lines: list[str] = []
    while index < len(lines) and lines[index].strip() != DIVIDER_MARKER:
        search_lines.append(lines[index])
        index += 1
    if index >= len(lines):
        raise EditError(f"缺少 {DIVIDER_MARKER} 分隔行")

    index += 1
    replace_lines: list[str] = []
    while index < len(lines) and not lines[index].strip().startswith(REPLACE_MARKER):
        replace_lines.append(lines[index])
        index += 1
    if index >= len(lines):
        raise EditError(f"缺少 {REPLACE_MARKER} 结束行")

    newline = "\n"
    return newline.join(search_lines).strip(), newline.join(replace_lines).strip()


def compute_edits(root: Path, edits: list[Edit]) -> EditResult:
    """在内存里算出改动后的文件内容，**不写盘**。

    解析阶段用它：补丁要交给闸门判定，不能解析时就把工作区改了。
    """
    root = Path(root).resolve()
    staged: dict[str, str] = {}
    failures: list[str] = []

    for edit in edits:
        target = (root / edit.path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            failures.append(f"{edit.path}: 路径在工作区之外")
            continue
        if not target.is_file():
            failures.append(f"{edit.path}: 文件不存在")
            continue

        base = staged.get(edit.path)
        if base is None:
            base = target.read_text(encoding="utf-8")
        try:
            staged[edit.path] = apply_edit(base, edit)
        except EditError as exc:
            failures.append(str(exc))

    if failures:
        return EditResult(changed={}, applied=0, failures=tuple(failures))
    return EditResult(changed=staged, applied=len(edits))


def apply_edits_to_workspace(root: Path, edits: list[Edit]) -> EditResult:
    """算出来并写盘。任何一处失败都不写，避免“改了一半”的中间状态。"""
    result = compute_edits(root, edits)
    if result.failures:
        return result
    for relative, content in result.changed.items():
        # 二进制写，避免把 \n 翻成 CRLF 造成“整文件 diff”
        (root / relative).write_bytes(content.encode("utf-8"))
    return result
