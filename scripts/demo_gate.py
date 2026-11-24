"""端到端演示：先证明"闸门"这层裁判可靠，再接模型。

三个场景都不需要任何 API Key：
  1. 修复前          → 测试红，整体拒绝
  2. 应用修复        → 全绿，可交付
  3. 改测试作弊      → 测试全绿，但被 diff 闸门拦下

运行：python scripts/demo_gate.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.audit import AuditLog, git_sha  # noqa: E402
from dbw.contract import load_contract  # noqa: E402
from dbw.gates import lint_baseline, run_all  # noqa: E402

FIXTURE = ROOT / "fixtures" / "repo_a"
MODEL_OUTPUT = ROOT / "fixtures" / "model_output"
WORK = ROOT / ".work" / "repo_a"
RUNS = ROOT / "runs"


def git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")


def _force_rmtree(path: Path) -> None:
    """删掉旧工作区。git 对象是只读的，Windows 上要先去掉只读位。"""
    import os
    import stat

    def on_error(func, target, _exc_info):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=on_error)


def prepare_workspace() -> None:
    """把示例仓库复制到工作区并建立基线提交。"""
    _force_rmtree(WORK)
    WORK.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, WORK, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))

    identity = ["-c", "user.name=dbw", "-c", "user.email=dbw@local"]
    git(WORK, "init", "-q")
    git(WORK, *identity, "add", "-A")
    git(WORK, *identity, "commit", "-q", "-m", "base")


def main() -> int:
    prepare_workspace()
    contract = load_contract(WORK / "contract.yaml")
    baseline = lint_baseline(contract, WORK)

    RUNS.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(RUNS / "audit.jsonl")

    audit.emit(
        "run_start",
        task_id=contract.task_id,
        git_sha=git_sha(WORK),
        contract=contract.to_dict(),
        lint_baseline=baseline,
    )
    print(f"契约：{contract.task_id}")
    print(f"运行：{audit.run_id}")
    print(f"基线：{git_sha(WORK)}　静态检查基线：{baseline.get('issues')}")

    def evaluate(label: str):
        report = run_all(contract, WORK, baseline, base_ref="HEAD")
        audit.emit("gate_report", phase=label, git_sha=git_sha(WORK), report=report.to_dict())
        print(f"\n── {label}")
        print(report.summary())
        return report

    # 场景一：未修复
    r1 = evaluate("场景一　未修复（期望：拒绝）")

    # 场景二：应用模型产出的修复
    shutil.copyfile(MODEL_OUTPUT / "parser.fixed.py", WORK / "pkg" / "parser.py")
    r2 = evaluate("场景二　应用修复（期望：全绿）")

    # 场景三：回滚代码，只改测试
    git(WORK, "checkout", "--", ".")
    shutil.copyfile(MODEL_OUTPUT / "tests.cheat.py", WORK / "tests" / "test_parser.py")
    r3 = evaluate("场景三　改测试作弊（期望：拒绝）")

    checks = [
        ("场景一 拒绝未修复的代码", r1.all_green is False),
        ("场景二 放行正确的修复", r2.all_green is True),
        ("场景三 拦住改测试作弊", r3.all_green is False),
    ]
    print("\n── 判定")
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'BAD'}] {name}")

    (RUNS / "last_report.json").write_text(
        json.dumps(
            {
                "run_id": audit.run_id,
                "scenarios": {
                    "before_fix": r1.to_dict(),
                    "after_fix": r2.to_dict(),
                    "cheat": r3.to_dict(),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    audit.emit("run_end", checks={name: passed for name, passed in checks}, events=audit.count)
    audit.close()

    print(f"\n审计日志：{(RUNS / 'audit.jsonl').relative_to(ROOT)}（{audit.count} 条）")
    ok = all(passed for _, passed in checks)
    print("结果：闸门行为符合预期" if ok else "结果：闸门行为与预期不符，请检查")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
