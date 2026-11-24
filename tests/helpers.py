"""测试共用的小工具：脚本化适配器、注册表、假闸门。

不放 pytest fixture，只放可被 import 的构造函数，避免测试之间隐式耦合。
"""

from __future__ import annotations

import json

from dbw.gates import GateReport, GateResult
from dbw.protocol import Capabilities, ModelResponse, ProviderRegistry, TokenUsage


class ScriptedAdapter:
    """按剧本返回响应的假供应商。剧本用完后重复最后一条。"""

    def __init__(self, provider: str, script: list, model: str = "scripted") -> None:
        self.provider = provider
        self.model = model
        self.script = list(script)
        self.calls: list = []

    def capabilities(self) -> Capabilities:
        return Capabilities(supports_json_schema=True)

    def complete(self, request) -> ModelResponse:
        self.calls.append(request)
        if not self.script:
            raise AssertionError(f"{self.provider} 的剧本已用尽")
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return ModelResponse(
            model=self.model,
            text=text,
            usage=TokenUsage(prompt=100, completion=50),
            request_digest=request.digest(),
        )


def registry(**adapters: ScriptedAdapter) -> ProviderRegistry:
    reg = ProviderRegistry()
    for name, adapter in adapters.items():
        reg.register(name, adapter)
    return reg


def gate(ok: bool) -> GateReport:
    return GateReport(task_id="t", results=[GateResult("tests", ok, "通过" if ok else "红")])
