"""一次真实交付：模型产出补丁 → 写进工作区 → 四类闸门判定。

不接编排、不做多轮修复——先看单轮能不能过，过不了才是需要修复循环的证据。

用法：
  python scripts/run_once.py
  python scripts/run_once.py --provider ark --model deepseek-v4-1-flash-261017
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.adapters import OpenAICompatAdapter  # noqa: E402
from dbw.audit import AuditLog, git_sha  # noqa: E402
from dbw.config import PROFILES, Settings  # noqa: E402
from dbw.contract import load_contract  # noqa: E402
from dbw.gates import lint_baseline, run_all  # noqa: E402
from dbw.protocol.base import Capabilities, Message, ModelRequest, extract_json  # noqa: E402
from dbw.protocol.interfaces import AuthoringInterface  # noqa: E402

FIXTURE = ROOT / "fixtures" / "repo_a"
WORK = ROOT / ".work" / "repo_a"
RUNS = ROOT / "runs"


def force_rmtree(path: Path) -> None:
    import os
    import stat

    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=on_error)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc.stdout


def prepare() -> None:
    force_rmtree(WORK)
    shutil.copytree(FIXTURE, WORK, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))
    identity = ["-c", "user.name=dbw", "-c", "user.email=dbw@local"]
    git(WORK, "init", "-q")
    git(WORK, *identity, "add", "-A")
    git(WORK, *identity, "commit", "-q", "-m", "base")


def failing_output(repo: Path, cmd: str) -> str:
    proc = subprocess.run(cmd.split(), cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = [r for r in f"{proc.stdout}\n{proc.stderr}".splitlines() if r.strip()]
    return "\n".join(rows[-25:])


def load_capabilities(provider: str, model: str) -> Capabilities:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model)
    path = RUNS / f"capabilities.{provider}.{safe}.json"
    if not path.is_file():
        raise SystemExit(
            f"缺少能力表 {path.name}。\n请先执行：python scripts/test_connection.py --provider {provider} --models {model}"
        )
    return Capabilities(**json.loads(path.read_text(encoding="utf-8"))["capabilities"])


def main() -> int:
    parser = argparse.ArgumentParser(description="单轮真实交付")
    parser.add_argument("--provider", default="deepseek", choices=sorted(PROFILES))
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    profile = PROFILES[args.provider]
    settings = Settings()
    key = settings.require(profile.key_env)
    base_url = settings.get(profile.base_env, profile.default_base) or profile.default_base
    model = args.model or settings.get(profile.model_env) or profile.default_models[0]
    capabilities = load_capabilities(profile.name, model)

    prepare()
    contract = load_contract(WORK / "contract.yaml")
    baseline = lint_baseline(contract, WORK)
    failure = failing_output(WORK, contract.tests[0].cmd)
    audit = AuditLog(RUNS / "audit.jsonl")

    audit.emit(
        "run_start",
        task_id=contract.task_id,
        git_sha=git_sha(WORK),
        interface="authoring",
        protocol="single-shot",
        provider=profile.name,
        model=model,
        lint_baseline=baseline,
        capabilities=capabilities.to_dict(),
    )

    print("═" * 70)
    print("单轮真实交付：模型 → 补丁 → 闸门")
    print("═" * 70)
    print(f"  任务   {contract.task_id}")
    print(f"  基线   {git_sha(WORK)}")
    print(f"  模型   {profile.name} / {model}")
    print(f"  能力   json_schema={'✓' if capabilities.supports_json_schema else '✗'}"
          f"  temperature={'固定 ' + str(capabilities.fixed_temperature) if capabilities.fixed_temperature else '可调'}")

    interface = AuthoringInterface()
    task = {
        "task_id": contract.task_id,
        "issue": "parse_kv 未剥离行内注释：'a=1  # note' 被解析成 '1  # note'；缺 '=' 时异常消息不对",
        "repo_layout": ["pkg/parser.py", "tests/test_parser.py", "contract.yaml"],
        "target_file": "pkg/parser.py",
        "current_file": (WORK / "pkg" / "parser.py").read_text(encoding="utf-8"),
        "failing_test_output": failure,
    }

    adapter = OpenAICompatAdapter(
        provider=profile.name,
        base_url=base_url,
        api_key=key,
        model=model,
        capabilities=capabilities,
        timeout_s=180.0,
        max_retries=2,
    )
    response = adapter.complete(
        ModelRequest(
            messages=(
                Message("system", interface.system_prompt(contract)),
                Message("user", json.dumps(task, ensure_ascii=False, indent=2)),
            ),
            response_schema=interface.propose_schema(),
            max_tokens=3072,
            temperature=capabilities.fixed_temperature if capabilities.fixed_temperature is not None else 0.2,
            timeout_s=180.0,
            idempotency_key=f"{audit.run_id}:proposer:propose:1",
        )
    )
    audit.emit(
        "model_response",
        seat="proposer",
        provider=profile.name,
        model=response.model,
        usage=response.usage.to_dict(),
        latency_ms=response.latency_ms,
        degraded=response.degraded,
        degraded_reason=response.degraded_reason,
        request_digest=response.request_digest,
        raw_digest=response.raw_digest,
        raw_text=response.text,
    )
    print(f"  调用   {response.latency_ms} ms，token {response.usage.total}"
          + (f"，降级：{response.degraded_reason}" if response.degraded else ""))

    patch = interface.parse_proposal(extract_json(response.text), contract)
    print(f"  提案   {len(patch.files)} 个文件：{', '.join(patch.files)}")
    print(f"  理由   {patch.rationale[:100]}")

    for relative, content in patch.files.items():
        target = WORK / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    report = run_all(contract, WORK, baseline, base_ref="HEAD")
    audit.emit("gate_report", attempt=1, report=report.to_dict(), signature=[(r.name, r.ok) for r in report.results])

    print("\n" + "─" * 70)
    print("闸门判定")
    print("─" * 70)
    print(report.summary())

    outcome = "accepted" if report.all_green else "rejected"
    audit.emit("run_end", outcome=outcome, rounds=1, budget={"tokens": response.usage.total}, events=audit.count)
    audit.close()

    print(f"\n  结论   {'交付成立' if report.all_green else '未通过，需要修复循环'}")
    print(f"  审计   {(RUNS / 'audit.jsonl').relative_to(ROOT)}（{audit.count} 条）")
    return 0 if report.all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
