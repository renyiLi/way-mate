"""生成 experiments/summary.json：给前端与论文共用的实验数据快照。

任务是唯一权威来源（tasks/*/task.json 的 measured 字段），
协议与模型对照的数字来自 experiments/ 下的实验记录，这里只做汇总，不重新计算。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments" / "summary.json"

# 协议阶梯：来自 experiments/11-27-1、11-27-2、11-26-3
PROTOCOL_LADDER = [
    {
        "mode": "single-turn",
        "label": "单轮（一次调用，无反馈）",
        "passed": 3,
        "total": 4,
        "tokens": "~35000",
        "source": "11/26/3",
    },
    {
        "mode": "3p.single",
        "label": "单提案 + 复核 + 反馈轮",
        "passed": 3,
        "total": 4,
        "tokens": "14000-59000",
        "source": "11/27/2",
    },
    {
        "mode": "3p.dual-same-model",
        "label": "同模型双提案",
        "passed": 4,
        "total": 4,
        "tokens": "150000-290000",
        "source": "11/27/1",
    },
    {
        "mode": "3p.dual-cross-model",
        "label": "跨模型双提案",
        "passed": 3,
        "total": 4,
        "tokens": "160000-290000",
        "source": "11/27/1",
    },
]

PROTOCOL_TASKS = ["moreitertools-1270", "moreitertools-1285", "moreitertools-1305", "moreitertools-1342"]


def main() -> int:
    tasks = []
    for path in sorted((ROOT / "tasks").glob("*/task.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        measured = data.get("measured") or {}
        tasks.append(
            {
                "id": data["id"],
                "title": data["title"],
                "tier": data["tier"],
                "repo": data["repo"].rstrip("/").split("/")[-1],
                "targets": data.get("target_test") if isinstance(data.get("target_test"), list) else [data.get("target_test")],
                "measured": {
                    "model": measured.get("model"),
                    "passed": measured.get("passed"),
                    "reps": measured.get("reps"),
                    "date": measured.get("date"),
                },
            }
        )

    tiers = Counter(item["tier"] for item in tasks)
    passed_total = sum(item["measured"]["passed"] or 0 for item in tasks)
    reps_total = sum(item["measured"]["reps"] or 0 for item in tasks)

    summary = {
        "generated_from": "tasks/*/task.json",
        "task_count": len(tasks),
        "tier_counts": dict(tiers),
        "baseline": {
            "model": "deepseek-chat",
            "passed": passed_total,
            "reps": reps_total,
            "source": "11/26/3",
        },
        "protocol_ladder": {
            "tasks": PROTOCOL_TASKS,
            "rows": PROTOCOL_LADDER,
            "note": "四条任务各自只跑 1 次，差一条即 25%，不可当通过率引用",
        },
        "negative_results": [
            "多角色协作未观察到增益：四条任务上单轮/3p.single/跨模型双提案都是 3/4",
            "同模型双提案到 4/4，但 n=1 且成本高 5-10 倍，不足以支撑结论",
            "复核座位每轮 5-8 条结论全部被丢弃，实际只起触发下一轮的作用",
            "裁决存在首位偏置：6 次双提案中 5 次选中第一份",
            "难度不是任务的绝对属性：同一任务换模型或换上下文策略结果会变",
        ],
        "tasks": tasks,
    }
    OUT.write_bytes(json.dumps(summary, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    print(f"写入 {OUT.relative_to(ROOT)}：{len(tasks)} 条任务，基线 {passed_total}/{reps_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
