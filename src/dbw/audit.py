"""审计日志：一切可追溯性都从这里来。

一行一个 JSON 对象，append-only。要求：
- 每次运行有唯一 run_id；
- 每次模型调用记录 provider / role / token / 延迟 / prompt 摘要；
- 每次闸门判定记录逐项结果；
- 每次人工决策记录决策人与结论。

论文第 6 章的所有数字，都应该能在这份日志里找到出处。
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def new_run_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def git_sha(repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


class AuditLog:
    """append-only JSONL 审计日志。"""

    def __init__(self, path: str | Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or new_run_id()
        self._fp = open(self.path, "a", encoding="utf-8")
        self._count = 0

    def emit(self, event: str, **fields) -> dict:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        self._fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fp.flush()
        self._count += 1
        return record

    @property
    def count(self) -> int:
        return self._count

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.close()

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
