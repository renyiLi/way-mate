"""单轮交付流程：模型产出补丁 → 写入工作区 → 四类闸门。

被 run_once.py（手工跑一次）和实验脚本（批量跑一组）共用，
保证"实验里跑的"和"手工跑的"是同一条代码路径。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import OpenAICompatAdapter
from .audit import AuditLog, git_sha
from .contract import Contract, load_contract
from .gates import GateReport, lint_baseline, run_all
from .protocol.base import Capabilities, Message, ModelRequest, extract_json
from .protocol.interfaces import AuthoringInterface, Patch


@dataclass
class DeliveryResult:
    provider: str
    model: str
    outcome: str = "error"
    reason: str = ""
    patch_files: tuple[str, ...] = ()
    rationale: str = ""
    tokens: int = 0
    latency_ms: int = 0
    degraded: bool = False
    gate: GateReport | None = None
    run_id: str = ""
    contract_hash: str = ""
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "reason": self.reason,
            "files": list(self.patch_files),
            "rationale": self.rationale,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "degraded": self.degraded,
            "run_id": self.run_id,
            "contract_hash": self.contract_hash,
            "error": self.error,
            "gate": self.gate.to_dict() if self.gate else None,
            "extra": self.extra,
        }


def force_rmtree(path: Path) -> None:
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=on_error)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def prepare_workspace(fixture: Path, work: Path) -> Path:
    """复制示例仓库到工作区，并建立基线提交。"""
    force_rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture, work, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))
    identity = ["-c", "user.name=dbw", "-c", "user.email=dbw@local"]
    _git(work, "init", "-q")
    _git(work, *identity, "add", "-A")
    _git(work, *identity, "commit", "-q", "-m", "base")
    return work


def capture_failure(repo: Path, cmd: str, limit: int = 25) -> str:
    proc = subprocess.run(cmd.split(), cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = [row for row in f"{proc.stdout}\n{proc.stderr}".splitlines() if row.strip()]
    return "\n".join(rows[-limit:])


def apply_patch(repo: Path, patch: Patch) -> None:
    for relative, content in patch.files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def reset_workspace(repo: Path) -> None:
    """把工作区恢复到基线提交，丢掉上一轮的补丁与新增文件。"""
    _git(repo, "checkout", "--", ".")
    _git(repo, "clean", "-fdq")


def strip_exact_section(contract_path: Path) -> None:
    """实验用：把 exact 段从**工作区副本**里摘掉，其余完全不变。

    这样 A/B 两组走的是同一条代码路径，唯一的变量只有"契约里有没有硬约束"。
    原始 fixtures 里的契约不受影响。
    """
    import yaml

    data = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    dod = data.get("doD") or {}
    dod.pop("exact", None)
    data["doD"] = dod
    contract_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def capabilities_path(runs_dir: Path, provider: str, model: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model)
    return runs_dir / f"capabilities.{provider}.{safe}.json"


def load_capabilities(runs_dir: Path, provider: str, model: str) -> Capabilities:
    path = capabilities_path(runs_dir, provider, model)
    if not path.is_file():
        raise FileNotFoundError(
            f"缺少能力表 {path.name}，请先执行 "
            f"python scripts/test_connection.py --provider {provider} --models {model}"
        )
    return Capabilities(**json.loads(path.read_text(encoding="utf-8"))["capabilities"])


def build_task(repo: Path, contract: Contract, issue: str, target_file: str) -> dict[str, Any]:
    layout = sorted(
        str(path.relative_to(repo)).replace("\\", "/")
        for path in repo.rglob("*.py")
        if not any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts)
    )
    task: dict[str, Any] = {
        "task_id": contract.task_id,
        "issue": issue,
        "repo_layout": layout,
    }
    target = repo / target_file
    if target_file and target.is_file():
        # 只有确实存在"目标文件"时才给，否则模型会被误导去改一个不存在的路径
        task["target_file"] = target_file
        task["current_file"] = target.read_text(encoding="utf-8")
    task["failing_test_output"] = capture_failure(repo, contract.tests[0].cmd)
    return task


def deliver_once(
    *,
    contract_path: Path,
    work_dir: Path,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    capabilities: Capabilities,
    audit: AuditLog | None = None,
    issue: str = "parse_kv 未剥离行内注释：'a=1  # note' 被解析成 '1  # note'；缺 '=' 时异常消息不对",
    target_file: str = "pkg/parser.py",
    strip_exact: bool = False,
    prepare: bool = True,
    temperature: float | None = None,
    timeout_s: float = 180.0,
    max_tokens: int = 8192,
) -> DeliveryResult:
    """跑一次完整交付。异常不外抛，全部转成 outcome=error 的结果，便于批量实验。

    `prepare=False` 表示工作区已经就绪（例如由 `prepare_task.py` 准备好的真实仓库），
    此时不再从 fixture 复制，直接使用传入目录。
    """
    contract_path = Path(contract_path)
    fixture = contract_path.parent
    result = DeliveryResult(provider=provider, model=model)

    if prepare:
        prepare_workspace(fixture, work_dir)
    if strip_exact:
        strip_exact_section(work_dir / "contract.yaml")
    contract = load_contract(work_dir / "contract.yaml")
    result.contract_hash = json.dumps(contract.to_dict(), sort_keys=True)[:16]
    baseline = lint_baseline(contract, work_dir)

    own_audit = audit is None
    log = audit or AuditLog(Path("runs") / "audit.jsonl")
    result.run_id = log.run_id

    log.emit(
        "run_start",
        task_id=contract.task_id,
        git_sha=git_sha(work_dir),
        interface="authoring",
        protocol="single-shot",
        provider=provider,
        model=model,
        exact_count=len(contract.exact),
        capabilities=capabilities.to_dict(),
    )

    interface = AuthoringInterface()
    task = build_task(work_dir, contract, issue, target_file)
    adapter = OpenAICompatAdapter(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model=model,
        capabilities=capabilities,
        timeout_s=timeout_s,
        max_retries=2,
    )
    request_temperature = temperature if temperature is not None else (
        capabilities.fixed_temperature if capabilities.fixed_temperature is not None else 0.2
    )

    system_prompt = interface.system_prompt(contract)
    user_prompt = json.dumps(task, ensure_ascii=False, indent=2)
    schema = interface.propose_schema()

    response = None
    patch = None
    parse_error: Exception | None = None

    # 最多两次：第一次输出不合法时，带着错误信息重问一次。
    # 与协议机的行为保持一致；不这样做，推理模型的空输出会被误判成"提案不合法"。
    for attempt in (1, 2):
        try:
            response = adapter.complete(
                ModelRequest(
                    messages=(Message("system", system_prompt), Message("user", user_prompt)),
                    response_schema=schema,
                    max_tokens=max_tokens,
                    temperature=request_temperature,
                    timeout_s=timeout_s,
                    idempotency_key=f"{log.run_id}:proposer:propose:{attempt}",
                )
            )
        except Exception as exc:  # noqa: BLE001 批量实验要如实记录失败
            result.error = f"{type(exc).__name__}: {exc}"
            log.emit("run_end", outcome="error", reason=result.error)
            if own_audit:
                log.close()
            return result

        result.tokens += response.usage.total
        result.latency_ms += response.latency_ms
        result.degraded = result.degraded or response.degraded
        log.emit(
            "model_response",
            seat="proposer",
            attempt=attempt,
            provider=provider,
            model=response.model,
            usage=response.usage.to_dict(),
            finish_reason=response.finish_reason,
            latency_ms=response.latency_ms,
            degraded=response.degraded,
            degraded_reason=response.degraded_reason,
            request_digest=response.request_digest,
            raw_digest=response.raw_digest,
            raw_text=response.text,
        )

        try:
            patch = interface.parse_proposal(extract_json(response.text), contract)
            break
        except Exception as exc:  # noqa: BLE001
            parse_error = exc
            # 推理模型可能把输出预算全花在思考上，正文为空。
            # 这必须报出来，否则会被误判成"提案不合法"。
            empty = not response.text.strip()
            hint = "（你的上一条回复没有正文，可能是推理过长）" if empty else ""
            user_prompt = (
                json.dumps(task, ensure_ascii=False, indent=2)
                + f"\n\n注意：你上一次的输出不合法{hint}（{exc}）。请直接输出符合要求的 JSON，不要任何其他文字。"
            )

    if patch is None:
        result.outcome = "rejected"
        result.reason = f"提案不合法：{type(parse_error).__name__}: {parse_error}"
        log.emit("run_end", outcome="rejected", reason=result.reason)
        if own_audit:
            log.close()
        return result

    result.patch_files = tuple(patch.files)
    result.rationale = patch.rationale
    apply_patch(work_dir, patch)

    report = run_all(contract, work_dir, baseline, base_ref="HEAD")
    result.gate = report
    result.outcome = "accepted" if report.all_green else "rejected"
    result.reason = "闸门全绿" if report.all_green else "；".join(f"{r.name}: {r.detail}" for r in report.failures)

    log.emit("gate_report", attempt=1, report=report.to_dict(), signature=[(r.name, r.ok) for r in report.results])
    log.emit("run_end", outcome=result.outcome, rounds=1, budget={"tokens": response.usage.total})
    if own_audit:
        log.close()
    return result
