"""闸门引擎：把契约翻译成可执行的判定。

设计原则：每个闸门只回答"这一项过没过"，不解释、不建议、不参与讨论。
所有判定结果都结构化，直接落审计日志，论文里的数据就是从这里出去的。
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .contract import Contract

_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "node_modules"}


@dataclass(frozen=True)
class GateResult:
    name: str
    ok: bool
    detail: str
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "metrics": self.metrics}


@dataclass
class GateReport:
    task_id: str
    results: list[GateResult]

    @property
    def all_green(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.ok]

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "all_green": self.all_green,
            "results": [r.to_dict() for r in self.results],
        }

    def summary(self) -> str:
        lines = []
        for result in self.results:
            mark = "PASS" if result.ok else "FAIL"
            lines.append(f"  [{mark}] {result.name:<6} {result.detail}")
        verdict = "全绿（可交付）" if self.all_green else f"{len(self.failures)} 项未通过"
        lines.append(f"  => {verdict}")
        return "\n".join(lines)


def _run(argv: list[str], cwd: Path, timeout_s: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return 127, "", f"命令不存在：{argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"超时（{timeout_s}s）"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ---------------------------------------------------------------- 测试闸门

def _parse_pytest_counts(output: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("passed", "failed", "error", "errors", "skipped"):
        match = re.search(rf"(\d+)\s+{key}\b", output)
        if match:
            counts[key.rstrip("s") if key == "errors" else key] = int(match.group(1))
    return counts


def gate_tests(contract: Contract, repo: Path) -> GateResult:
    if not contract.tests:
        return GateResult("tests", False, "契约未指定任何测试")

    total: dict[str, int] = {}
    for spec in contract.tests:
        argv = spec.argv
        code, out, err = _run(argv, repo, spec.timeout_s)
        combined = f"{out}\n{err}"
        counts = _parse_pytest_counts(combined)
        for key, value in counts.items():
            total[key] = total.get(key, 0) + value
        if code != 0:
            tail = _tail(combined, 12)
            return GateResult(
                "tests",
                False,
                f"`{spec.cmd}` 退出码 {code}；{_counts_text(counts)}",
                {"exit_code": code, "counts": counts, "tail": tail},
            )

    return GateResult("tests", True, f"全部通过；{_counts_text(total)}", {"counts": total})


_COUNT_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "error": "错误",
    "errors": "错误",
    "skipped": "跳过",
}


def _counts_text(counts: dict[str, int]) -> str:
    if not counts:
        return "无计数信息"
    return "，".join(f"{_COUNT_LABELS.get(key, key)} {value}" for key, value in sorted(counts.items()))


def _tail(text: str, lines: int) -> str:
    rows = [r for r in text.splitlines() if r.strip()]
    return "\n".join(rows[-lines:])


# ---------------------------------------------------------------- 静态检查闸门

def _iter_py(repo: Path):
    for path in repo.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def _syntax_errors(repo: Path) -> list[str]:
    bad: list[str] = []
    for path in _iter_py(repo):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (SyntaxError, UnicodeDecodeError) as exc:
            bad.append(f"{path.relative_to(repo)}: {exc}")
    return bad


def _ruff_count(repo: Path) -> tuple[int, str]:
    code, out, err = _run(["ruff", "check", "--output-format=json", "--no-cache", "."], repo, 120)
    if code == 127:
        return -1, err
    try:
        issues = json.loads(out or "[]")
    except json.JSONDecodeError:
        return -1, f"ruff 输出无法解析：{out[:200]}"
    return len(issues), ""


def lint_baseline(contract: Contract, repo: Path) -> dict:
    """在打补丁之前采集基线，用于计算"新增"问题数。"""
    if contract.lint.tool != "ruff":
        return {"tool": contract.lint.tool, "issues": 0, "fallback": True}
    if shutil.which("ruff") is None:
        return {"tool": "ruff", "issues": -1, "fallback": "syntax"}
    count, _ = _ruff_count(repo)
    return {"tool": "ruff", "issues": count, "fallback": None}


def gate_lint(contract: Contract, repo: Path, baseline: dict) -> GateResult:
    spec = contract.lint
    if spec.tool != "ruff":
        return GateResult("lint", False, f"不支持的静态检查工具：{spec.tool}")

    if shutil.which("ruff") is None:
        # 没装 ruff 时降级为语法检查：宁可判定能力变弱，也不能假装通过
        bad = _syntax_errors(repo)
        return GateResult(
            "lint",
            not bad,
            f"ruff 未安装，降级为语法检查（{len(bad)} 处错误）",
            {"fallback": "syntax", "errors": bad[:10], "count": len(bad)},
        )

    count, problem = _ruff_count(repo)
    if count < 0:
        return GateResult("lint", False, f"ruff 执行失败：{problem}", {"error": problem})

    base_count = baseline.get("issues", 0)
    if base_count < 0:
        base_count = 0
    new_issues = count - base_count
    ok = new_issues <= spec.max_new
    return GateResult(
        "lint",
        ok,
        f"问题总数 {count}（基线 {base_count}，新增 {new_issues}，上限 {spec.max_new}）",
        {"count": count, "baseline": base_count, "new": new_issues, "max_new": spec.max_new},
    )


# ---------------------------------------------------------------- 接口冻结闸门

def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def gate_api(contract: Contract, repo: Path) -> GateResult:
    specs = contract.api.frozen
    if not specs:
        return GateResult("api", True, "契约未冻结接口")

    missing: list[str] = []
    for spec in specs:
        module_path, _, symbol = spec.rpartition(".")
        if not module_path:
            missing.append(f"{spec}（格式应为 module.symbol）")
            continue
        py_file = repo / (module_path.replace(".", "/") + ".py")
        if not py_file.is_file():
            missing.append(f"{spec}（模块 {module_path} 不存在）")
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            missing.append(f"{spec}（模块存在语法错误：{exc}）")
            continue
        if symbol not in _top_level_names(tree):
            missing.append(f"{spec}（符号未被定义）")

    ok = not missing
    detail = f"冻结 {len(specs)} 个符号，全部存在" if ok else f"缺失/变更：{'；'.join(missing)}"
    return GateResult("api", ok, detail, {"frozen": specs, "missing": missing})


# ---------------------------------------------------------------- 改动预算闸门

def _changed_files(repo: Path, base_ref: str) -> list[tuple[str, int, int]]:
    code, out, _ = _run(["git", "diff", "--numstat", base_ref, "--", "."], repo, 60)
    changes: list[tuple[str, int, int]] = []
    if code == 0:
        for row in out.splitlines():
            parts = row.split("\t")
            if len(parts) < 3:
                continue
            added, deleted, path = parts[0], parts[1], parts[2]
            if added == "-" or deleted == "-":
                changes.append((path, 0, 0))
            else:
                changes.append((path, int(added), int(deleted)))
    code, out, _ = _run(["git", "ls-files", "--others", "--exclude-standard"], repo, 60)
    if code == 0:
        for row in out.splitlines():
            if row.strip():
                changes.append((row.strip(), 0, 0))
    return changes


def gate_diff(contract: Contract, repo: Path, base_ref: str = "HEAD") -> GateResult:
    spec = contract.diff
    changes = _changed_files(repo, base_ref)
    files = len(changes)
    lines = sum(added + deleted for _, added, deleted in changes)
    touched = [path for path, _, _ in changes]
    forbidden = [p for p in touched if any(rule in p for rule in spec.forbid)]

    reasons: list[str] = []
    if files > spec.max_files:
        reasons.append(f"改动 {files} 个文件，超过上限 {spec.max_files}")
    if lines > spec.max_lines:
        reasons.append(f"改动 {lines} 行，超过上限 {spec.max_lines}")
    if forbidden:
        reasons.append(f"触碰禁止修改的路径：{', '.join(forbidden)}")

    ok = not reasons
    if ok:
        detail = f"改动 {files} 个文件 / {lines} 行（上限 {spec.max_files} / {spec.max_lines}）"
    else:
        detail = "；".join(reasons)
    return GateResult(
        "diff",
        ok,
        detail,
        {"files": files, "lines": lines, "touched": touched, "forbidden": forbidden},
    )


# ---------------------------------------------------------------- 汇总

def run_all(contract: Contract, repo: Path, baseline: dict, base_ref: str = "HEAD") -> GateReport:
    results = [
        gate_tests(contract, repo),
        gate_lint(contract, repo, baseline),
        gate_api(contract, repo),
        gate_diff(contract, repo, base_ref),
    ]
    return GateReport(task_id=contract.task_id, results=results)
