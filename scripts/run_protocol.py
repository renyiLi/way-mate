"""用真实模型跑一次多轮协议：提案 → 复核 → 闸门 → 修复 → 再判。

这是"协作解决问题"的最小验证：单轮会失败的任务，多轮能不能救回来。

用法：
  python scripts/run_protocol.py --strip-exact --rounds 2
  python scripts/run_protocol.py --proposer ark/deepseek-v4-1-flash-261017 \
      --reviewer ark/kimi-k3-1-pro-261024 --arbiter deepseek/deepseek-chat --rounds 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.adapters import OpenAICompatAdapter  # noqa: E402
from dbw.audit import AuditLog, git_sha  # noqa: E402
from dbw.config import PROFILES, Settings  # noqa: E402
from dbw.contract import load_contract  # noqa: E402
from dbw.delivery import (  # noqa: E402
    apply_patch,
    build_task,
    load_capabilities,
    prepare_workspace,
    reset_workspace,
    strip_exact_section,
)
from dbw.gates import lint_baseline, run_all  # noqa: E402
from dbw.protocol.machine import SeatAssignment, execute  # noqa: E402
from dbw.protocol.base import ProviderRegistry  # noqa: E402

FIXTURE = ROOT / "fixtures" / "repo_a"
WORK = ROOT / ".work" / "repo_a"
RUNS = ROOT / "runs"


def build_adapter(spec: str, settings: Settings) -> tuple[str, OpenAICompatAdapter]:
    provider, _, model = spec.partition("/")
    if provider not in PROFILES:
        raise SystemExit(f"未知供应商：{provider}（可选：{', '.join(sorted(PROFILES))}）")
    profile = PROFILES[provider]
    model = model or profile.default_models[0]
    capabilities = load_capabilities(RUNS, provider, model)
    adapter = OpenAICompatAdapter(
        provider=provider,
        base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
        api_key=settings.require(profile.key_env),
        model=model,
        capabilities=capabilities,
        timeout_s=180.0,
        max_retries=2,
    )
    return f"{provider}/{model}", adapter


def main() -> int:
    parser = argparse.ArgumentParser(description="真实模型多轮协议")
    parser.add_argument("--proposer", default="deepseek/deepseek-chat")
    parser.add_argument("--reviewer", default="ark/kimi-k3-1-pro-261024")
    parser.add_argument("--arbiter", default="", help="留空即 2 人协议")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--strip-exact", action="store_true", help="摘掉 exact（制造确定性失败）")
    parser.add_argument("--task", default="", help="任务目录；用真实仓库时指定")
    parser.add_argument("--timeout", type=float, default=240.0, help="单次模型调用超时（秒）")
    parser.add_argument("--max-tokens", type=int, default=8192, help="单次模型输出预算")
    args = parser.parse_args()

    settings = Settings()
    if args.task:
        recipe = json.loads((ROOT / args.task / "task.json").read_text(encoding="utf-8"))
        work = ROOT / ".work" / "tasks" / recipe["id"]
        task_text = recipe["statement"]
        targets = recipe.get("target_files") or [""]
        target_file = targets[0]
        reset_workspace(work)
    else:
        work = WORK
        prepare_workspace(FIXTURE, work)
        if args.strip_exact:
            strip_exact_section(work / "contract.yaml")
        task_text = "parse_kv 未剥离行内注释；缺 '=' 时异常消息不对"
        target_file = "pkg/parser.py"

    contract = load_contract(work / "contract.yaml")
    baseline = lint_baseline(contract, work)
    audit = AuditLog(RUNS / "audit.jsonl")

    registry = ProviderRegistry()
    proposer_key, proposer = build_adapter(args.proposer, settings)
    registry.register(proposer_key, proposer)
    reviewer_key, reviewer = build_adapter(args.reviewer, settings)
    if reviewer_key != proposer_key:
        registry.register(reviewer_key, reviewer)
    arbiter_key = None
    if args.arbiter:
        arbiter_key, arbiter = build_adapter(args.arbiter, settings)
        if arbiter_key not in registry:
            registry.register(arbiter_key, arbiter)

    seats = SeatAssignment(proposer=proposer_key, reviewer=reviewer_key, arbiter=arbiter_key)

    def evaluate(patch, _outcome):
        reset_workspace(work)
        apply_patch(work, patch)
        return run_all(contract, work, baseline, base_ref="HEAD")

    print("═" * 78)
    print("真实模型多轮协议")
    print("═" * 78)
    print(f"  任务     {contract.task_id}")
    print(f"  协议     {seats.protocol().value}")
    print(f"  提案座位 {proposer_key}")
    print(f"  复核座位 {reviewer_key}")
    print(f"  裁决座位 {arbiter_key or '（无，2 人协议）'}")
    print(f"  契约     exact {'已摘掉' if args.strip_exact else f'{len(contract.exact)} 条生效'}")
    print(f"  基线     {git_sha(work)}")
    print(f"  轮次上限 {args.rounds}")

    run = execute(
        interface="authoring",
        task=build_task(work, contract, task_text, target_file),
        seats=seats,
        registry=registry,
        evaluate=evaluate,
        contract=contract,
        audit=audit,
        max_repair_rounds=args.rounds - 1,
        request_timeout_s=args.timeout,
        max_output_tokens=args.max_tokens,
    )
    audit.close()

    print("\n" + "─" * 78)
    print("每轮结果")
    print("─" * 78)
    for record in run.rounds:
        gate = record.gate or {}
        green = "全绿" if gate.get("all_green") else "未通过"
        failed = [item["name"] for item in gate.get("results", []) if not item["ok"]]
        print(
            f"  第 {record.index} 轮  提案 {record.proposal_count} 份 · "
            f"复核 {len(record.findings)} 条（丢弃 {len(record.dropped)}）· "
            f"阻断 {record.blockers_after_arbitration} · 闸门 {green}"
            + (f"（{', '.join(failed)}）" if failed else "")
        )
        for reason in record.dropped[:3]:
            print(f"      丢弃：{reason}")

    print("\n" + "═" * 78)
    print(f"结果   {run.outcome.value} —— {run.reason}")
    print(f"轮次   {len(run.rounds)}    token {run.budget.tokens}    调用 {run.budget.calls} 次")
    print(f"同模型兼角 {'是' if run.self_played else '否'}")
    print("═" * 78)
    if run.outcome.value != "accepted":
        last = run.rounds[-1].gate if run.rounds else None
        if last:
            print("最后一轮闸门：")
            for item in last["results"]:
                print(f"  [{'PASS' if item['ok'] else 'FAIL'}] {item['name']}: {item['detail'][:90]}")
    return 0 if run.outcome.value == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
