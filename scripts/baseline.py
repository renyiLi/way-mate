"""单轮基线：对任务集重复跑 N 次，统计通过次数与成本。

用途：给任务分难度档（3/3 易、1-2/3 中、0/3 难），并作为 2 人 / 3 人协议的对照基线。

用法：
  python scripts/baseline.py --reps 3
  python scripts/baseline.py --reps 3 --tasks tabulate-366,tabulate-362
  python scripts/baseline.py --reps 1 --backend runtime --budget 12
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.audit import AuditLog  # noqa: E402
from dbw.config import PROFILES, Settings  # noqa: E402
from dbw.delivery import DeliveryResult, deliver_once, load_capabilities  # noqa: E402
from dbw.protocol.envelope import Budget  # noqa: E402


def list_tasks() -> list[str]:
    return sorted(p.parent.name for p in (ROOT / "tasks").glob("*/task.json"))


def prepare(task_id: str, python: str, mirror: str = "") -> dict:
    cmd = [python, str(ROOT / "scripts" / "prepare_task.py"), "--task", f"tasks/{task_id}", "--no-verify"]
    if mirror:
        cmd.extend(["--mirror", mirror])
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"准备任务 {task_id} 失败：{proc.stdout[-300:]} {proc.stderr[-300:]}")
    return json.loads((ROOT / "tasks" / task_id / "task.json").read_text(encoding="utf-8"))


def run_direct(recipe: dict, provider: str, model: str, settings: Settings, capabilities) -> DeliveryResult:
    profile = PROFILES[provider]
    targets = recipe.get("target_files") or [""]
    return deliver_once(
        contract_path=ROOT / ".work" / "tasks" / recipe["id"] / "contract.yaml",
        work_dir=ROOT / ".work" / "tasks" / recipe["id"],
        provider=provider,
        model=model,
        api_key=settings.require(profile.key_env),
        base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
        capabilities=capabilities,
        issue=recipe["statement"],
        target_file=targets[0],
        prepare=False,
    )


def run_runtime(recipe: dict, provider: str, model: str, settings: Settings, capabilities, budget_steps: int) -> dict:
    from dbw.adapters import OpenAICompatAdapter
    from dbw.contract import load_contract
    from dbw.delivery import apply_patch, reset_workspace
    from dbw.gates import lint_baseline, run_all
    from dbw.protocol.interfaces import Patch
    from dbw.runtime import MiniSweRuntime

    profile = PROFILES[provider]
    work = ROOT / ".work" / "tasks" / recipe["id"]
    reset_workspace(work)
    contract = load_contract(work / "contract.yaml")
    baseline = lint_baseline(contract, work)

    adapter = OpenAICompatAdapter(
        provider=provider,
        base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
        api_key=settings.require(profile.key_env),
        model=model,
        capabilities=capabilities,
        timeout_s=180.0,
        max_retries=2,
    )
    result = MiniSweRuntime(model=adapter, timeout_s=60.0).run_contract(
        workspace=work,
        task=recipe["statement"],
        contract=contract,
        budget=Budget(max_calls=budget_steps),
    )
    if not result.patch:
        return {"outcome": "rejected", "reason": "无改动", "tokens": result.tokens, "calls": result.calls}
    reset_workspace(work)
    apply_patch(work, Patch(files=dict(result.patch), rationale="baseline/runtime"))
    report = run_all(contract, work, baseline, base_ref="HEAD")
    return {
        "outcome": "accepted" if report.all_green else "rejected",
        "reason": "；".join(r.name for r in report.failures),
        "tokens": result.tokens,
        "calls": result.calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="单轮基线")
    parser.add_argument("--tasks", default="", help="逗号分隔；留空跑全部")
    parser.add_argument("--provider", default="deepseek", choices=sorted(PROFILES))
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--backend", default="direct", choices=["direct", "runtime"])
    parser.add_argument("--budget", type=int, default=12, help="runtime 的步数上限")
    parser.add_argument("--mirror", default="", help="本地克隆目录；网络不可用时用它准备任务")
    args = parser.parse_args()

    settings = Settings()
    capabilities = load_capabilities(ROOT / "runs", args.provider, args.model)
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()] or list_tasks()
    reps = max(1, args.reps)

    print("═" * 78)
    print(f"单轮基线 · {args.backend} · {args.provider}/{args.model} · 每格 {reps} 次")
    print("═" * 78)
    print(f"{'任务':<18}{'通过':<8}{'平均 token':<14}{'平均耗时':<12}失败闸门")
    print("-" * 78)

    rows = []
    started = time.perf_counter()
    for task_id in tasks:
        recipe = prepare(task_id, sys.executable, args.mirror)
        passed = 0
        tokens = 0
        ms = 0
        reasons: set[str] = set()
        for _ in range(reps):
            if args.backend == "direct":
                r = run_direct(recipe, args.provider, args.model, settings, capabilities)
                tokens += r.tokens
                ms += r.latency_ms
                ok = r.outcome == "accepted"
                if not ok:
                    reasons.add(r.reason[:24])
            else:
                r = run_runtime(recipe, args.provider, args.model, settings, capabilities, args.budget)
                tokens += r["tokens"]
                ok = r["outcome"] == "accepted"
                if not ok:
                    reasons.add(str(r.get("reason"))[:24])
            passed += 1 if ok else 0

        rows.append(
            {
                "task": task_id,
                "passed": passed,
                "reps": reps,
                "tokens_avg": round(tokens / reps),
                "ms_avg": round(ms / reps) if args.backend == "direct" else None,
                "reasons": sorted(reasons),
            }
        )
        mark = f"{passed}/{reps}"
        token_text = str(rows[-1]["tokens_avg"])
        ms_text = f"{rows[-1]['ms_avg']} ms" if rows[-1]["ms_avg"] else "—"
        print(f"{task_id:<18}{mark:<8}{token_text:<14}{ms_text:<12}{'，'.join(sorted(reasons)) or '—'}")

    elapsed = time.perf_counter() - started
    print("-" * 78)
    print(f"总通过 {sum(r['passed'] for r in rows)}/{len(rows) * reps}   用时 {elapsed:.0f}s")

    out = ROOT / "runs" / f"baseline-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "backend": args.backend,
                "model": args.model,
                "reps": reps,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"明细 {out.relative_to(ROOT)}")
    _ = AuditLog  # 保持导入稳定，后续接入审计
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
