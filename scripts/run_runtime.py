"""用执行后端（后端 B）跑一条任务：模型在仓库里迭代，最后交出补丁。

与 run_once.py（后端 A：一次生成）对照，回答"多轮探索能不能救回单轮失败的任务"。

用法：
  python scripts/run_runtime.py --provider deepseek --model deepseek-chat --budget 8
"""

from __future__ import annotations

import argparse
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
    load_capabilities,
    prepare_workspace,
    reset_workspace,
    strip_exact_section,
)
from dbw.gates import lint_baseline, run_all  # noqa: E402
from dbw.protocol.envelope import Budget  # noqa: E402
from dbw.protocol.interfaces import Patch  # noqa: E402
from dbw.runtime import MiniSweRuntime  # noqa: E402

FIXTURE = ROOT / "fixtures" / "repo_a"
WORK = ROOT / ".work" / "repo_a"

TASK = """修复 pkg/parser.py 的 parse_kv：
1. 行内注释没有被剥离：'a=1  # note' 目前被解析成 '1  # note'。
2. 缺少 '=' 的行抛出的异常消息不符合验收要求。
先用 pytest 复现失败，再改代码，最后跑 pytest 确认全部通过。"""


def main() -> int:
    parser = argparse.ArgumentParser(description="执行后端跑一条任务")
    parser.add_argument("--provider", default="deepseek", choices=sorted(PROFILES))
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--budget", type=int, default=8, help="最多几步")
    parser.add_argument("--strip-exact", action="store_true")
    args = parser.parse_args()

    profile = PROFILES[args.provider]
    settings = Settings()
    capabilities = load_capabilities(ROOT / "runs", profile.name, args.model)

    prepare_workspace(FIXTURE, WORK)
    if args.strip_exact:
        strip_exact_section(WORK / "contract.yaml")
    contract = load_contract(WORK / "contract.yaml")
    baseline = lint_baseline(contract, WORK)

    audit = AuditLog(ROOT / "runs" / "audit.jsonl")
    adapter = OpenAICompatAdapter(
        provider=profile.name,
        base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
        api_key=settings.require(profile.key_env),
        model=args.model,
        capabilities=capabilities,
        timeout_s=120.0,
        max_retries=2,
    )
    runtime = MiniSweRuntime(model=adapter, timeout_s=60.0, wall_time_s=600.0)

    print("═" * 72)
    print("执行后端（后端 B）：模型在仓库里自己迭代")
    print("═" * 72)
    print(f"  模型   {profile.name} / {args.model}")
    print(f"  预算   {args.budget} 步")
    print(f"  契约   exact {'已摘掉' if args.strip_exact else f'{len(contract.exact)} 条生效'}")
    print(f"  基线   {git_sha(WORK)}\n")

    result = runtime.run_contract(workspace=WORK, task=TASK, contract=contract, budget=Budget(max_calls=args.budget))

    print("── 轨迹")
    for step in result.transcript:
        if "error" in step:
            print(f"  {step['step']:>2}. 错误 {step['error'][:80]}")
        else:
            mark = "⏱" if step["timed_out"] else ("✓" if step["returncode"] == 0 else "✗")
            print(f"  {step['step']:>2}. {mark} {step['command'][:64]}")

    print(f"\n  终止   {result.stop_reason}")
    print(f"  自称完成 {result.claim_done}  ← 仅参考，闸门照跑")
    print(f"  调用   {result.calls} 次，token {result.tokens}")
    print(f"  改动   {len(result.patch)} 个文件：{', '.join(result.patch) or '（无）'}")

    audit.emit(
        "runtime_run",
        backend="miniswe",
        model=args.model,
        stop_reason=result.stop_reason,
        claim_done=result.claim_done,
        calls=result.calls,
        tokens=result.tokens,
        files=list(result.patch),
        transcript=result.transcript,
    )

    if not result.patch:
        print("\n  没有产出补丁，直接判失败（闸门无需运行）")
        audit.close()
        return 1

    reset_workspace(WORK)
    apply_patch(WORK, Patch(files=dict(result.patch), rationale=f"runtime/{result.stop_reason}"))
    report = run_all(contract, WORK, baseline, base_ref="HEAD")

    print("\n" + "─" * 72)
    print("闸门判定")
    print("─" * 72)
    print(report.summary())

    outcome = "accepted" if report.all_green else "rejected"
    audit.emit("gate_report", report=report.to_dict(), backend="miniswe")
    audit.emit("run_end", outcome=outcome, backend="miniswe", calls=result.calls, tokens=result.tokens)
    audit.close()

    print(f"\n  结论   {'交付成立' if report.all_green else '未通过'}")
    return 0 if report.all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
