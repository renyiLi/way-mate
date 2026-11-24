"""对照实验：契约里有没有"精确断言"，影响通过率吗？

同一道题、同一份契约、同一套代码路径，唯一变量是工作区契约副本里有没有 exact 段。

  A 组（无 exact）：把 exact 从契约副本摘掉
  B 组（有 exact）：原样

每个模型每组跑 reps 次（默认 1）。模型本身有随机性，reps=1 只能当线索，不能当结论。

用法：
  python scripts/experiment_exact.py
  python scripts/experiment_exact.py --reps 3
  python scripts/experiment_exact.py --only ark/deepseek-v4-1-flash-261017 --reps 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.config import PROFILES, Settings  # noqa: E402
from dbw.delivery import DeliveryResult, deliver_once, load_capabilities  # noqa: E402

MODELS: list[tuple[str, str]] = [
    ("deepseek", "deepseek-chat"),
    ("ark", "deepseek-v4-1-flash-261017"),
    ("ark", "doubao-seed-2-1-pro-260915"),
    ("ark", "kimi-k3-1-pro-261024"),
]

FRESH: list[tuple[str, str]] = [
    ("deepseek", "deepseek-chat"),
    ("ark", "deepseek-v4-1-flash-261017"),
    ("ark", "kimi-k3-1-pro-261024"),
]


def run_condition(
    models: list[tuple[str, str]], settings: Settings, strip_exact: bool, reps: int
) -> dict[tuple[str, str], list[DeliveryResult]]:
    out: dict[tuple[str, str], list[DeliveryResult]] = {}
    for provider, model in models:
        profile = PROFILES[provider]
        capabilities = load_capabilities(ROOT / "runs", provider, model)
        runs: list[DeliveryResult] = []
        for rep in range(reps):
            started = time.perf_counter()
            result = deliver_once(
                contract_path=ROOT / "fixtures" / "repo_a" / "contract.yaml",
                work_dir=ROOT / ".work" / "repo_a",
                provider=provider,
                model=model,
                api_key=settings.require(profile.key_env),
                base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
                capabilities=capabilities,
                strip_exact=strip_exact,
            )
            elapsed = time.perf_counter() - started
            runs.append(result)
            mark = "✓" if result.outcome == "accepted" else "✗"
            note = "" if result.outcome == "accepted" else f"  {result.reason[:52]}"
            print(
                f"  {provider}/{model:<30} 第{rep + 1}次 {mark}  {elapsed:6.1f}s  token {result.tokens}{note}",
                flush=True,
            )
        out[(provider, model)] = runs
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="精确断言对照实验")
    parser.add_argument("--only", default="", help="只跑一个 provider/model")
    parser.add_argument("--reps", type=int, default=1, help="每个模型每组跑几次")
    parser.add_argument("--fresh", action="store_true", help="只跑耗时较短的三个模型（跳过 doubao）")
    args = parser.parse_args()

    models = FRESH if args.fresh else MODELS
    if args.only:
        provider, _, model = args.only.partition("/")
        models = [(provider, model)]
    reps = max(1, args.reps)

    settings = Settings()
    print("═" * 88)
    print("对照实验：契约里的精确断言是否影响通过率")
    print("═" * 88)
    print("  契约   fixtures/repo_a/contract.yaml（含 exact 1 条）")
    print(f"  规模   {len(models)} 个模型 × 2 组 × {reps} 次 = {len(models) * 2 * reps} 次运行\n")

    print("─" * 88)
    print("A 组：无 exact（对照组）")
    print("─" * 88)
    off = run_condition(models, settings, strip_exact=True, reps=reps)

    print(f"\n{'─' * 88}")
    print("B 组：有 exact（实验组）")
    print("─" * 88)
    on = run_condition(models, settings, strip_exact=False, reps=reps)

    print(f"\n{'═' * 88}")
    print("对照结果")
    print("═" * 88)
    print(f"{'模型':<46}{'无 exact':<14}{'有 exact':<14}{'变化'}")
    print("-" * 88)
    total_off = total_on = 0
    for key in off:
        provider, model = key
        before = sum(1 for r in off[key] if r.outcome == "accepted")
        after = sum(1 for r in on[key] if r.outcome == "accepted")
        total_off += before
        total_on += after
        change = "—" if before == after else ("↑" if after > before else "↓")
        print(f"{provider}/{model:<44}{f'{before}/{reps}':<14}{f'{after}/{reps}':<14}{change}")
    n = len(off) * reps
    print(f"\n  总通过率  无 exact {total_off}/{n}  →  有 exact {total_on}/{n}")
    if reps == 1:
        print("  ⚠ reps=1：模型有随机性，这只能当线索，不能当结论。用 --reps 3 复测。")

    out_path = ROOT / "runs" / f"experiment-exact-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "reps": reps,
                "condition_off": {f"{p}/{m}": [r.to_dict() for r in rs] for (p, m), rs in off.items()},
                "condition_on": {f"{p}/{m}": [r.to_dict() for r in rs] for (p, m), rs in on.items()},
                "summary": {"pass_off": total_off, "pass_on": total_on, "total": n},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  明细   {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
