"""问任意模型：把一段材料发过去，拿回意见并落盘。

用途：让不同模型评论文档、评审题目、挑逻辑漏洞。
不做结构化解析，只回文本——评审类任务不需要 JSON。

用法：
  python scripts/ask_model.py --prompt-file docs/prompts/题目评审.md --out runs/title-review-kimi.md
  python scripts/ask_model.py --provider ark --model deepseek-v4-1-flash-261017 \
      --prompt "用三句话指出下面的问题：……"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.adapters import OpenAICompatAdapter  # noqa: E402
from dbw.config import PROFILES, Settings  # noqa: E402
from dbw.delivery import load_capabilities  # noqa: E402
from dbw.protocol.base import Message, ModelRequest  # noqa: E402

DEFAULT_SYSTEM = "你是一位严格的评审专家。不要客套，不要复述我的说法，直接指出问题。"


def main() -> int:
    parser = argparse.ArgumentParser(description="问任意模型")
    parser.add_argument("--provider", default="ark", choices=sorted(PROFILES))
    parser.add_argument("--model", default="kimi-k3-1-pro-261024")
    parser.add_argument("--prompt-file", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--system", default=DEFAULT_SYSTEM)
    parser.add_argument("--out", default="")
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=240.0, help="秒；推理模型要放宽")
    args = parser.parse_args()

    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    elif args.prompt:
        prompt = args.prompt
    else:
        raise SystemExit("需要 --prompt 或 --prompt-file")

    profile = PROFILES[args.provider]
    settings = Settings()
    capabilities = load_capabilities(ROOT / "runs", profile.name, args.model)
    adapter = OpenAICompatAdapter(
        provider=profile.name,
        base_url=settings.get(profile.base_env, profile.default_base) or profile.default_base,
        api_key=settings.require(profile.key_env),
        model=args.model,
        capabilities=capabilities,
        timeout_s=args.timeout,
        max_retries=1,
    )

    temperature = (
        capabilities.fixed_temperature if capabilities.fixed_temperature is not None else 0.3
    )
    started = time.perf_counter()
    response = adapter.complete(
        ModelRequest(
            messages=(Message("system", args.system), Message("user", prompt)),
            max_tokens=args.max_tokens,
            temperature=temperature,
            timeout_s=args.timeout,
            idempotency_key=f"ask:{profile.name}:{args.model}:{int(time.time())}",
        )
    )
    elapsed = time.perf_counter() - started

    print(response.text.strip())
    if not response.text.strip():
        hint = "（finish_reason=length，输出预算被推理过程耗尽）" if response.finish_reason == "length" else ""
        print(f"[警告] 模型没有返回正文{hint}。请加大 --max-tokens 重试。")
    print("\n" + "─" * 78)
    print(
        f"{profile.name}/{response.model}  {elapsed:.1f}s  "
        f"token {response.usage.total}（输入 {response.usage.prompt} / 输出 {response.usage.completion}）  "
        f"finish={response.finish_reason}"
        + (f"  降级：{response.degraded_reason}" if response.degraded else "")
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            f"# 评审：{profile.name}/{response.model}\n\n"
            f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- 耗时：{elapsed:.1f}s\n"
            f"- token：{response.usage.total}\n\n---\n\n{response.text.strip()}\n",
            encoding="utf-8",
        )
        print(f"已保存 {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
