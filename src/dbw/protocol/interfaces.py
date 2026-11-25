"""三个分内接口：写作 / 审计 / 协作。

接口的定义不是"流程"，而是**输出合法性约束（不变量）**：
不变量不满足就丢弃该输出并记一次违规——不许"大概对"。

流程（2 人 / 3 人怎么商量）统一由 machine.py 的协议机实现，
接口层只负责三件事：描述输出模式、校验输出、把交付物渲染成复核用的文本。

三个接口在同一台协议机上的分工：
- 写作：提案 = 补丁，复核 = 找契约违反，闸门 = 代码闸门
- 协作：提案 = 任务图，复核 = 找分解缺陷，闸门 = 图合法性闸门
- 审计：提案 = 候选问题清单，复核 = 逐条验真，闸门 = 验真后的结论闸门
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..contract import Contract
from ..exact import render_exact_block
from ..edit_format import Edit, compute_edits
from ..gates import GateReport, GateResult
from .base import PayloadError

SEVERITIES = ("BLOCKER", "MAJOR", "MINOR")
GATE_NAMES = ("tests", "lint", "api", "diff")


class InvalidOutput(PayloadError):
    """输出结构合法但违反接口不变量。此类输出必须被丢弃，不得进闸门。"""


# ---------------------------------------------------------------- 共享输出模式

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fid": {"type": "string"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "gate": {"type": "string"},
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                    "remedy": {"type": "string"},
                },
                "required": ["fid", "severity", "gate", "claim", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

VERDICTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fid": {"type": "string"},
                    "decision": {"type": "string", "enum": ["upheld", "rejected", "insufficient"]},
                    "reason": {"type": "string"},
                },
                "required": ["fid", "decision", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

CHOICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "choice": {"type": "integer", "minimum": 0},
        "reason": {"type": "string"},
    },
    "required": ["choice", "reason"],
    "additionalProperties": False,
}


CRITIQUE_RULES = """你处于「复核」座位，只能做一件事：指出交付物对验收契约的**具体违反**。

硬规则：
- 每条结论必须给出 gate（只能是 tests / lint / api / diff 之一）和 evidence（引用契约条目或具体位置）。
- 只准评判"是否满足契约"，不准给风格建议、不准评价架构、不准说"建议优化"。
- 没有证据支撑的结论会被直接丢弃，不计入结果。
- 找不到违反就输出 {"findings": []}，不要为了显得有用而编造问题。
- 只输出 JSON，不要任何解释性文字。"""

ARBITER_RULES = """你处于「裁决」座位。你**不是**第二个裁判，只裁一件事：
复核座位提出的每条结论，是否成立。

- upheld：结论成立，且有契约依据；
- rejected：结论不成立，或把风格问题冒充成契约问题；
- insufficient：证据不足以判断，按不成立处理，但需说明缺什么证据。

