"""执行后端的测试：循环语义、命令解析、快照差异、预算与终止。

全部离线：模型用脚本化的假适配器，命令只跑我们自己的 echo 与 python。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbw.contract import load_contract  # noqa: E402
from dbw.protocol import Capabilities, ModelResponse, TokenUsage  # noqa: E402
from dbw.protocol.envelope import Budget  # noqa: E402
from dbw.runtime import (  # noqa: E402
    STOP_BUDGET,
    STOP_FORMAT_ERROR,
    STOP_STEP_LIMIT,
    STOP_SUBMITTED,
    SUBMIT_SENTINEL,
    LocalEnvironment,
    MiniSweRuntime,
    extract_command,
    parse_action,
    render_contract_digest,
)
from dbw.runtime.workspace import diff, snapshot  # noqa: E402

CONTRACT = load_contract(ROOT / "fixtures" / "repo_a" / "contract.yaml")

SUBMIT = f"```bash\necho {SUBMIT_SENTINEL}\n```"


class ScriptedModel:
    provider = "scripted"

    def __init__(self, outputs: list[str], model: str = "scripted") -> None:
        self.model = model
        self.outputs = list(outputs)
        self.calls: list = []
        self.fallback = SUBMIT

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def complete(self, request) -> ModelResponse:
        self.calls.append(request)
        text = self.outputs.pop(0) if self.outputs else self.fallback
        return ModelResponse(
            model=self.model,
            text=text,
            usage=TokenUsage(prompt=100, completion=20),
            request_digest=request.digest(),
        )


# ---------------------------------------------------------------- 命令解析

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("```bash\nls -la\n```", "ls -la"),
        ("```\npytest -q\n```", "pytest -q"),
        ("```sh\necho hi\n```", "echo hi"),
        ("pytest -q", "pytest -q"),
        ("我来看看仓库结构。\n\n```bash\nls\n```", "ls"),
        ("我不确定", None),
        ("我不确定该做什么？", None),
        ("先做 A 再做 B\n然后做 C", None),
        ("", None),
    ],
)
def test_extract_command(text, expected):
    assert extract_command(text) == expected


# ---------------------------------------------------------------- 循环语义

def test_submit_stops_the_loop(tmp_path: Path):
    model = ScriptedModel(["```bash\necho hello\n```", SUBMIT])
    runtime = MiniSweRuntime(model=model)
    result = runtime.run(workspace=tmp_path, task="随便看看", budget=Budget(max_calls=5))

    assert result.stop_reason == STOP_SUBMITTED
    assert result.claim_done is True
    assert result.calls == 2
    assert len(result.transcript) == 2
    assert result.transcript[0]["returncode"] == 0


def test_step_limit_terminates(tmp_path: Path):
    model = ScriptedModel(["```bash\necho tick\n```"] * 10)
    runtime = MiniSweRuntime(model=model, step_limit=2)
    result = runtime.run(workspace=tmp_path, task="永远别提交", budget=Budget(max_calls=20))

    assert result.stop_reason == STOP_STEP_LIMIT
    assert result.calls == 2
    assert result.claim_done is False


def test_repeated_format_errors_terminate(tmp_path: Path):
    model = ScriptedModel(["我不确定", "还是没有代码块", "第三次"])  # 连续三次都不合规
    runtime = MiniSweRuntime(model=model, max_format_errors=3)
    result = runtime.run(workspace=tmp_path, task="不给代码块", budget=Budget(max_calls=10))

    assert result.stop_reason == STOP_FORMAT_ERROR
    assert result.claim_done is False


def test_budget_exhaustion_is_a_normal_exit(tmp_path: Path):
    model = ScriptedModel(["```bash\necho tick\n```"] * 10)
    runtime = MiniSweRuntime(model=model)
    result = runtime.run(workspace=tmp_path, task="跑不完", budget=Budget(max_calls=1))

    assert result.stop_reason == STOP_BUDGET
    assert result.calls == 1


# ---------------------------------------------------------------- 产物归一化

def test_patch_captures_created_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    model = ScriptedModel(
        [
            "```bash\necho changed > a.txt\n```",
            "```bash\necho new > b.txt\n```",
            SUBMIT,
        ]
    )
    result = MiniSweRuntime(model=model).run(workspace=tmp_path, task="改两个文件", budget=Budget(max_calls=5))

    assert set(result.patch) == {"a.txt", "b.txt"}
    assert "changed" in result.patch["a.txt"]
    assert "new" in result.patch["b.txt"]
    assert result.claim_done is True


def test_patch_captures_write_action(tmp_path: Path):
    """write 块是跨平台改文件的方式：Windows 下 heredoc 不可用。"""
    model = ScriptedModel(
        [
            "```write:a.py\ndef f():\n    return 1\n```",
            SUBMIT,
        ]
    )
    result = MiniSweRuntime(model=model).run(workspace=tmp_path, task="写一个文件", budget=Budget(max_calls=4))

    assert result.patch.get("a.py") == "def f():\n    return 1\n"
    assert (tmp_path / "a.py").read_text(encoding="utf-8").startswith("def f()")


def test_write_outside_workspace_is_refused(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    model = ScriptedModel(["```write:../escape.txt\nnope\n```", SUBMIT])
    result = MiniSweRuntime(model=model).run(workspace=workspace, task="越界写", budget=Budget(max_calls=4))

    assert result.patch == {}
    assert not (tmp_path / "escape.txt").exists()


def test_parse_action_distinguishes_kinds():
    write = parse_action("```write:pkg/m.py\nx = 1\n```")
    assert write is not None and write.kind == "write" and write.target == "pkg/m.py"
    bash = parse_action("```bash\npytest -q\n```")
    assert bash is not None and bash.kind == "bash" and bash.body == "pytest -q"
    assert parse_action("就是一段中文说明") is None


def test_patch_marks_deleted_file_as_empty(tmp_path: Path):
    target = tmp_path / "gone.txt"
    target.write_text("bye", encoding="utf-8")
    model = ScriptedModel([f'```bash\n{sys.executable} -c "from pathlib import Path; Path(\'gone.txt\').unlink()"\n```', SUBMIT])
    result = MiniSweRuntime(model=model).run(workspace=tmp_path, task="删掉一个文件", budget=Budget(max_calls=5))

    assert result.patch.get("gone.txt") == ""


def test_snapshot_diff_ignores_unchanged(tmp_path: Path):
    (tmp_path / "same.txt").write_text("x", encoding="utf-8")
    before = snapshot(tmp_path)
    assert diff(before, snapshot(tmp_path)) == {}


# ---------------------------------------------------------------- 环境

def test_command_timeout_is_reported(tmp_path: Path):
    env = LocalEnvironment(tmp_path, timeout_s=1.0)
    command = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    observation = env.execute(command)

    assert observation.timed_out is True
    assert observation.returncode != 0


def test_empty_command_is_rejected(tmp_path: Path):
    observation = LocalEnvironment(tmp_path).execute("   ")
    assert observation.returncode == 1


# ---------------------------------------------------------------- 契约摘要

def test_contract_digest_carries_hard_constraints():
    digest = render_contract_digest(CONTRACT)
    assert "missing '='" in digest, "精确断言必须进摘要，否则执行循环不知道要逐字匹配"
    assert "tests/" in digest, "禁止路径必须进摘要"
    assert "pytest" in digest


def test_runtime_injects_digest_into_first_prompt(tmp_path: Path):
    model = ScriptedModel([SUBMIT])
    MiniSweRuntime(model=model).run_contract(workspace=tmp_path, task="修个 bug", contract=CONTRACT, budget=Budget(max_calls=2))

    first_user_message = model.calls[0].messages[1].content
    assert "missing '='" in first_user_message
    assert "tests/" in model.calls[0].messages[0].content
