"""从 runs/audit.jsonl 导出逐次实验结果，并生成可随仓库交付的审计摘录。

用法：
  python scripts/export_runs.py
  python scripts/export_runs.py --audit runs/audit.jsonl --out experiments/附件

产物：
  experiments/附件/逐次结果.csv        每次运行一行（成功、失败、调用错误都保留）
  experiments/附件/audit-摘录.jsonl    只含被导出运行的审计事件（响应正文替换为摘要）

设计要点：
- 分组按 run_id 里内嵌的**本地时间**判定，不做 UTC 换算；
- 每组都有期望条数，数量不符时直接报错，避免导出后才发现分组错了；
- 缺失字段一律写「缺失」，未进入闸门的运行写「未进入闸门」，不伪装成全红。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_RE = re.compile(r"^[0-9a-f]{16}$")


def load_runs(audit_path: Path) -> dict[str, dict]:
    """按 run_id 聚合审计事件，同时记录每个事件的行号（1 基）。"""
    runs: dict[str, dict] = {}
    with audit_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            event = json.loads(line)
            run_id = event.get("run_id")
            if not run_id:
                continue
            record = runs.setdefault(
                run_id,
                {
                    "run_id": run_id,
                    "events": [],
                    "lines": [],
                    "calls": 0,
                    "tokens": 0,
                    "gate": None,
                    "outcome": "",
                    "reason": "",
                    "task_id": "",
                    "model": "",
                    "provider": "",
                    "protocol": "",
                    "backend": "",
                    "seats": {},
                    "contract_hash": "",
                    "ts": "",
                },
            )
            record["events"].append(event)
            record["lines"].append(number)

            kind = event["event"]
            if kind == "run_start":
                record["task_id"] = event.get("task_id") or record["task_id"]
                record["model"] = event.get("model") or record["model"]
                record["provider"] = event.get("provider") or record["provider"]
                record["protocol"] = event.get("protocol") or record["protocol"]
                record["backend"] = event.get("backend") or record["backend"]
                record["seats"] = event.get("seats") or record["seats"]
                record["contract_hash"] = event.get("contract_hash") or record["contract_hash"]
                record["ts"] = event.get("ts") or record["ts"]
            elif kind == "model_response":
                record["calls"] += 1
                record["tokens"] += (event.get("usage") or {}).get("total", 0)
                if not record["model"]:
                    record["model"] = event.get("model", "")
            elif kind == "gate_report":
                record["gate"] = event.get("report") or record["gate"]
            elif kind == "run_end":
                record["outcome"] = event.get("outcome", "")
                record["reason"] = event.get("reason", "")
                if event.get("tokens"):
                    record["tokens"] = event["tokens"]
    return runs


def classify_experiment(record: dict) -> tuple[str, str] | None:
    """返回 (实验编号, 组别)；不属于这三组返回 None。

    run_id 形如 20261127T160608-bb894b，前缀是**本地时间**，可直接比较。
    """
    run_id = record["run_id"]
    protocol = record["protocol"]

    if run_id.startswith("20261128") and protocol == "single-shot":
        return "11/28/1", "主实验（每任务 5 次）"

    if run_id.startswith("20261126") and protocol == "single-shot":
        stamp = run_id[:15]  # 20261126T145322
        if "20261126T1450" <= stamp < "20261126T1500":
            return "11/26/3", "首轮（每任务 3 次）"
        # 重测窗口里还混着别的手工验证运行，必须限定到 1285
        if (
            "20261126T1500" <= stamp < "20261126T1510"
            and record["task_id"] == "moreitertools-1285"
        ):
            return "11/26/3", "重测（1285，diff 上限 80→120）"
        return None

    if run_id.startswith("20261127"):
        if protocol == "single-shot":
            return "11/27/3", "单轮（无反馈）"
        if protocol == "3p.dual" and run_id >= "20261127T1600":
            seats = record["seats"] or {}
            same = seats.get("proposer") == seats.get("proposer_b")
            return "11/27/3", "同模型双提案" if same else "跨模型双提案"
    return None


def gate_cell(record: dict, name: str) -> str:
    report = record["gate"]
    if not report:
        return "未进入闸门"
    for item in report.get("results", []):
        if item.get("name") == name:
            return "通过" if item.get("ok") else "未通过"
    return "缺失"


def fail_reason(record: dict) -> str:
    """失败主因：优先看 run_end 的原因，其次看是哪道闸门没通过。"""
    outcome = record["outcome"]
    reason = record["reason"] or ""
    if outcome == "accepted":
        return "—"
    if "提案不合法" in reason or "输出不合法" in reason:
        return "提案不合法"
    if outcome == "error" or "模型调用失败" in reason:
        return "调用错误"
    if outcome == "budget_exhausted":
        return "预算耗尽"
    report = record["gate"]
    if not report:
        return "未进入闸门"
    failed = [i["name"] for i in report.get("results", []) if not i.get("ok")]
    if not failed:
        return "待核实"
    labels = {
        "tests": "测试未通过",
        "lint": "lint 新增问题",
        "api": "接口破坏",
        "diff": "改动超限或触碰禁改路径",
    }
    return "；".join(labels.get(name, name) for name in failed)


def contract_cell(record: dict) -> str:
    value = record["contract_hash"]
    if not value:
        return "缺失"
    return value if HASH_RE.match(str(value)) else "缺失（旧格式非哈希）"


def slim_event(event: dict) -> dict:
    """裁剪事件里的超大字段，保留结构信息。

    摘录要能随仓库交付，所以响应正文与整份任务都要去掉——
    但它们的存在与大小本身是有用信息，用摘要代替。
    """
    event = dict(event)
    raw = event.pop("raw_text", None)
    if raw is not None:
        event["raw_text"] = f"<已裁剪，{len(raw)} 字符>"
    task = event.pop("task", None)
    if isinstance(task, dict):
        event["task"] = {
            "keys": sorted(task.keys()),
            "current_file_chars": len(str(task.get("current_file", ""))),
            "failing_test_output_chars": len(str(task.get("failing_test_output", ""))),
        }
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="导出逐次实验结果为 CSV + 审计摘录")
    parser.add_argument("--audit", default=str(ROOT / "runs" / "audit.jsonl"))
    parser.add_argument("--out", default=str(ROOT / "experiments" / "附件"))
    args = parser.parse_args()

    audit_path = Path(args.audit)
    if not audit_path.is_file():
        raise SystemExit(f"找不到审计日志：{audit_path}")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = load_runs(audit_path)
    selected: list[tuple[dict, str, str]] = []
    for record in runs.values():
        hit = classify_experiment(record)
        if hit:
            selected.append((record, hit[0], hit[1]))

    # 分组计数与期望值核对——数量不符说明分组规则错了
    counts: dict[str, int] = defaultdict(int)
    for _, experiment, group in selected:
        counts[f"{experiment} | {group}"] += 1
    expected = {
        "11/28/1 | 主实验（每任务 5 次）": 100,
        "11/26/3 | 首轮（每任务 3 次）": 60,
        "11/26/3 | 重测（1285，diff 上限 80→120）": 3,
        "11/27/3 | 单轮（无反馈）": 12,
        "11/27/3 | 同模型双提案": 12,
        "11/27/3 | 跨模型双提案": 12,
    }
    problems = []
    for key, want in expected.items():
        got = counts.get(key, 0)
        if got != want:
            problems.append(f"{key}: 实际 {got}，期望 {want}")
    if problems:
        raise SystemExit("分组条数不符，未写出文件：\n  " + "\n  ".join(problems))

    # 按实验、任务、run_id 排序，保证可复现
    selected.sort(key=lambda item: (item[1], item[2], item[0]["task_id"], item[0]["run_id"]))

    rows = []
    excerpt_lines: list[str] = []
    for record, experiment, group in selected:
        start = len(excerpt_lines) + 1
        for event in record["events"]:
            excerpt_lines.append(json.dumps(slim_event(event), ensure_ascii=False))
        end = len(excerpt_lines)

        note = ""
        if record["task_id"] == "moreitertools-1285" and experiment == "11/26/3":
            note = (
                "首轮值，修订汇总中已被重测替换"
                if group.startswith("首轮")
                else "替换首轮记录；diff 上限由 80 放宽到 120"
            )

        rows.append(
            {
                "实验编号": experiment,
                "组别": group,
                "任务ID": record["task_id"] or "缺失",
                "日期": f"{record['run_id'][:4]}-{record['run_id'][4:6]}-{record['run_id'][6:8]}",
                "run_id": record["run_id"],
                "供应商": record["provider"] or "缺失",
                "模型": record["model"] or "缺失",
                "协议": record["protocol"] or "缺失",
                "契约哈希": contract_cell(record),
                "模型调用数": record["calls"],
                "tests": gate_cell(record, "tests"),
                "lint": gate_cell(record, "lint"),
                "api": gate_cell(record, "api"),
                "diff": gate_cell(record, "diff"),
                "闸门结论": "全绿" if record["gate"] and record["gate"].get("all_green") else ("未通过" if record["gate"] else "未进入闸门"),
                "最终状态": record["outcome"] or "缺失",
                "失败主因": fail_reason(record),
                "token": record["tokens"],
                "审计位置": f"audit-摘录.jsonl:L{start}-L{end}",
                "备注": note,
            }
        )

    csv_path = out_dir / "逐次结果.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    excerpt_path = out_dir / "audit-摘录.jsonl"
    excerpt_path.write_bytes(("\n".join(excerpt_lines) + "\n").encode("utf-8"))

    accepted = sum(1 for r in rows if r["最终状态"] == "accepted")
    print(f"已写出 {csv_path.relative_to(ROOT)}：{len(rows)} 行（通过 {accepted}，未通过 {len(rows) - accepted}）")
    print(f"已写出 {excerpt_path.relative_to(ROOT)}：{len(excerpt_lines)} 条事件")
    print("\n分组统计：")
    for key in expected:
        passed = sum(1 for r in rows if f"{r['实验编号']} | {r['组别']}" == key and r["最终状态"] == "accepted")
        print(f"  {key:<46} {passed}/{expected[key]} 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
