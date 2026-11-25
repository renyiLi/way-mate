"""一次真实交付：模型产出补丁 → 四类闸门判定。

用法：
  python scripts/run_once.py
  python scripts/run_once.py --provider ark --model kimi-k3-1-pro-261024
  python scripts/run_once.py --provider ark --model X --strip-exact   # 对照组
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.config import PROFILES, Settings  # noqa: E402
from dbw.delivery import deliver_once, load_capabilities  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="单轮真实交付")
    parser.add_argument("--provider", default="deepseek", choices=sorted(PROFILES))
    parser.add_argument("--model", default="")
    parser.add_argument("--contract", default="", help="契约路径；缺省用 fixtures/repo_a")
    parser.add_argument("--no-prepare", action="store_true", help="工作区已就绪，不要再复制")
    parser.add_argument("--task", default="", help="任务目录（tasks/xxx），自动读配方")
    parser.add_argument("--strip-exact", action="store_true", help="摘掉契约里的 exact 段（对照组）")
    args = parser.parse_args()

    profile = PROFILES[args.provider]
    settings = Settings()
    model = args.model or settings.get(profile.model_env) or profile.default_models[0]
    capabilities = load_capabilities(ROOT / "runs", profile.name, model)

    if args.task:
        task_dir = Path(args.task).resolve()
        recipe = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
        work_dir = ROOT / ".work" / "tasks" / recipe["id"]
        contract_path = work_dir / "contract.yaml"
        issue = recipe["statement"]
        prepare = False
        label = recipe["title"]
    else:
        work_dir = ROOT / ".work" / "repo_a"
        contract_path = Path(args.contract).resolve() if args.contract else ROOT / "fixtures" / "repo_a" / "contract.yaml"
        issue = "parse_kv 未剥离行内注释：'a=1  # note' 被解析成 '1  # note'；缺 '=' 时异常消息不对"
        prepare = not args.no_prepare
        label = "示例仓库 fix-parser-inline-comment"

    print("═" * 70)
    print("单轮真实交付：模型 → 补丁 → 闸门")
    print("═" * 70)
    print(f"  任务   {label}")
    print(f"  模型   {profile.name} / {model}")
    print(f"  工作区 {work_dir}")
    print(f"  exact  {'已摘掉（对照组）' if args.strip_exact else '生效'}")

    result = deliver_once(
        contract_path=contract_path,
        work_dir=work_dir,
        provider=profile.name,
        model=model,
        api_key=settings.require(profile.key_env),
        base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
        capabilities=capabilities,
        issue=issue,
        target_file="",
        strip_exact=args.strip_exact,
        prepare=prepare,
    )

    if result.error:
        print(f"  调用失败  {result.error}")
        return 1

    if result.gate is None:
        print(f"  未进入闸门：{result.reason}")
        return 1

    print(f"  调用   {result.latency_ms} ms，token {result.tokens}")
    print(f"  提案   {len(result.patch_files)} 个文件：{', '.join(result.patch_files)}")
    print(f"  理由   {result.rationale[:100]}")
    print("\n" + "─" * 70)
    print("闸门判定")
    print("─" * 70)
    if result.gate:
        print(result.gate.summary())
    print(f"\n  结论   {'交付成立' if result.outcome == 'accepted' else '未通过'}")
    print(f"  审计   runs/audit.jsonl（run {result.run_id}）")
    return 0 if result.outcome == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
