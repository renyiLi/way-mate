"""本地执行环境：在指定目录里跑 bash 命令，带回输出。

改编自 mini-swe-agent 的 `environments/local.py`（MIT，见同目录 NOTICE）。
改动：
- 去掉 pydantic 配置类，改为显式参数；
- 输出统一截断，避免观测撑爆上下文；
- 超时处理保留"杀整个进程组"，否则子进程会变成孤儿。
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

# 模型用它声明"我完成了"。语义与 mini-swe-agent 保持一致。
SUBMIT_SENTINEL = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

MAX_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class Observation:
    output: str
    returncode: int
    timed_out: bool = False

    @property
    def submitted(self) -> bool:
        """首行是提交哨兵且退出码为 0。"""
        lines = self.output.lstrip().splitlines()
        return bool(lines) and lines[0].strip() == SUBMIT_SENTINEL and self.returncode == 0

    @property
    def submission(self) -> str:
        lines = self.output.lstrip().splitlines(keepends=True)
        return "".join(lines[1:]).strip()

    def render(self) -> str:
        body = self.output
        if len(body) > MAX_OUTPUT_CHARS:
            body = body[:MAX_OUTPUT_CHARS] + f"\n…（已截断，原长 {len(self.output)} 字符）"
        if self.timed_out:
            return f"命令超时被终止。\n{body}"
        return f"退出码 {self.returncode}\n{body}"


class LocalEnvironment:
    """工作区内的命令执行器。不是沙箱——它只隔离工作目录，不隔离权限。

    真正的沙箱（容器、断网、只读挂载）留给后续接入，接口不变即可替换。
    """

    name = "local"

    def __init__(self, workspace: Path, *, timeout_s: float = 30.0) -> None:
        self.workspace = Path(workspace)
        self.timeout_s = timeout_s

    def execute(self, command: str, *, timeout_s: float | None = None) -> Observation:
        command = command.strip()
        if not command:
            return Observation(output="（空命令）", returncode=1)

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # 让子进程的输出别被分页器或交互式提示卡住
        env.setdefault("PAGER", "cat")
        env.setdefault("GIT_PAGER", "cat")

        try:
            process = subprocess.Popen(
                command,
                shell=True,
                text=True,
                cwd=str(self.workspace),
                env=env,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            return Observation(output=f"无法启动命令：{exc}", returncode=-1)

        try:
            stdout, _ = process.communicate(timeout=timeout_s or self.timeout_s)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
            else:
                process.kill()
            stdout, _ = process.communicate()
            return Observation(output=stdout or "", returncode=-1, timed_out=True)

        return Observation(output=stdout or "", returncode=process.returncode or 0)