你不得新增结论，不得评价代码质量，不得修改提案。
最终交付判定由外部闸门执行，不由你决定。只输出 JSON。"""


# ---------------------------------------------------------------- 交付物

@dataclass(frozen=True)
class Patch:
    files: dict[str, str]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {"files": dict(self.files), "rationale": self.rationale}

    def preview(self, per_file_chars: int = 1200) -> str:
        chunks = []
        for path, content in self.files.items():
            body = content if len(content) <= per_file_chars else content[:per_file_chars] + "\n…（截断）"
            chunks.append(f"--- {path}\n{body}")
        return "\n".join(chunks)


@dataclass(frozen=True)
class Finding:
    fid: str
    severity: str
    gate: str
    claim: str
    evidence: str
    remedy: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fid": self.fid,
            "severity": self.severity,
            "gate": self.gate,
            "claim": self.claim,
            "evidence": self.evidence,
            "remedy": self.remedy,
        }


@dataclass(frozen=True)
class ReviewOutcome:
    """复核的统一产物：有效结论、阻断项、被丢弃的无效结论。"""

    items: tuple[Finding, ...] = ()
    blockers: tuple[Finding, ...] = ()
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskNode:
    id: str
    goal: str
    dod: tuple[str, ...]
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "goal": self.goal, "dod": list(self.dod), "depends_on": list(self.depends_on)}


@dataclass(frozen=True)
class TaskDAG:
    nodes: tuple[TaskNode, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": [n.to_dict() for n in self.nodes]}

    @property
    def order(self) -> tuple[str, ...]:
        pending = {n.id: set(n.depends_on) for n in self.nodes}
        ordered: list[str] = []
        while pending:
            ready = sorted(nid for nid, deps in pending.items() if not deps)
            if not ready:
                break
            for nid in ready:
                ordered.append(nid)
                pending.pop(nid)
            for deps in pending.values():
                deps.difference_update(ready)
        return tuple(ordered)


# ---------------------------------------------------------------- 结论解析（写作与协作共用）

def parse_findings(payload: Any) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
    if not isinstance(payload, dict):
        raise InvalidOutput("复核结果必须是 JSON 对象")
    raw = payload.get("findings")
    if raw is None:
        raise InvalidOutput("缺少 findings 字段")
    if not isinstance(raw, list):
        raise InvalidOutput("findings 必须是数组")

    kept: list[Finding] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            dropped.append(f"第 {index} 条不是对象")
            continue
        fid = str(item.get("fid") or f"F{index + 1}")
        if fid in seen:
            dropped.append(f"{fid} 编号重复")
            continue
        severity = str(item.get("severity", "")).upper()
        gate = str(item.get("gate", "")).strip()
        claim = str(item.get("claim", "")).strip()
        evidence = str(item.get("evidence", "")).strip()

        if severity not in SEVERITIES:
            dropped.append(f"{fid} severity 非法：{severity!r}")
            continue
        if gate not in GATE_NAMES:
            dropped.append(f"{fid} gate 必须属于 {GATE_NAMES}，实际 {gate!r}")
            continue
        if not claim:
            dropped.append(f"{fid} 没有结论")
            continue
        if not evidence:
            dropped.append(f"{fid} 没有证据（无证据的结论一律丢弃）")
            continue

        seen.add(fid)
        kept.append(
            Finding(
                fid=fid,
                severity=severity,
                gate=gate,
                claim=claim,
                evidence=evidence,
                remedy=str(item.get("remedy", "")).strip(),
            )
        )

    return tuple(kept), tuple(dropped)


def to_outcome(findings: tuple[Finding, ...], dropped: tuple[str, ...]) -> ReviewOutcome:
    return ReviewOutcome(
        items=findings,
        blockers=tuple(f for f in findings if f.severity == "BLOCKER"),
        dropped=dropped,
    )


def apply_verdicts(findings: tuple[Finding, ...], payload: Any) -> tuple[tuple[Finding, ...], tuple[str, ...]]:
    """按裁决结果过滤结论。只保留 upheld，其余视为不成立。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        raise InvalidOutput('裁决结果必须形如 {"verdicts": [...]}')

    decisions: dict[str, str] = {}
    for item in payload["verdicts"]:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("fid", ""))
        decision = str(item.get("decision", ""))
        if decision in {"upheld", "rejected", "insufficient"}:
            decisions[fid] = decision

    upheld: list[Finding] = []
    rejected: list[str] = []
    for finding in findings:
        decision = decisions.get(finding.fid)
        if decision == "upheld":
            upheld.append(finding)
        else:
            rejected.append(f"{finding.fid}（{decision or '未裁决→视为不成立'}）")
    return tuple(upheld), tuple(rejected)


# ---------------------------------------------------------------- 写作接口

