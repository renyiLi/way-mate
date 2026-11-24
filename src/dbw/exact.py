"""精确断言：从测试源码静态提取"必须逐字出现"的字符串。

为什么需要它：验收要求常常藏在 `pytest.raises(..., match=...)` 里，
模型只能从 25 行 traceback 里揣摩，于是把消息"优化"成中文，测试就红了。
把这类硬约束提到契约里，提案与复核两个座位都能直接看到。

三条纪律：
1. 只收"字符串必须逐字匹配"这一类；接口名、路径、禁改项各有专门字段，不重复收；
2. `match` 是正则，含元字符时不收（宁可少收，不可错收）；
3. 本模块只产出**建议**，契约仍由人确认——系统无权改验收标准。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# 正则元字符：一旦出现，`match` 就不再是字面量
META_CHARS = set(r".^$*+?{}[]\|()")

ORIGINS = ("auto", "manual")


class ExactError(ValueError):
    """精确断言条目不合法。"""


@dataclass(frozen=True)
class ExactSpec:
    exception: str
    contains: str
    source: str = ""
    origin: str = "auto"

    def key(self) -> tuple[str, str]:
        return (self.exception, self.contains)

    def to_dict(self) -> dict[str, str]:
        return {
            "exception": self.exception,
            "contains": self.contains,
            "source": self.source,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExactSpec":
        exception = str(data.get("exception", "")).strip()
        contains = str(data.get("contains", "")).strip()
        if not exception:
            raise ExactError("exact.exception 不能为空")
        if not contains:
            raise ExactError("exact.contains 不能为空")
        origin = str(data.get("origin", "auto")).strip() or "auto"
        if origin not in ORIGINS:
            raise ExactError(f"exact.origin 只能是 {' / '.join(ORIGINS)}，实际 {origin!r}")
        return cls(
            exception=exception,
            contains=contains,
            source=str(data.get("source", "")).strip(),
            origin=origin,
        )


def is_literal(pattern: str) -> bool:
    """`match` 能否当字面量用。含元字符的一律不认。"""
    return not (set(pattern) & META_CHARS)


def extract_from_source(source: str, filename: str = "<string>") -> tuple[list[ExactSpec], list[str]]:
    """从一份测试源码里提取精确断言。返回（采纳项, 跳过说明）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # 测试文件本身有语法错误时不静默跳过
        return [], [f"{filename}: 无法解析（{exc.msg}，行 {exc.lineno}）"]

    specs: list[ExactSpec] = []
    notes: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "raises":
                continue

            exception = ast.unparse(call.args[0]) if call.args else "Exception"
            pattern: str | None = None
            for keyword in call.keywords:
                if keyword.arg == "match" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        pattern = keyword.value.value

            where = f"{filename}:{node.lineno}"
            if pattern is None:
                notes.append(f"{where}: 只断言异常类型（{exception}），没有 match")
                continue
            if not is_literal(pattern):
                notes.append(f"{where}: match 含正则元字符，需人工确认：{pattern!r}")
                continue

            specs.append(ExactSpec(exception=exception, contains=pattern, source=where, origin="auto"))

    return specs, notes


def extract_from_paths(paths: list[Path]) -> tuple[list[ExactSpec], list[str]]:
    specs: list[ExactSpec] = []
    notes: list[str] = []
    for path in paths:
        files = [path] if path.is_file() else sorted(path.rglob("test_*.py"))
        for file in files:
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                notes.append(f"{file}: 读取失败（{exc}）")
                continue
            found, skipped = extract_from_source(text, str(file))
            specs.extend(found)
            notes.extend(skipped)
    return specs, notes


def render_exact_block(specs: tuple[ExactSpec, ...] | list[ExactSpec]) -> str:
    """渲染成提示词里的硬约束区块。空列表返回空串。"""
    if not specs:
        return ""
    lines = ["## 硬约束：不满足即判失败，不要改写措辞"]
    for spec in specs:
        origin = "契约声明" if spec.origin == "manual" else "从测试提取"
        suffix = f"（{origin}：{spec.source}）" if spec.source else f"（{origin}）"
        lines.append(f"- 抛出 `{spec.exception}` 时，消息必须逐字包含 `{spec.contains}`{suffix}")
    return "\n".join(lines)


def check_consistency(declared: tuple[ExactSpec, ...], extracted: list[ExactSpec]) -> list[str]:
    """检查契约与测试是否漂移。返回问题列表，空表示一致。

    这是"怎么保证契约和测试一致"的可执行答案。
    """
    problems: list[str] = []
    declared_keys = {spec.key() for spec in declared}
    extracted_keys = {spec.key() for spec in extracted}

    for spec in declared:
        if spec.key() not in extracted_keys:
            problems.append(f"契约声明了但测试里找不到：{spec.exception} → {spec.contains!r}")
    for spec in extracted:
        if spec.key() not in declared_keys:
            problems.append(f"测试里有但契约没声明：{spec.exception} → {spec.contains!r}")
    return problems
