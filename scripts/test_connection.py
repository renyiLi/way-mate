"""测通路：先确认这根管子是通的，再谈协议。

四步，对每个模型都跑一遍：
  1. 基本对话      —— 密钥、地址、模型名是否可用
  2. 能力探测      —— json_object / json_schema / tools / seed 逐项实测
  3. 协议回环      —— 真实模型跑一次「写作接口」，看能否产出合法补丁
  4. 能力落盘      —— 写 runs/capabilities.<provider>.<model>.json（协议 R-P0-1）

用法：
  python scripts/test_connection.py
  python scripts/test_connection.py --provider ark
  python scripts/test_connection.py --provider ark --models a,b,c
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.adapters import OpenAICompatAdapter  # noqa: E402
from dbw.config import PROFILES, ModelProfile, Settings  # noqa: E402
from dbw.contract import load_contract  # noqa: E402
from dbw.protocol.base import (  # noqa: E402
    AdapterError,
    Capabilities,
    Message,
    ModelRequest,
    ToolSpec,
    extract_json,
)
from dbw.protocol.interfaces import AuthoringInterface  # noqa: E402

RUNS = ROOT / "runs"

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "word": {"type": "string"}},
    "required": ["ok", "word"],
    "additionalProperties": False,
}
TOOL = ToolSpec(name="noop", description="无副作用的连通性测试工具", parameters={"type": "object", "properties": {}})


def rule(width: int = 78) -> str:
    return "─" * width


def probe(label: str, adapter: OpenAICompatAdapter, request: ModelRequest) -> dict:
    started = time.perf_counter()
    try:
        response = adapter.complete(request)
    except AdapterError as exc:
        return {"label": label, "ok": False, "ms": int((time.perf_counter() - started) * 1000), "error": str(exc)[:200]}
    return {
        "label": label,
        "ok": True,
        "ms": response.latency_ms,
        "degraded": response.degraded,
        "finish_reason": response.finish_reason,
        "usage": response.usage.to_dict(),
        "text": response.text.strip()[:60],
    }


def build_task() -> dict:
    fixture = ROOT / "fixtures" / "repo_a"
    return {
        "task_id": "fix-parser-inline-comment",
        "issue": "parse_kv 未剥离行内注释：'a=1  # note' 被解析成 '1  # note'；缺 '=' 时异常消息不对",
        "failing_tests": [
            "test_inline_comment_stripped: assert {'a': '1  # note'} == {'a': '1'}",
            "test_missing_equals_raises: ValueError 消息不含 \"missing '='\"",
        ],
        "repo_layout": ["pkg/parser.py", "tests/test_parser.py", "contract.yaml"],
        "target_file": "pkg/parser.py",
        "current_file": (fixture / "pkg" / "parser.py").read_text(encoding="utf-8"),
    }


def probe_model(profile: ModelProfile, model: str, settings: Settings, contract, task: dict) -> dict:
    api_key = settings.require(profile.key_env)
    base_url = settings.get(profile.base_env, profile.default_base) or profile.default_base

    state: dict[str, float | None] = {"fixed": None}

    def make(**caps) -> OpenAICompatAdapter:
        return OpenAICompatAdapter(
            provider=profile.name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            capabilities=Capabilities(fixed_temperature=state["fixed"], **caps),
            timeout_s=90.0,
            max_retries=1,
        )

    print(f"\n{rule()}\n模型 {model}\n{rule()}")

    basic = probe(
        "basic",
        make(supports_json_schema=False, supports_json_object=False),
        ModelRequest(messages=(Message("user", "只回复两个字：通了"),), max_tokens=256, temperature=0.2, timeout_s=60.0),
    )
    if not basic["ok"] and "temperature" in basic["error"]:
        # 参数取值域限制：部分推理模型不接受自定义 temperature
        state["fixed"] = 1.0
        basic = probe(
            "basic",
            make(supports_json_schema=False, supports_json_object=False),
            ModelRequest(
                messages=(Message("user", "只回复两个字：通了"),), max_tokens=256, temperature=1.0, timeout_s=60.0
            ),
        )
        if basic["ok"]:
            print("  [约束] temperature 只能取 1.0，已按固定值重新握手（降级需登记）")

    if not basic["ok"]:
        print(f"  [失败] {basic['error']}")
        return {"model": model, "basic": basic, "ok": False}

    fixed = state["fixed"]
    T = fixed if fixed is not None else 0.0
    print(f"  [通路] {basic['ms']} ms → {basic['text']!r}  token {basic['usage']['total']}")

    probes = {
        "json_object": (
            make(supports_json_schema=False, supports_json_object=True),
            ModelRequest(
                messages=(Message("user", '以 JSON 输出 {"ok": true, "word": "通"}'),),
                response_schema=SCHEMA,
                max_tokens=256,
                temperature=T,
                timeout_s=60.0,
            ),
        ),
        "json_schema": (
            make(supports_json_schema=True, supports_json_object=True),
            ModelRequest(
                messages=(Message("user", '输出 ok=true, word="通"'),),
                response_schema=SCHEMA,
                max_tokens=256,
                temperature=T,
                timeout_s=60.0,
            ),
        ),
        "tools": (
            make(supports_tools=True),
            ModelRequest(
                messages=(Message("user", "调用 noop 工具，不要别的动作。"),),
                tools=(TOOL,),
                max_tokens=256,
                temperature=T,
                timeout_s=60.0,
            ),
        ),
        "seed": (
            make(supports_seed=True),
            ModelRequest(
                messages=(Message("user", "回复：种子"),), seed=42, max_tokens=256, temperature=T, timeout_s=60.0
            ),
        ),
    }

    results: dict[str, dict] = {}
    for name, (adapter, request) in probes.items():
        outcome = probe(name, adapter, request)
        results[name] = outcome
        if outcome["ok"]:
            note = "（降级）" if outcome.get("degraded") else ""
            print(f"  [支持] {name:<12}{note} {outcome['ms']} ms")
        else:
            print(f"  [不支持] {name:<10} {outcome['error'][:100]}")

    capabilities = Capabilities(
        supports_tools=results["tools"]["ok"],
        supports_json_schema=results["json_schema"]["ok"],
        supports_json_object=results["json_object"]["ok"] or results["json_schema"]["ok"],
        supports_seed=results["seed"]["ok"],
        supports_stream=False,
        max_context=64_000,
        fixed_temperature=fixed,
    )

    # ---- 协议回环
    interface = AuthoringInterface()
    adapter = OpenAICompatAdapter(
        provider=profile.name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        capabilities=capabilities,
        timeout_s=150.0,
        max_retries=1,
    )
    round_trip: dict = {"ok": False}
    try:
        response = adapter.complete(
            ModelRequest(
                messages=(
                    Message("system", interface.system_prompt(contract)),
                    Message("user", json.dumps(task, ensure_ascii=False)),
                ),
                response_schema=interface.propose_schema(),
                max_tokens=3072,
                temperature=T,
                timeout_s=150.0,
            )
        )
        patch = interface.parse_proposal(extract_json(response.text), contract)
        forbidden = [p for p in patch.files if any(rule_ in p for rule_ in contract.diff.forbid)]
        round_trip = {
            "ok": True,
            "files": list(patch.files),
            "path_correct": all(p == "pkg/parser.py" for p in patch.files),
            "touched_forbidden": forbidden,
            "rationale": patch.rationale[:160],
            "usage": response.usage.to_dict(),
            "ms": response.latency_ms,
            "degraded": response.degraded,
        }
        mark = "✓" if round_trip["path_correct"] else "✗ 路径不对"
        print(f"  [回环] {response.latency_ms} ms，{len(patch.files)} 个文件 {mark}，token {response.usage.total}")
        print(f"         {patch.rationale[:90]}")
    except Exception as exc:  # noqa: BLE001 探测脚本要如实报告任何失败
        round_trip = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:240]}
        print(f"  [回环] 失败：{round_trip['error']}")

    report = {
        "provider": profile.name,
        "base_url": base_url,
        "model": model,
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "capabilities": capabilities.to_dict(),
        "probes": results,
        "basic": basic,
        "round_trip": round_trip,
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", model)
    (RUNS / f"capabilities.{profile.name}.{safe}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"model": model, "ok": True, "capabilities": capabilities, "round_trip": round_trip, "basic": basic, "probes": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="模型通路与能力探测")
    parser.add_argument("--provider", default="deepseek", choices=sorted(PROFILES))
    parser.add_argument("--models", default="", help="逗号分隔；留空则用 .env 里的模型或默认值")
    args = parser.parse_args()

    profile = PROFILES[args.provider]
    settings = Settings()
    if not settings.get(profile.key_env):
        print(f"[失败] 缺少 {profile.key_env}，请检查 {settings.env_path}")
        return 2

    if args.models.strip():
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        configured = settings.get(profile.model_env)
        models = [configured] if configured else list(profile.default_models)

    print("═" * 78)
    print(f"通路测试 · {profile.name} · {len(models)} 个模型")
    print("═" * 78)
    print(f"  密钥   {settings.masked(profile.key_env)}")
    print(f"  地址   {settings.get(profile.base_env, profile.default_base)}")

    contract = load_contract(ROOT / "fixtures" / "repo_a" / "contract.yaml")
    task = build_task()
    summaries = [probe_model(profile, model, settings, contract, task) for model in models]

    # ---- 汇总表
    print(f"\n{'═' * 78}\n汇总\n{'═' * 78}")
    header = f"{'模型':<34}{'首包':<8}{'schema':<8}{'tools':<7}{'seed':<6}{'回环':<6}{'token'}"
    print(header)
    print("-" * 78)
    for item in summaries:
        if not item.get("ok"):
            print(f"{item['model']:<34}{'未通'}（{item['basic'].get('error', '')[:40]}）")
            continue
        caps = item["capabilities"]
        trip = item["round_trip"]
        tokens = trip.get("usage", {}).get("total", 0) if trip.get("ok") else 0
        print(
            f"{item['model']:<34}{item['basic']['ms']:<8}{'✓' if caps.supports_json_schema else '✗':<8}"
            f"{'✓' if caps.supports_tools else '✗':<7}{'✓' if caps.supports_seed else '✗':<6}"
            f"{'✓' if trip.get('ok') else '✗':<6}{tokens}"
        )

    healthy = [s for s in summaries if s.get("ok") and s["round_trip"].get("ok")]
    print(f"\n可用且回环通过：{len(healthy)}/{len(summaries)}")
    print(f"能力明细已写入 runs/capabilities.{profile.name}.*.json")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
