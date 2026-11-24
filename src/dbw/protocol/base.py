"""P0 底层协议：与具体供应商无关的模型调用契约。

这一层只做四件事：
1. 能力协商（这家支持 tools / json_schema / seed 吗）；
2. 请求与响应的统一形状；
3. 错误分类（可重试 / 不可重试 / 能力不足 / 预算耗尽）；
4. 降级必须被登记——否则三家模型的对比数据就是假的。

不在这里做：编排、重试策略的业务含义、任何提示词。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol as TypingProtocol

from .envelope import TokenUsage


# ---------------------------------------------------------------- 错误分类

class AdapterError(Exception):
    """所有适配器异常的基类。retryable 决定编排层是否重试。"""

    retryable: bool = False

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TransientError(AdapterError):
    """限流、超时、5xx。可重试。"""

    retryable = True


class PermanentError(AdapterError):
    """参数错误、鉴权失败、模型不存在。重试无意义。"""

    retryable = False


class CapabilityError(AdapterError):
    """供应商不支持请求所需能力，且没有安全的降级方案。"""

    retryable = False


# ---------------------------------------------------------------- 能力协商

@dataclass(frozen=True)
class Capabilities:
    supports_tools: bool = False
    supports_json_schema: bool = False
    supports_json_object: bool = True
    supports_seed: bool = False
    supports_stream: bool = False
    max_context: int = 32_000
    fixed_temperature: float | None = None

    def check(self, *, tools: bool, json_schema: bool, seed: bool) -> list[str]:
        """返回不可满足的能力项。空列表表示完全满足。"""
        missing: list[str] = []
        if tools and not self.supports_tools:
            missing.append("tools")
        if json_schema and not self.supports_json_schema and not self.supports_json_object:
            missing.append("json_schema")
        if seed and not self.supports_seed:
            missing.append("seed")
        return missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "supports_tools": self.supports_tools,
            "supports_json_schema": self.supports_json_schema,
            "supports_json_object": self.supports_json_object,
            "supports_seed": self.supports_seed,
            "supports_stream": self.supports_stream,
            "max_context": self.max_context,
            "fixed_temperature": self.fixed_temperature,
        }


# 供应商标记的默认能力，可覆盖。下列数值均已实测（见 runs/capabilities.*.json），
# 不得沿用厂商宣传：DeepSeek 实测不支持 json_schema。
KNOWN_CAPABILITIES: dict[str, Capabilities] = {
    "deepseek": Capabilities(
        supports_tools=True, supports_json_schema=False, supports_json_object=True,
        supports_seed=True, supports_stream=True, max_context=64_000,
    ),
    "kimi": Capabilities(
        supports_tools=True, supports_json_schema=False, supports_json_object=True,
        supports_seed=False, supports_stream=True, max_context=128_000,
    ),
    "codex": Capabilities(
        supports_tools=True, supports_json_schema=True, supports_json_object=True,
        supports_seed=True, supports_stream=True, max_context=200_000,
    ),
}


# ---------------------------------------------------------------- 请求与响应

@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[Message, ...]
    response_schema: dict[str, Any] | None = None
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int = 4096
    temperature: float = 0.2
    seed: int | None = None
    timeout_s: float = 120.0
    idempotency_key: str = ""

    def digest(self) -> str:
        raw = json.dumps(
            {
                "messages": [m.to_dict() for m in self.messages],
                "schema": self.response_schema,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "seed": self.seed,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelResponse:
    model: str
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    degraded: bool = False
    degraded_reason: str = ""
    raw_digest: str = ""
    request_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "finish_reason": self.finish_reason,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "raw_digest": self.raw_digest,
            "request_digest": self.request_digest,
            "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in self.tool_calls],
        }


# ---------------------------------------------------------------- 适配器接口

class ModelAdapter(TypingProtocol):
    """一切供应商适配器必须满足的形状。编排层只认这个接口。"""

    provider: str
    model: str

    def capabilities(self) -> Capabilities: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ProviderRegistry:
    """供应商注册表。新增第四家不需要改编排层——这是"可插拔"的可验证形式。"""

    def __init__(self) -> None:
        self._adapters: dict[str, ModelAdapter] = {}

    def register(self, name: str, adapter: ModelAdapter) -> None:
        if name in self._adapters:
            raise ValueError(f"供应商已注册：{name}")
        self._adapters[name] = adapter

    def get(self, name: str) -> ModelAdapter:
        if name not in self._adapters:
            raise KeyError(f"未注册的供应商：{name}（已注册：{', '.join(sorted(self._adapters))}）")
        return self._adapters[name]

    def names(self) -> list[str]:
        return sorted(self._adapters)

    def __contains__(self, name: object) -> bool:
        return name in self._adapters


# ---------------------------------------------------------------- JSON 归一化

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class PayloadError(ValueError):
    """模型输出无法解析成契约要求的 JSON。"""


def extract_json(text: str) -> Any:
    """从模型输出里抽出 JSON 对象。

    这是真实的方言归一化工作：有的供应商会包 ```json 围栏，
    有的会在前后加解释文字，有的会吐单引号。这里只做保守处理，
    失败就明确报错，绝不猜。
    """
    if not text or not text.strip():
        raise PayloadError("模型返回空内容")

    candidates: list[str] = []
    fenced = _FENCE.findall(text)
    candidates.extend(block.strip() for block in fenced)
    candidates.append(text.strip())

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise PayloadError(f"无法解析为 JSON，前 200 字符：{text[:200]!r}")


def digest_text(raw: bytes | str) -> str:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()