def _patch_from_edits(payload_edits: Any, payload: dict[str, Any], contract: Contract) -> Patch:
    """把 SEARCH/REPLACE 编辑合成为完整文件内容。

    Patch 仍然是"整份文件内容"，所以闸门、复核、审计都不用改——
    变的只是模型怎么表达改动。
    """
    if not isinstance(payload_edits, list) or not payload_edits:
        raise InvalidOutput("edits 必须是非空数组")

    edits: list[Edit] = []
    for index, item in enumerate(payload_edits):
        if not isinstance(item, dict):
            raise InvalidOutput(f"edits[{index}] 必须是对象")
        path = str(item.get("path", "")).strip()
        search = item.get("search")
        replace = item.get("replace")
        if not path or not isinstance(search, str) or not isinstance(replace, str):
            raise InvalidOutput(f"edits[{index}] 需要 path / search / replace 三个字符串")
        if path.startswith("/") or ".." in path.split("/"):
            raise InvalidOutput(f"只允许仓库内相对路径：{path!r}")
        for rule in contract.diff.forbid:
            if rule in path:
                raise InvalidOutput(f"提案触碰禁止修改的路径 {path}（规则 {rule}）")
        edits.append(Edit(path=path, search=search, replace=replace))

    result = compute_edits(contract.repo, edits)
    if result.failures:
        raise InvalidOutput("；".join(result.failures))
    if len(result.changed) > contract.diff.max_files:
        raise InvalidOutput(f"改动 {len(result.changed)} 个文件，超过契约上限 {contract.diff.max_files}")

    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise InvalidOutput("rationale 不能为空：说不出为什么改，就不该改")
    return Patch(files=dict(result.changed), rationale=rationale.strip())


def render_propose_prompt(task: dict[str, Any], contract: Contract) -> str:
    """提案提示词的**唯一**构造点。

    单轮后端（delivery）与协议机（machine）都调它。
    两边各自拼提示词会导致“单轮 vs 2 人协议”的对比混入提示词变量，实验直接失效。
    """
    task_block = json.dumps(task, ensure_ascii=False, indent=2)
    dod_block = json.dumps(contract.to_dict()["doD"], ensure_ascii=False, indent=2)
    return f"任务：\n{task_block}\n\n验收契约：\n{dod_block}\n\n请给出你的产出。"


class AuthoringInterface:
    """写作接口：只产出补丁，不产出散文。"""

    name = "authoring"

    def system_prompt(self, contract: Contract) -> str:
        block = render_exact_block(contract.exact)
        base = (
            "你是一名实现工程师，目标是用最小改动让契约通过。\n"
            "你只输出一个 JSON 对象，形如 "
            '{"edits": [{"path": "相对路径", "search": "文件里逐字存在的原文片段", "replace": "替换后的内容"}], "rationale": "为什么这样改"}。\n'
            "规则：\n"
            "- 改已有文件用 edits，只给要替换的那几行，不要重写整份文件；\n"
            "- search 片段必须在文件里唯一出现，且尽量短；\n"
            "- 只有新建文件时才用 files（整份内容）；\n"
            f"- 绝对不要修改这些路径：{', '.join(contract.diff.forbid) or '（无限制）'}；\n"
            f"- 改动预算：最多 {contract.diff.max_files} 个文件 / {contract.diff.max_lines} 行；\n"
            "- 不确定时选择更小的改动；\n"
            "- 只输出 JSON。"
        )
        return f"{base}\n\n{block}" if block else base

    def propose_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "files": {"type": "object", "additionalProperties": {"type": "string"}},
                "edits": {
                    "type": "array",
                    "description": (
                        "推荐：用 SEARCH/REPLACE 片段描述改动，不要重写整份文件。"
                        "每项 {path, search, replace}；search 必须是文件里逐字存在且唯一的一小段，"
                        "replace 是替换后的内容。修改已有文件时优先用它。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "search": {"type": "string"},
                            "replace": {"type": "string"},
                        },
                        "required": ["path", "search", "replace"],
                        "additionalProperties": False,
                    },
                },
                "rationale": {"type": "string"},
            },
            "required": ["rationale"],
            "additionalProperties": False,
        }

    def parse_proposal(self, payload: Any, contract: Contract) -> Patch:
        if not isinstance(payload, dict):
            raise InvalidOutput("提案必须是 JSON 对象")
        if payload.get("edits") is not None:
            return _patch_from_edits(payload["edits"], payload, contract)
        files = payload.get("files")
        rationale = payload.get("rationale")
        if not isinstance(files, dict) or not files:
            raise InvalidOutput("files 必须是非空对象")
        if not isinstance(rationale, str) or not rationale.strip():
            raise InvalidOutput("rationale 不能为空：说不出为什么改，就不该改")

        clean: dict[str, str] = {}
        for path, content in files.items():
            if not isinstance(path, str) or not isinstance(content, str):
                raise InvalidOutput(f"files 的键值必须是字符串：{path!r}")
            normalized = str(path).replace("\\", "/").lstrip("./")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise InvalidOutput(f"只允许仓库内相对路径：{path!r}")
            for rule in contract.diff.forbid:
                if rule in normalized:
                    raise InvalidOutput(f"提案触碰禁止修改的路径 {normalized}（规则 {rule}）")
            clean[normalized] = content

        if len(clean) > contract.diff.max_files:
            raise InvalidOutput(f"改动 {len(clean)} 个文件，超过契约上限 {contract.diff.max_files}")
        return Patch(files=clean, rationale=rationale.strip())

    def critique_context(self, deliverable: Patch) -> str:
        return f"提案理由：{deliverable.rationale}\n\n文件内容：\n{deliverable.preview()}"

    def review_schema(self) -> dict[str, Any]:
        return FINDINGS_SCHEMA

    def parse_review(self, payload: Any, contract: Contract, deliverable: Patch) -> ReviewOutcome:
        return to_outcome(*parse_findings(payload))


