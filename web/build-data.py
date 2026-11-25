#!/usr/bin/env python3
"""构建期生成前端静态数据。

读 `tasks/*/task.json`、`tasks/*/contract.yaml`、`runs/audit.jsonl`，
写出两个文件：

- `web/data/site.json` —— 给工具、论文与人工检查用；
- `web/data/site.js`   —— 同一份数据包成 `window.__DBW_DATA__`，页面直接 `file://` 也能读，
                          不需要本地服务器，也不需要 fetch。

字段只取 API.md 承诺的稳定面：闸门固定四项与顺序、`all_green` 是唯一判定、
`outcome` 枚举、审计事件名、`run_id` 主键。`detail` / `reason` / `rationale`
只作为展示文本原样带出，不参与任何判定；`metrics.tail` 体积大且不入前端，已丢弃。

用法：

    python web/build-data.py            # 生成到 web/data/
    python web/build-data.py --check    # 只报告，不落盘

`runs/` 不入库，换台机器要重跑一次才有数据；没有 `runs/audit.jsonl` 时脚本照样
成功，产出空的 runs 列表，页面会显示空态而不是报错。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # 契约是可选输入，没装 pyyaml 也能生成
    yaml = None

GATE_ORDER = ("tests", "lint", "api", "diff")
LOCAL_TZ = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def local_iso(moment):
    if moment is None:
        return None
    return moment.astimezone(LOCAL_TZ).isoformat(timespec="seconds")


def load_tasks(root: Path):
    tasks = []
    for path in sorted(root.glob("tasks/*/task.json")):
        data = read_json(path)
        contract_path = path.with_name("contract.yaml")
        contract, raw = None, None
        if contract_path.exists():
            raw = read_text(contract_path)
            if yaml is not None:
                try:
                    contract = yaml.safe_load(raw) or None
                except yaml.YAMLError:
                    contract = None
        tasks.append(
            {
                "id": data.get("id") or path.parent.name,
                "title": data.get("title") or "",
                "repo": data.get("repo") or "",
                "tier": data.get("tier") or "",
                "statement": data.get("statement") or "",
                "target_test": data.get("target_test") or "",
                "target_files": data.get("target_files") or [],
                "forbidden": data.get("forbidden") or [],
                "base_sha": (data.get("base_sha") or "")[:12],
                "pull_request": data.get("pull_request"),
                "upstream_issue": data.get("upstream_issue") or "",
                "verified": data.get("verified") or {},
                "contract": contract,
                "contract_yaml": raw,
                "contract_source": str(contract_path.relative_to(root)).replace("\\", "/") if raw else "",
                "source": str(path.relative_to(root)).replace("\\", "/"),
            }
        )
    return tasks


def load_events(root: Path):
    path = root / "runs" / "audit.jsonl"
    if not path.exists():
        return [], 0, 0
    events, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    return events, len(events) + bad, bad


def slim_gate(report):
    if not isinstance(report, dict):
        return None
    results = []
    for item in report.get("results") or []:
        if not isinstance(item, dict):
            continue
        metrics = dict(item.get("metrics") or {})
        metrics.pop("tail", None)
        results.append(
            {
                "name": item.get("name"),
                "ok": bool(item.get("ok")),
                "detail": item.get("detail") or "",
                "metrics": metrics,
            }
        )
    return {
        "task_id": report.get("task_id") or "",
        "all_green": bool(report.get("all_green")),
        "results": results,
    }


def slim_event(event):
    """时间线用：只留能展示的标量，丢掉 raw_text / prompt 这类大块文本。"""
    out = {}
    moment = local_iso(parse_ts(event.get("ts")))
    if moment:
        out["ts"] = moment
    if event.get("event"):
        out["event"] = event["event"]
    for key in (
        "seat",
        "attempt",
        "phase",
        "interface",
        "protocol",
        "provider",
        "model",
        "latency_ms",
        "degraded",
        "finish_reason",
        "stop_reason",
        "claim_done",
        "calls",
        "rounds",
        "events",
        "outcome",
    ):
        if event.get(key) is not None:
            out[key] = event[key]
    usage = event.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("total"), int):
        out["tokens"] = usage["total"]
    elif isinstance(event.get("tokens"), int):
        out["tokens"] = event["tokens"]
    report = event.get("report")
    if isinstance(report, dict) and event.get("event") == "gate_report":
        results = report.get("results") or []
        out["all_green"] = bool(report.get("all_green"))
        out["gate_total"] = len(results)
        out["gate_failed"] = sum(1 for item in results if not item.get("ok"))
        if event.get("signature"):
            out["signature"] = event["signature"]
    return out


def build_run(events):
    events = sorted(events, key=lambda e: parse_ts(e.get("ts")) or datetime.min.replace(tzinfo=timezone.utc))
    starts = [e for e in events if e.get("event") == "run_start"]
    ends = [e for e in events if e.get("event") == "run_end"]
    gates = [e for e in events if e.get("event") == "gate_report" and isinstance(e.get("report"), dict)]
    runtimes = [e for e in events if e.get("event") == "runtime_run"]
    responses = [e for e in events if e.get("event") == "model_response"]

    start = starts[0] if starts else {}
    end = ends[-1] if ends else {}
    gate_event = gates[-1] if gates else {}
    runtime = runtimes[-1] if runtimes else {}

    start_task = start.get("task") if isinstance(start.get("task"), dict) else {}
    gate_report = gate_event.get("report") if isinstance(gate_event.get("report"), dict) else {}
    task_id = start.get("task_id") or start_task.get("task_id") or gate_report.get("task_id") or ""

    budget = end.get("budget") if isinstance(end.get("budget"), dict) else {}
    tokens = end.get("tokens")
    if tokens is None:
        tokens = budget.get("tokens")
    if tokens is None:
        used = [r.get("usage", {}).get("total") for r in responses if isinstance(r.get("usage"), dict)]
        tokens = sum(v for v in used if isinstance(v, int)) or None

    provider, model = start.get("provider"), start.get("model")
    if responses:
        provider = provider or responses[-1].get("provider")
        model = model or responses[-1].get("model")
    model = model or runtime.get("model")

    seats = start.get("seats") if isinstance(start.get("seats"), dict) else None
    protocol = start.get("protocol") or (seats or {}).get("protocol") or None

    stamps = [parse_ts(e.get("ts")) for e in events]
    stamps = [s for s in stamps if s is not None]
    started = min(stamps) if stamps else None
    ended = max(stamps) if stamps else None

    # 只有 run_start 与 run_end 都在时才算时长；否则宁可为 null，不编数字
    duration_ms = None
    if starts and ends and started and ended:
        duration_ms = int((ended - started).total_seconds() * 1000)

    return {
        "run_id": events[0].get("run_id") or "",
        "task_id": task_id,
        "started_at": local_iso(started),
        "ended_at": local_iso(ended),
        "duration_ms": duration_ms,
        "interface": start.get("interface"),
        "protocol": protocol,
        "backend": end.get("backend") or runtime.get("backend"),
        "provider": provider,
        "model": model,
        "seats": seats,
        "outcome": end.get("outcome"),
        "rounds": end.get("rounds"),
        "calls": end.get("calls") or runtime.get("calls"),
        "tokens": tokens,
        "gate_attempts": len(gates),
        "gate_signature": gate_event.get("signature"),
        "gate": slim_gate(gate_report) if gate_report else None,
        "events": len(events),
        "timeline": [slim_event(event) for event in events],
    }


def build_runs(events):
    grouped = {}
    for event in events:
        run_id = event.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue
        grouped.setdefault(run_id, []).append(event)
    runs = [build_run(group) for group in grouped.values()]
    runs.sort(key=lambda r: (r["started_at"] or "", r["run_id"]), reverse=True)
    return runs


def gate_stats(runs):
    stats = []
    for name in GATE_ORDER:
        evaluated = failed = 0
        for run in runs:
            gate = run.get("gate")
            if not gate:
                continue
            for item in gate["results"]:
                if item.get("name") != name:
                    continue
                evaluated += 1
                if not item.get("ok"):
                    failed += 1
        stats.append(
            {
                "name": name,
                "evaluated": evaluated,
                "failed": failed,
                "passed": evaluated - failed,
            }
        )
    return stats


def attach_task_runs(tasks, runs):
    for task in tasks:
        related = [r for r in runs if r.get("task_id") == task["id"]]
        outcomes = Counter(r.get("outcome") or "unknown" for r in related)
        task["runs"] = {
            "total": len(related),
            "accepted": outcomes.get("accepted", 0),
            "rejected": outcomes.get("rejected", 0),
            "unknown": outcomes.get("unknown", 0),
        }
        latest = related[0] if related else None
        task["last_run"] = (
            {
                "run_id": latest["run_id"],
                "started_at": latest["started_at"],
                "outcome": latest.get("outcome"),
            }
            if latest
            else None
        )


def build_payload(root: Path):
    tasks = load_tasks(root)
    events, total_lines, bad_lines = load_events(root)
    runs = build_runs(events)
    attach_task_runs(tasks, runs)

    outcomes = Counter(r.get("outcome") or "unknown" for r in runs)
    models = sorted({r["model"] for r in runs if r.get("model")})
    providers = sorted({r["provider"] for r in runs if r.get("provider")})
    tokens = sum(r["tokens"] for r in runs if isinstance(r.get("tokens"), int))
    latest = runs[0]["started_at"] if runs else None

    return {
        "schema": 1,
        "generated_from": {
            "tasks": [t["source"] for t in tasks],
            "audit": "runs/audit.jsonl",
            "audit_lines": total_lines,
            "audit_events": len(events),
            "audit_bad_lines": bad_lines,
            "latest_run_at": latest,
        },
        "summary": {
            "tasks": len(tasks),
            "runs": len(runs),
            "runs_with_gate": sum(1 for r in runs if r.get("gate")),
            "outcomes": {
                "accepted": outcomes.get("accepted", 0),
                "rejected": outcomes.get("rejected", 0),
                "unknown": outcomes.get("unknown", 0),
            },
            "tokens": tokens,
            "models": models,
            "providers": providers,
        },
        "gate_stats": gate_stats(runs),
        "tasks": tasks,
        "runs": runs,
    }


def write_outputs(root: Path, out_dir: Path, payload, dry_run=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    pretty = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    files = {
        out_dir / "site.json": pretty,
        out_dir / "site.js": "window.__DBW_DATA__ = " + compact + ";\n",
    }
    if dry_run:
        for path, text in files.items():
            print(f"[build-data] would write {path.relative_to(root)} ({len(text.encode('utf-8'))} bytes)")
        return files
    for path, text in files.items():
        path.write_bytes(text.encode("utf-8"))
        print(f"[build-data] wrote {path.relative_to(root)} ({len(text.encode('utf-8'))} bytes)")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成 web/data/site.json 与 site.js")
    parser.add_argument("--root", type=Path, default=ROOT, help="仓库根目录，默认取本文件上一级")
    parser.add_argument("--out", type=Path, default=None, help="输出目录，默认 <root>/web/data")
    parser.add_argument("--check", action="store_true", help="只报告会写什么，不落盘")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out_dir = (args.out or root / "web" / "data").resolve()
    payload = build_payload(root)

    summary = payload["summary"]
    print(
        "[build-data] tasks={tasks} runs={runs} runs_with_gate={gate_reports} "
        "events={events} bad_lines={bad}".format(
            tasks=summary["tasks"],
            runs=summary["runs"],
            gate_reports=summary["runs_with_gate"],
            events=payload["generated_from"]["audit_events"],
            bad=payload["generated_from"]["audit_bad_lines"],
        )
    )
    write_outputs(root, out_dir, payload, dry_run=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
