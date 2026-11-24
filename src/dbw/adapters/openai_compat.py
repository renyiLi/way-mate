"""OpenAI 兼容适配器：一套代码覆盖 DeepSeek / Kimi，必要时覆盖 Codex 兼容端点。

只依赖标准库（urllib），不引入 requests / httpx，保证答辩机器上装完 pyyaml 就能跑。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from ..protocol.base import (
    AdapterError,
    Capabilities,
    CapabilityError,
    ModelRequest,
    ModelResponse,
    PermanentError,
    ToolCall,
    TransientError,
    digest_text,
)
from ..protocol.envelope import TokenUsage

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _error_message(body: bytes) -> str:
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return (body or b"").decode("utf-8", "replace")[:200]
    error = data.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    return json.dumps(data, ensure_ascii=False)[:300]


class OpenAICompatAdapter:
    """满足 ModelAdapter 协议。provider / model / capabilities / complete。"""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        capabilities: Capabilities | None = None,
        timeout_s: float = 90.0,
        max_retries: int = 3,
        backoff_s: float = 1.5,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self._caps = capabilities or Capabilities()
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s

    def capabilities(self) -> Capabilities:
        return self._caps

    # ------------------------------------------------------------ 主流程

    def complete(self, request: ModelRequest) -> ModelResponse:
        timeout_s = request.timeout_s or self.timeout_s
        payload, degraded, degraded_reason = self._build(request)

        last_error: AdapterError | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                status, body = self._post(payload, timeout_s)
            except TransientError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(self.backoff_s * (2**attempt))
                continue
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return self._parse(body, request, elapsed_ms, degraded, degraded_reason)

        raise last_error or TransientError("未知的调用失败")

    # ------------------------------------------------------------ 组包

    def _build(self, request: ModelRequest) -> tuple[dict, bool, str]:
        caps = self.capabilities()
        payload: dict = {
            "model": self.model,
            "messages": [m.to_dict() for m in request.messages],
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        degraded = False
        reasons: list[str] = []

        # 部分推理模型只允许固定 temperature（如 kimi-k3 只接受 1）。
        # 这属于"参数取值域"限制，不是能力缺失：强制取值并登记降级，而不是直接失败。
        if caps.fixed_temperature is not None:
            payload["temperature"] = caps.fixed_temperature
            if abs(request.temperature - caps.fixed_temperature) > 1e-9:
                degraded = True
                reasons.append(
                    f"temperature 被强制为 {caps.fixed_temperature}（该模型不支持采样控制，请求值 {request.temperature} 被忽略）"
                )
        else:
            payload["temperature"] = request.temperature

        if request.response_schema:
            if caps.supports_json_schema:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "result", "schema": request.response_schema, "strict": True},
                }
            elif caps.supports_json_object:
                payload["response_format"] = {"type": "json_object"}
                degraded = True
                reasons.append("降级为 json_object（该供应商不支持严格 schema）")
            else:
                degraded = True
                reasons.append("降级为纯文本 JSON 解析")

        if request.tools:
            if not caps.supports_tools:
                raise CapabilityError(f"{self.provider} 不支持工具调用，且无安全降级方案")
            payload["tools"] = [tool.to_openai() for tool in request.tools]

        if request.seed is not None:
            if caps.supports_seed:
                payload["seed"] = request.seed
            else:
                degraded = True
                reasons.append("seed 被忽略（该供应商不可复现采样）")

        return payload, degraded, "；".join(reasons)

    # ------------------------------------------------------------ 网络

    def _post(self, payload: dict, timeout_s: float) -> tuple[int, bytes]:
        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            detail = _error_message(exc.read())
            if exc.code in _RETRYABLE_STATUS:
                raise TransientError(f"HTTP {exc.code}：{detail}", status=exc.code) from None
            raise PermanentError(f"HTTP {exc.code}：{detail}", status=exc.code) from None
        except (socket.timeout, TimeoutError) as exc:
            raise TransientError(f"请求超时（{timeout_s:.0f}s）：{exc}") from None
        except urllib.error.URLError as exc:
            raise TransientError(f"网络不可达：{exc.reason}") from None

    # ------------------------------------------------------------ 解包

    def _parse(
        self,
        body: bytes,
        request: ModelRequest,
        elapsed_ms: int,
        degraded: bool,
        degraded_reason: str,
    ) -> ModelResponse:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PermanentError(f"响应不是合法 JSON：{exc}") from None

        choices = data.get("choices") or []
        if not choices:
            raise PermanentError(f"响应没有 choices：{json.dumps(data, ensure_ascii=False)[:200]}")
        choice = choices[0]
        message = choice.get("message") or {}
        usage_raw = data.get("usage") or {}

        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                arguments = {"_raw": raw_args}
            tool_calls.append(ToolCall(name=str(function.get("name", "")), arguments=arguments))

        cached = usage_raw.get("prompt_cache_hit_tokens") or 0
        return ModelResponse(
            model=str(data.get("model") or self.model),
            text=message.get("content") or "",
            tool_calls=tuple(tool_calls),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            usage=TokenUsage(
                prompt=int(usage_raw.get("prompt_tokens") or 0),
                completion=int(usage_raw.get("completion_tokens") or 0),
                cached=int(cached),
            ),
            latency_ms=elapsed_ms,
            degraded=degraded,
            degraded_reason=degraded_reason,
            raw_digest=digest_text(body),
            request_digest=request.digest(),
        )