# ---------------------------------------------------------------- 审计接口

class ReviewInterface:
    """审计接口：只产出可判定的违例，不产出意见。

    作为工作负载使用时：提案 = 候选问题清单，复核 = 逐条验真。
    这样"审计"本身也是多模型协作，防的是模型编造问题。
    """

    name = "review"

    def system_prompt(self, contract: Contract) -> str:
        contract_dump = json.dumps(contract.to_dict()["doD"], ensure_ascii=False)
        block = render_exact_block(contract.exact)
        head = f"你是一名验收审计员，只按契约办事。\n当前契约的验收条件：{contract_dump}"
        return f"{head}\n\n{block}\n\n{CRITIQUE_RULES}" if block else f"{head}\n\n{CRITIQUE_RULES}"

    def propose_schema(self) -> dict[str, Any]:
        return FINDINGS_SCHEMA

    def parse_proposal(self, payload: Any, contract: Contract) -> tuple[Finding, ...]:
        findings, _dropped = parse_findings(payload)
        return findings

    def critique_context(self, deliverable: tuple[Finding, ...]) -> str:
        return json.dumps([f.to_dict() for f in deliverable], ensure_ascii=False, indent=2)

    def review_schema(self) -> dict[str, Any]:
        return VERDICTS_SCHEMA

    def parse_review(
        self, payload: Any, contract: Contract, deliverable: tuple[Finding, ...]
    ) -> ReviewOutcome:
        upheld, rejected = apply_verdicts(deliverable, payload)
        return to_outcome(upheld, rejected)


# ---------------------------------------------------------------- 协作接口

