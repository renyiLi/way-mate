"""工作区快照与差异提取。

不用 git：模型可能在循环里执行 `git checkout`、`git clean`，快照法不受影响。
"""

from __future__ import annotations

from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv", "venv", ".mypy_cache"}
MAX_FILE_BYTES = 200_000
MAX_FILES = 2_000


def snapshot(root: Path) -> dict[str, str]:
    """读取工作区里所有文本文件的内容。二进制与超大文件跳过。"""
    root = Path(root)
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            files[str(path.relative_to(root)).replace("\\", "/")] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if len(files) >= MAX_FILES:
            break
    return files


def diff(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    """返回被新增或修改的文件（含完整新内容）。删除的文件映射为空串。"""
    patch: dict[str, str] = {}
    for path, content in after.items():
        if before.get(path) != content:
            patch[path] = content
    for path in before:
        if path not in after:
            patch[path] = ""
    return patch
