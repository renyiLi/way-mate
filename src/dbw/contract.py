"""可执行验收契约（Definition of Done）的解析与校验。

契约是本系统唯一的裁判来源：模型说什么不算，闸门跑出什么才算。
字段一律严格校验，拼错字段名会立刻报错而不是被静默忽略。
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .exact import ExactError, ExactSpec


class ContractError(ValueError):
    """契约文件不合法。错误信息里带字段路径，便于直接定位。"""


@dataclass(frozen=True)
class TestSpec:
    cmd: str
    timeout_s: int = 120

    @property
    def argv(self) -> list[str]:
        tokens = self.cmd.split()
        if not tokens:
            raise ContractError("doD.tests[].cmd 不能为空")
        # 统一用当前解释器跑 pytest，避免 PATH 里装的是另一个环境
        if tokens[0] in {"pytest", "py.test"}:
            return [sys.executable, "-m", "pytest", *tokens[1:]]
        return tokens


@dataclass(frozen=True)
class LintSpec:
    tool: str = "ruff"
    max_new: int = 0


@dataclass(frozen=True)
class ApiSpec:
    frozen: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiffSpec:
    max_files: int = 5
    max_lines: int = 200
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoopSpec:
    repair_rounds: int = 3
    stop_on: str = "all_green"
    on_exhausted: str = "escalate_to_human"


@dataclass(frozen=True)
class Contract:
    task_id: str
    repo: Path
    tests: tuple[TestSpec, ...]
    lint: LintSpec
    api: ApiSpec
    diff: DiffSpec
    loop: LoopSpec
    source: Path
    exact: tuple[ExactSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repo": str(self.repo),
            "doD": {
                "tests": [{"cmd": t.cmd, "timeout_s": t.timeout_s} for t in self.tests],
                "lint": {"tool": self.lint.tool, "max_new": self.lint.max_new},
                "api": {"frozen": list(self.api.frozen)},
                "diff": {
                    "max_files": self.diff.max_files,
                    "max_lines": self.diff.max_lines,
                    "forbid": list(self.diff.forbid),
                },
                "exact": [spec.to_dict() for spec in self.exact],
            },
            "loop": {
                "repair_rounds": self.loop.repair_rounds,
                "stop_on": self.loop.stop_on,
                "on_exhausted": self.loop.on_exhausted,
            },
        }


# ---------------------------------------------------------------- 校验工具

_TOP_KEYS = {"task_id", "repo", "doD", "loop"}
_DOD_KEYS = {"tests", "lint", "api", "diff", "exact"}
_LINT_KEYS = {"tool", "max_new"}
_API_KEYS = {"frozen"}
_DIFF_KEYS = {"max_files", "max_lines", "forbid"}
_LOOP_KEYS = {"repair_rounds", "stop_on", "on_exhausted"}
_TEST_KEYS = {"cmd", "timeout_s"}


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{where} 必须是映射（键值对），实际是 {type(value).__name__}")
    return value


def _strict_keys(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ContractError(f"{where} 存在未知字段：{', '.join(unknown)}（可用字段：{', '.join(sorted(allowed))}）")


def _req_str(data: dict[str, Any], key: str, where: str) -> str:
    if key not in data:
        raise ContractError(f"{where} 缺少必填字段 {key}")
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{where}.{key} 必须是非空字符串")
    return value.strip()


def _opt_int(data: dict[str, Any], key: str, where: str, default: int, minimum: int = 0) -> int:
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{where}.{key} 必须是整数")
    if value < minimum:
        raise ContractError(f"{where}.{key} 不能小于 {minimum}")
    return value


def _opt_str_list(data: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    if key not in data:
        return ()
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ContractError(f"{where}.{key} 必须是非空字符串列表")
    return tuple(v.strip() for v in value)


# ---------------------------------------------------------------- 入口

def load_contract(path: str | Path) -> Contract:
    """读取并校验契约文件。所有相对路径都以契约文件所在目录为基准。"""
    source = Path(path).resolve()
    if not source.is_file():
        raise ContractError(f"契约文件不存在：{source}")

    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractError(f"YAML 语法错误：{exc}") from exc

    data = _mapping(raw, "契约根节点")
    _strict_keys(data, _TOP_KEYS, "契约根节点")

    task_id = _req_str(data, "task_id", "契约根节点")
    base_dir = source.parent
    repo = Path(_req_str(data, "repo", "契约根节点"))
    if not repo.is_absolute():
        repo = (base_dir / repo).resolve()

    dod = _mapping(data.get("doD"), "doD")
    _strict_keys(dod, _DOD_KEYS, "doD")

    raw_tests = dod.get("tests")
    if not isinstance(raw_tests, list) or not raw_tests:
        raise ContractError("doD.tests 必须是非空列表，至少指定一条验收命令")
    tests: list[TestSpec] = []
    for index, item in enumerate(raw_tests):
        where = f"doD.tests[{index}]"
        if isinstance(item, str):
            tests.append(TestSpec(cmd=_req_str({"cmd": item}, "cmd", where)))
            continue
        entry = _mapping(item, where)
        _strict_keys(entry, _TEST_KEYS, where)
        tests.append(
            TestSpec(
                cmd=_req_str(entry, "cmd", where),
                timeout_s=_opt_int(entry, "timeout_s", where, 120, minimum=1),
            )
        )

    lint_raw = _mapping(dod.get("lint", {}), "doD.lint")
    _strict_keys(lint_raw, _LINT_KEYS, "doD.lint")
    lint = LintSpec(
        tool=(lint_raw.get("tool") or "ruff") if isinstance(lint_raw.get("tool", "ruff"), str) else "ruff",
        max_new=_opt_int(lint_raw, "max_new", "doD.lint", 0),
    )

    api_raw = _mapping(dod.get("api", {}), "doD.api")
    _strict_keys(api_raw, _API_KEYS, "doD.api")
    api = ApiSpec(frozen=_opt_str_list(api_raw, "frozen", "doD.api"))

    diff_raw = _mapping(dod.get("diff", {}), "doD.diff")
    _strict_keys(diff_raw, _DIFF_KEYS, "doD.diff")
    diff = DiffSpec(
        max_files=_opt_int(diff_raw, "max_files", "doD.diff", 5, minimum=1),
        max_lines=_opt_int(diff_raw, "max_lines", "doD.diff", 200, minimum=1),
        forbid=_opt_str_list(diff_raw, "forbid", "doD.diff"),
    )

    exact_raw = dod.get("exact") or []
    if not isinstance(exact_raw, list):
        raise ContractError("doD.exact 必须是列表")
    exact_specs: list[ExactSpec] = []
    for index, item in enumerate(exact_raw):
        where = f"doD.exact[{index}]"
        entry = _mapping(item, where)
        _strict_keys(entry, {"exception", "contains", "source", "origin"}, where)
        try:
            exact_specs.append(ExactSpec.from_dict(entry))
        except ExactError as exc:
            raise ContractError(f"{where}: {exc}") from exc

    loop_raw = _mapping(data.get("loop", {}), "loop")
    _strict_keys(loop_raw, _LOOP_KEYS, "loop")
    loop = LoopSpec(
        repair_rounds=_opt_int(loop_raw, "repair_rounds", "loop", 3, minimum=0),
        stop_on=str(loop_raw.get("stop_on", "all_green")),
        on_exhausted=str(loop_raw.get("on_exhausted", "escalate_to_human")),
    )

    return Contract(
        task_id=task_id,
        repo=repo,
        tests=tuple(tests),
        lint=lint,
        api=api,
        diff=diff,
        loop=loop,
        source=source,
        exact=tuple(exact_specs),
    )


def contract_hash(contract: Contract) -> str:
    """契约的稳定指纹：对标准化后的字段算 sha256，取前 16 位。

    单轮与协议两条路径都用它，保证"同一份契约"在两处得到同一个值；
    审计把它记进 run_start，论文与附件据此说明"这次运行用的是哪版契约"。
    """
    raw = json.dumps(contract.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
