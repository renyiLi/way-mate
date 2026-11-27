"""从真实 PR 挖一条任务：验证红/绿，产出 task.json + contract.yaml + test_patch.diff。

用法：
  python scripts/mine_task.py --repo https://github.com/more-itertools/more-itertools \
      --pr 1322 --out tasks/moreitertools-1322
  python scripts/mine_task.py --repo <本地克隆> --pr 1322 --out /tmp/x   # 离线，跳过 fetch

流程与人工挖掘一致：
  1. 定位 PR 的 base 提交；
  2. 只取 PR 的测试改动，在 base 上跑测试 → 收集失败用例；
  3. 再把 PR 的源码改动叠上 → 必须全绿（否则这条任务不可用）；
  4. 生成测试补丁、契约草案与配方。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GIT = r"E:\Git\cmd\git.exe" if Path(r"E:\Git\cmd\git.exe").exists() else "git"

FAILED_RE = re.compile(r"^(?:SUB)?FAILED\s*(?:\([^)]*\)\s*)?(\S+?)::(\S+)", re.MULTILINE)
SUMMARY_RE = re.compile(r"^(\d+) failed", re.MULTILINE)


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        [GIT, *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{proc.stderr.strip()[:300]}")
    return proc.stdout


def parse_failed_nodes(output: str) -> list[str]:
    """从 pytest 输出里取失败用例的 node id，统一成正斜杠并去重。"""
    nodes: list[str] = []
    for match in FAILED_RE.finditer(output):
        # pytest 在 Windows 上输出反斜杠路径；统一成正斜杠，否则与 task.json 里的写法不一致
        node = f"{match.group(1)}::{match.group(2)}".replace("\\", "/")
        if node not in nodes:
            nodes.append(node)
    return nodes


def run_pytest(repo: Path, python: str, target: str = "") -> tuple[list[str], str]:
    cmd = [python, "-m", "pytest", "-q", "--tb=no"]
    if target:
        cmd.append(target)
    proc = subprocess.run(
        cmd, cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    output = proc.stdout + proc.stderr
    nodes = parse_failed_nodes(output)
    return nodes, output.strip().splitlines()[-1] if output.strip() else ""


def github_token() -> str:
    proc = subprocess.run(
        [GIT, "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def parse_github_url(repo_url: str) -> tuple[str, str] | None:
    """从 GitHub URL 里取 (owner, repo)。不是 GitHub 就返回 None。"""
    url = repo_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+)$", url)
    return (match.group(1), match.group(2)) if match else None


def api_base_sha(repo_url: str, pr: int) -> tuple[str, str]:
    """取 PR 声明的 base 提交。返回 (sha, 说明)；取不到时 sha 为空串。"""
    parsed = parse_github_url(repo_url)
    if not parsed:
        return "", f"不是 GitHub 仓库（{repo_url[:60]}）"
    owner, name = parsed
    token = github_token()
    api = f"https://api.github.com/repos/{owner}/{name}/pulls/{pr}"
    headers = {"User-Agent": "dbw-mine_task", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read())
        sha = str(payload.get("base", {}).get("sha", ""))
        return (sha, "GitHub API") if sha else ("", "API 未返回 base.sha")
    except Exception as exc:  # noqa: BLE001 离线/限流都只是降级
        return "", f"API 查询失败（{type(exc).__name__}）"


def main() -> int:
    parser = argparse.ArgumentParser(description="从真实 PR 挖一条任务")
    parser.add_argument("--repo", required=True, help="仓库 URL 或本地克隆路径")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--out", required=True, help="输出目录，如 tasks/moreitertools-1322")
    parser.add_argument("--id", default="", help="任务 id；默认由仓库名与 PR 号拼出")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--work", default="", help="工作副本目录；默认用系统临时目录")
    parser.add_argument(
        "--source-glob", default="", help="源码路径匹配，默认取 PR 里非测试的 .py 改动"
    )
    parser.add_argument("--base", default="", help="显式指定 base 提交；缺省用 PR base.sha 或 merge-base")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    task_id = args.id or f"{Path(args.repo.rstrip('/')).name.replace('-', '')}-{args.pr}"
    work = Path(args.work) if args.work else ROOT / ".work" / "mine" / task_id
    if work.exists():
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(work)], capture_output=True)
        if work.exists():
            raise SystemExit(f"无法清理 {work}")

    print(f"═══ 挖掘 {task_id} ═══")
    print(f"  克隆 {args.repo} → {work}")
    git(ROOT, "clone", "--quiet", str(args.repo), str(work))

    pr_ref = f"pr{args.pr}"

    def has_pr_ref() -> bool:
        if git(work, "show-ref", "--verify", f"refs/heads/{pr_ref}", check=False).strip():
            return True
        if git(work, "show-ref", "--verify", f"refs/remotes/origin/{pr_ref}", check=False).strip():
            git(work, "branch", "--quiet", pr_ref, f"origin/{pr_ref}", check=False)
            return bool(git(work, "show-ref", "--verify", f"refs/heads/{pr_ref}", check=False).strip())
        return False

    if has_pr_ref():
        print(f"  取 PR #{args.pr}（源仓库已带 {pr_ref}，跳过联网）")
    else:
        print(f"  取 PR #{args.pr}")
        # 以本地克隆作源时，它的 origin 才是真仓库：PR 引用必须从那边取
        fetch_url = ""
        if not str(args.repo).startswith("http"):
            fetch_url = git(Path(args.repo), "remote", "get-url", "origin", check=False).strip()
        if fetch_url:
            git(work, "remote", "add", "upstream", fetch_url)
            git(work, "fetch", "--quiet", "upstream", f"pull/{args.pr}/head:{pr_ref}", check=False)
        else:
            git(work, "fetch", "--quiet", "origin", f"pull/{args.pr}/head:{pr_ref}", check=False)
        if not has_pr_ref():
            raise SystemExit(
                f"取不到 PR #{args.pr}。离线时请先在源仓库里执行：\n"
                f"    git -C {args.repo} fetch origin pull/{args.pr}/head:{pr_ref}"
            )

    # base 提交：优先用 PR 声明的 base（否则 PR 的测试文件会套在更早的代码上，
    # 连带打挂一堆与本次修复无关的用例），取不到才退回 merge-base。
    origin_url = git(work, "remote", "get-url", "origin", check=False).strip()
    if not origin_url.startswith("http") and not str(args.repo).startswith("http"):
        origin_url = git(Path(args.repo), "remote", "get-url", "origin", check=False).strip()
    api_base, api_note = api_base_sha(origin_url, args.pr)
    has_api_commit = bool(api_base) and subprocess.run(
        [GIT, "cat-file", "-e", f"{api_base}^{{commit}}"], cwd=str(work), capture_output=True
    ).returncode == 0

    if args.base:
        base = git(work, "rev-parse", args.base).strip()
        source_of_base = "命令行 --base"
    elif has_api_commit:
        base = api_base
        source_of_base = "PR base.sha（GitHub API）"
    else:
        base = git(work, "merge-base", "HEAD", pr_ref).strip()
        source_of_base = f"merge-base（{api_note}；失败用例可能偏多）"
    base_short = base[:10]
    print(f"  base = {base_short}（{source_of_base}）")

    changed = git(work, "diff", "--name-only", base, pr_ref).splitlines()
    test_files = [p for p in changed if p.startswith("tests/") or "/test" in p or p.startswith("test")]
    source_files = [p for p in changed if p not in test_files and p.endswith(".py")]
    if not test_files:
        raise SystemExit("PR 没有测试改动，不适合做任务（无法验证红/绿）")
    if not source_files:
        raise SystemExit("PR 没有源码改动，不适合做任务")
    print(f"  测试文件 {test_files}")
    print(f"  源码文件 {source_files}")

    # 1) 先在 base 上跑一遍，拿到"改测试之前就已经失败的用例"
    #    这样即使 base 选得偏（merge-base 与 PR 声明的 base 可能不同），
    #    也能把无关失败减掉，只留下这条 PR 真正暴露出来的用例。
    git(work, "checkout", "--force", "--quiet", base)
    prev_nodes, prev_summary = run_pytest(work, args.python)
    print(f"  base 基线：{prev_summary}（已有失败 {len(prev_nodes)} 条）")

    # 2) base + PR 的测试 → 新增的失败才是这条任务要修的
    for path in test_files:
        git(work, "checkout", pr_ref, "--", path)
    red_all, red_summary = run_pytest(work, args.python)
    red_nodes = [node for node in red_all if node not in prev_nodes]
    print(f"  旧源码 + 新测试：{red_summary} → 新增失败 {len(red_nodes)} 条")
    if not red_nodes:
        raise SystemExit("新测试在旧源码上没有引入失败，这条 PR 不适合做任务")

    # 3) 叠加 PR 的源码改动 → 新增失败必须全部消失
    for path in source_files:
        git(work, "checkout", pr_ref, "--", path)
    green_nodes, green_summary = run_pytest(work, args.python)
    still = [node for node in green_nodes if node not in prev_nodes]
    print(f"  加修复：{green_summary}")
    if still:
        raise SystemExit(f"加修复后仍有新增失败：{still[:3]}")

    # 3) 生成产物
    out.mkdir(parents=True, exist_ok=True)
    patch = git(work, "diff", base, pr_ref, "--", *test_files)
    (out / "test_patch.diff").write_bytes(patch.encode("utf-8"))

    forbidden = sorted({path.split("/")[0] + "/" for path in test_files if "/" in path})
    recipe = {
        "id": task_id,
        "repo": args.repo if args.repo.startswith("http") else git(work, "remote", "get-url", "origin").strip(),
        "base_sha": base,
        "pull_request": args.pr,
        "title": f"PR #{args.pr}（待补标题）",
        "statement": "（待补：把 PR 描述改写成只讲症状、不给解法的任务陈述）",
        "target_test": red_nodes,
        "test_patch": "test_patch.diff",
        "target_files": source_files,
        "forbidden": forbidden,
        "tier": "medium",
        "solution_note": "（待补：说明上游修法，但不要写进 statement）",
        "verified": {"red_without_fix": red_summary, "green_with_fix": green_summary},
        "mined_by": "scripts/mine_task.py",
    }
    (out / "task.json").write_bytes(
        json.dumps(recipe, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )

    cmd = "python -m pytest " + " ".join(red_nodes) + " -q"
    contract = (
        f"task_id: {task_id}\n"
        "repo: .\n\n"
        "doD:\n"
        "  tests:\n"
        f"    - cmd: {cmd}\n"
        "      timeout_s: 180\n\n"
        "  lint:\n"
        "    tool: ruff\n"
        "    max_new: 0\n\n"
        "  api:\n"
        "    frozen: []\n\n"
        "  exact: []\n\n"
        "  diff:\n"
        "    max_files: 3\n"
        "    max_lines: 120\n"
        "    forbid:\n" + "".join(f"      - {item}\n" for item in forbidden) + "\n"
        "loop:\n"
        "  repair_rounds: 3\n"
        "  stop_on: all_green\n"
        "  on_exhausted: escalate_to_human\n"
    )
    (out / "contract.yaml").write_bytes(contract.encode("utf-8"))

    print(f"\n产物写入 {out}")
    print(f"  base_sha   {base}")
    print(f"  失败用例   {len(red_nodes)} 条")
    print("  待人工补   title / statement / solution_note")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
