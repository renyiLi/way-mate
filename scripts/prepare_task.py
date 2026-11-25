"""按任务配方准备一个可交付的工作区。

配方（`tasks/<id>/`）只存三样东西：仓库地址 + 基线提交、测试补丁、契约。
工作区在 `.work/tasks/<id>/` 里现场克隆出来，不进版本库。

用法：
  python scripts/prepare_task.py --task tasks/tabulate-366
  python scripts/prepare_task.py --task tasks/tabulate-366 --no-verify
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import load_contract  # noqa: E402
from dbw.delivery import force_rmtree  # noqa: E402


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:400]}")
    return proc


def prepare(task_dir: Path, out: Path, python: str, verify: bool) -> dict:
    recipe = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    base = recipe["base_sha"]

    if out.exists():
        # Windows 上 .git 里的对象是只读的，普通 rmtree 会静默半途失败
        force_rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"  克隆 {recipe['repo']}")
    proc = subprocess.run(
        ["git", "clone", "--quiet", recipe["repo"], str(out)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"克隆失败：{proc.stderr.strip()[:400]}")

    print(f"  切到基线 {base[:10]}")
    # 关掉行尾转换：补丁是按 LF 生成的，工作区若被转成 CRLF，git apply 会失败
    git(out, "config", "core.autocrlf", "false")
    git(out, "checkout", "--force", "--quiet", base)

    patch = (task_dir / recipe["test_patch"]).read_text(encoding="utf-8")
    patch_file = out / ".task-test.patch"
    # 必须二进制写入：write_text 会把 \n 翻译成 CRLF，补丁一带 CRLF 就对不上
    patch_file.write_bytes(patch.encode("utf-8"))
    applied = git(out, "apply", "--whitespace=nowarn", str(patch_file), check=False)
    patch_file.unlink(missing_ok=True)
    if applied.returncode != 0:
        raise SystemExit(f"测试补丁应用失败：{applied.stderr.strip()[:400]}")
    print("  测试补丁已应用")

    shutil.copyfile(task_dir / "contract.yaml", out / "contract.yaml")

    # 测试补丁与契约一起提交：这样 HEAD 就是"任务基线"，
    # 闸门算改动时只看模型干了什么，不会把测试补丁当成模型的越界修改。
    git(out, "add", "-A")
    git(
        out,
        "-c", "user.name=task", "-c", "user.email=task@local",
        "commit", "--quiet", "-m", f"task: {recipe['id']} 基线（含测试与契约）",
    )
    print(f"  任务基线已提交：{git(out, 'rev-parse', '--short', 'HEAD').stdout.strip()}")

    info = {
        "task_id": recipe["id"],
        "repo": recipe["repo"],
        "base_sha": base,
        "workspace": str(out),
        "target_test": recipe["target_test"],
        "verified": False,
        "red": None,
    }

    if verify:
        contract = load_contract(out / "contract.yaml")
        cmd = [python, "-m", "pytest", recipe["target_test"], "-q"]
        print(f"  校验：{recipe['target_test']} 在旧源码上应当失败")
        run = subprocess.run(
            cmd, cwd=str(out), capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        tail = [line for line in (run.stdout or "").splitlines() if line.strip()][-1:] or [""]
        info["red"] = tail[0]
        info["verified"] = run.returncode != 0
        mark = "✓ 失败（符合预期）" if info["verified"] else "✗ 竟然通过了，任务无效"
        print(f"    {mark}：{tail[0]}")
        _ = contract

    # 写在任务目录旁边，不要写进工作区：否则会被 diff 闸门算成一个改动文件
    meta = out.parent / f"{recipe['id']}.prepared.json"
    meta.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="准备任务工作区")
    parser.add_argument("--task", required=True, help="任务目录，如 tasks/tabulate-366")
    parser.add_argument("--out", default="", help="工作区目录，默认 .work/tasks/<id>")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-verify", action="store_true")
    args = parser.parse_args()

    task_dir = Path(args.task).resolve()
    recipe = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    out = Path(args.out).resolve() if args.out else ROOT / ".work" / "tasks" / recipe["id"]

    print("═" * 68)
    print(f"准备任务：{recipe['id']}  {recipe['title']}")
    print("═" * 68)
    info = prepare(task_dir, out, args.python, not args.no_verify)

    print("\n" + "─" * 68)
    print(f"工作区  {info['workspace']}")
    print(f"校验    {'通过（旧源码上确实失败）' if info['verified'] else '未通过'}")
    print(f"下一步  python scripts/run_once.py 需要指向 {info['workspace']}/contract.yaml")
    return 0 if info["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
