"""从测试源码提取精确断言，与契约对照。

用法：
  python scripts/extract_exact.py                 # 打印建议 + 一致性检查
  python scripts/extract_exact.py --check         # 只做一致性检查，漂移则退出码 1（供 CI 用）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import load_contract  # noqa: E402
from dbw.exact import check_consistency, extract_from_paths, render_exact_block  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="提取精确断言并与契约对照")
    parser.add_argument("--contract", default=str(ROOT / "fixtures" / "repo_a" / "contract.yaml"))
    parser.add_argument("--tests", default="", help="测试路径，默认取契约所在仓库的 tests 目录")
    parser.add_argument("--check", action="store_true", help="只检查一致性，漂移则退出码 1")
    args = parser.parse_args()

    contract = load_contract(args.contract)
    tests_path = Path(args.tests) if args.tests else contract.repo / "tests"

    extracted, notes = extract_from_paths([tests_path])

    if not args.check:
        print(f"扫描：{tests_path}")
        print(f"契约已声明 {len(contract.exact)} 条，测试中提取到 {len(extracted)} 条\n")
        print("=== 建议写入契约的条目 ===")
        if extracted:
            for spec in extracted:
                print(f"  - exception: {spec.exception}")
                print(f"    contains:  {spec.contains!r}")
                print(f"    source:    {spec.source}")
                print(f"    origin:    {spec.origin}\n")
        else:
            print("  （无）\n")
        if notes:
            print("=== 跳过（需人工确认）===")
            for note in notes:
                print(f"  - {note}")
            print()

    problems = check_consistency(contract.exact, extracted)
    if problems:
        print("=== 漂移：契约与测试不一致 ===")
        for problem in problems:
            print(f"  ! {problem}")
        return 1 if args.check else 0

    print("=== 一致性：契约与测试吻合 ===")
    block = render_exact_block(contract.exact)
    if block:
        print("\n注入给模型的文本：")
        for line in block.splitlines():
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
