"""Wire adapters (the ONLY place that knows provider HTTP shapes). Pure: build a request, parse a response.
Transport, retries, validation and metrics live in `client.ModelClient`.

* OpenAICompatibleAdapter: `/chat/completions` (openai_chat) and `/responses` (openai_responses). Serves OpenAI,
  NVIDIA NIM, vLLM and any other OpenAI-compatible server by base URL.
* AnthropicAdapter: native Messages API (`/v1/messages`); also what NIM-style `/v1/messages` gateways speak.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

Messages = list[dict[str, str]]
Parsed = tuple[str, int | None, int | None, str | None]  # text, input tokens, output tokens, stop reason

ANTHROPIC_VERSION = "2023-06-01"


def _endpoint(base_url: str, suffix: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith(f"/{suffix}"):
        return base
    return f"{base}/{suffix}" if re.search(r"/v\d+$", base) else f"{base}/v1/{suffix}"


def _temperature_rejected(text: str) -> bool:
    low = text.lower()
    return "temperature" in low and any(w in low for w in ("unsupported", "not support", "deprecated", "default"))


class OpenAICompatibleAdapter:
    provider_family = "openai_compatible"

    def __init__(self, protocol: str = "openai_chat") -> None:
        if protocol not in ("openai_chat", "openai_responses"):
            raise ValueError(protocol)
        self.protocol = protocol
        self.token_param = "max_tokens" if protocol == "openai_chat" else "max_output_tokens"
        self.send_temperature = True
        self.developer_role = False  # provider 'meta' + openai_responses: instructions go in a `developer` message

    def endpoint(self, base_url: str) -> str:
        return _endpoint(base_url, "chat/completions" if self.protocol == "openai_chat" else "responses")

    def headers(self, api_key: str, base_url: str) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if api_key:
            h["authorization"] = f"Bearer {api_key}"
        return h

    def body(self, model: str, system: str, messages: Messages, max_tokens: int, temperature: float | None) -> dict[str, Any]:
        if self.protocol == "openai_chat":
            body: dict[str, Any] = {"model": model, self.token_param: max_tokens,
                                    "messages": [{"role": "system", "content": system}, *messages]}
        elif self.developer_role:
            body = {"model": model, self.token_param: max_tokens,
                    "input": [{"role": "developer", "content": system},
                              *({"role": m["role"], "content": m["content"]} for m in messages)]}
        else:
            body = {"model": model, "instructions": system, self.token_param: max_tokens,
                    "input": [{"role": m["role"], "content": m["content"]} for m in messages]}
        if temperature is not None and self.send_temperature:
            body["temperature"] = temperature
        return body

    def adapt_after_400(self, text: str) -> bool:
        """Some models reject `max_tokens` (want `max_completion_tokens`) or `temperature`. True -> retry once."""
        low = text.lower()
        if self.protocol == "openai_chat" and self.token_param == "max_tokens" and "max_completion_tokens" in low:
            self.token_param = "max_completion_tokens"
            return True
        if self.send_temperature and _temperature_rejected(low):
            self.send_temperature = False
            return True
        return False

    def parse(self, data: dict[str, Any]) -> Parsed:
        u = data.get("usage") or {}
        if self.protocol == "openai_chat":
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
            return content, u.get("prompt_tokens"), u.get("completion_tokens"), choice.get("finish_reason")
        text = data.get("output_text") or ""
        if not text:
            for item in data.get("output", []):  # reasoning items are ignored: never read or stored
                if item.get("type") == "message":
                    text += "".join(c.get("text", "") for c in item.get("content", []) if c.get("type") == "output_text")
        return text, u.get("input_tokens"), u.get("output_tokens"), data.get("status")


class AnthropicAdapter:
    provider_family = "anthropic"
    protocol = "anthropic_messages"

    def endpoint(self, base_url: str) -> str:
        return _endpoint(base_url, "messages")

    def headers(self, api_key: str, base_url: str) -> dict[str, str]:
        h = {"anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        if api_key:
            h["x-api-key"] = api_key
            host = urlparse(base_url).hostname or ""
            if not host.endswith("anthropic.com"):  # compatible gateways (e.g. NIM /v1/messages) may want Bearer
                h["authorization"] = f"Bearer {api_key}"
        return h

    def body(self, model: str, system: str, messages: Messages, max_tokens: int, temperature: float | None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if temperature is not None and self.send_temperature:
            body["temperature"] = temperature
        return body

    send_temperature = True

    def adapt_after_400(self, text: str) -> bool:
        if self.send_temperature and _temperature_rejected(text):
            self.send_temperature = False
            return True
        return False

    def parse(self, data: dict[str, Any]) -> Parsed:
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")  # skips thinking
        u = data.get("usage") or {}
        return text, u.get("input_tokens"), u.get("output_tokens"), data.get("stop_reason")


WireAdapter = OpenAICompatibleAdapter | AnthropicAdapter


def adapter_for(protocol: str) -> WireAdapter:
    from app.connectors.llm.gateway import UnsupportedModelProtocol

    if protocol in ("openai_chat", "openai_responses"):
        return OpenAICompatibleAdapter(protocol)
    if protocol == "anthropic_messages":
        return AnthropicAdapter()
    raise UnsupportedModelProtocol(
        f"unsupported MODEL_API_PROTOCOL '{protocol}' (use openai_chat | openai_responses | anthropic_messages)"
    )