class CollabInterface:
    """协作接口：只做分解与依赖排序，不做实现。"""

    name = "collab"

    def system_prompt(self, contract: Contract) -> str:
        return (
            "你是一名交付规划师。你的任务是把任务拆成可独立验收的子任务图。\n"
            '输出形如 {"nodes": [{"id": "T1", "goal": "...", "dod": ["可执行验收命令"], "depends_on": ["T0"]}]}。\n'
            "规则：\n"
            "- 每个节点至少一条 dod，且必须是可执行命令（如 pytest tests/x.py::test_y）；\n"
            "- depends_on 只能引用本图中的节点；\n"
            "- 图必须是 DAG，不允许环；\n"
            f"- 节点数不超过 {max(3, contract.diff.max_files)} 个，宁可少而准；\n"
            "- 不要写实现代码。只输出 JSON。"
        )

    def propose_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "goal": {"type": "string"},
                            "dod": {"type": "array", "items": {"type": "string"}},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "goal", "dod"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["nodes"],
            "additionalProperties": False,
        }

    def parse_proposal(self, payload: Any, contract: Contract) -> TaskDAG:
        if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
            raise InvalidOutput('分解结果必须形如 {"nodes": [...]}')
        if not payload["nodes"]:
            raise InvalidOutput("nodes 不能为空")

        nodes: list[TaskNode] = []
        ids: set[str] = set()
        for index, item in enumerate(payload["nodes"]):
            if not isinstance(item, dict):
                raise InvalidOutput(f"第 {index} 个节点不是对象")
            nid = str(item.get("id", "")).strip()
            goal = str(item.get("goal", "")).strip()
            if not nid:
                raise InvalidOutput(f"第 {index} 个节点缺少 id")
            if nid in ids:
                raise InvalidOutput(f"节点 id 重复：{nid}")
            if not goal:
                raise InvalidOutput(f"节点 {nid} 缺少 goal")
            dod_raw = item.get("dod")
            if not isinstance(dod_raw, list) or not dod_raw:
                raise InvalidOutput(f"节点 {nid} 缺少验收命令（dod）")
            dod = tuple(str(d).strip() for d in dod_raw if str(d).strip())
            if not dod:
                raise InvalidOutput(f"节点 {nid} 的 dod 全为空")
            deps_raw = item.get("depends_on") or []
            if not isinstance(deps_raw, list):
                raise InvalidOutput(f"节点 {nid} 的 depends_on 必须是数组")
            deps = tuple(str(d).strip() for d in deps_raw if str(d).strip())
            ids.add(nid)
            nodes.append(TaskNode(id=nid, goal=goal, dod=dod, depends_on=deps))

        known = {n.id for n in nodes}
        for node in nodes:
            unknown = [d for d in node.depends_on if d not in known]
            if unknown:
                raise InvalidOutput(f"节点 {node.id} 依赖了不存在的节点：{unknown}")
        return TaskDAG(nodes=tuple(nodes))

    def critique_context(self, deliverable: TaskDAG) -> str:
        return json.dumps(deliverable.to_dict(), ensure_ascii=False, indent=2)

    def review_schema(self) -> dict[str, Any]:
        return FINDINGS_SCHEMA

    def parse_review(self, payload: Any, contract: Contract, deliverable: TaskDAG) -> ReviewOutcome:
        return to_outcome(*parse_findings(payload))


# ---------------------------------------------------------------- 分解也要过闸门

def dag_gate(dag: TaskDAG) -> GateReport:
    """把"分解方案"本身也交给闸门判定，与代码交付共用同一套机制。"""
    ids = {n.id for n in dag.nodes}
    dangling = [f"{n.id}->{d}" for n in dag.nodes for d in n.depends_on if d not in ids]
    no_dod = [n.id for n in dag.nodes if not n.dod]
    ordered = dag.order
    cyclic = len(ordered) != len(dag.nodes)

    results = [
        GateResult("tests", not no_dod, f"每个节点都有验收命令（缺失 {len(no_dod)} 个）", {"missing": no_dod}),
        GateResult("api", not dangling, f"依赖都指向已定义节点（悬空 {len(dangling)} 条）", {"dangling": dangling}),
        GateResult(
            "diff",
            not cyclic,
            "任务图无环" if not cyclic else f"检测到环，只能排出 {len(ordered)}/{len(dag.nodes)} 个节点",
            {},
        ),
    ]
    return GateReport(task_id="collab.dag", results=results)


INTERFACES: dict[str, Any] = {
    "authoring": AuthoringInterface(),
    "review": ReviewInterface(),
    "collab": CollabInterface(),
}


def get_interface(name: str) -> Any:
    if name not in INTERFACES:
        raise KeyError(f"未知接口：{name}（可用：{', '.join(sorted(INTERFACES))}）")
    return INTERFACES[name]
