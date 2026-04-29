"""LLM provider abstraction with circuit breaker pattern."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import httpx

from afriagent.config import settings, LLMProvider
from afriagent.config.logging import get_logger
from afriagent.observability import LLM_CALLS, LLM_LATENCY, LLM_TOKENS, CIRCUIT_STATE

log = get_logger(__name__)


class CircuitState(Enum):
    CLOSED = 0      # Normal operation
    OPEN = 1        # Failing, reject calls
    HALF_OPEN = 2   # Testing recovery


class CircuitBreaker:
    """Circuit breaker for LLM provider failover."""

    def __init__(
        self,
        fail_threshold: int = 5,
        reset_seconds: int = 60,
    ) -> None:
        self.fail_threshold = fail_threshold
        self.reset_seconds = reset_seconds
        self.state = CircuitState.CLOSED
        self.fail_count = 0
        self.last_fail_time = 0.0

    def record_success(self) -> None:
        self.fail_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.fail_count += 1
        self.last_fail_time = time.time()
        if self.fail_count >= self.fail_threshold:
            self.state = CircuitState.OPEN
            log.warning("Circuit breaker OPEN", failures=self.fail_count)

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_fail_time > self.reset_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: allow one test request


class LLMResponse(BaseModel):
    """Standardized LLM response."""

    content: str
    model: str
    provider: str
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = {}


from pydantic import BaseModel


class BaseLLMProvider(ABC):
    """Abstract LLM provider interface."""

    def __init__(self) -> None:
        self.circuit = CircuitBreaker(
            fail_threshold=settings.circuit_breaker_fail_threshold,
            reset_seconds=settings.circuit_breaker_reset_seconds,
        )

    @abstractmethod
    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        ...

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        ...


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API provider."""

    def __init__(self) -> None:
        super().__init__()
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        self.embedding_model = settings.openai_embedding_model

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        if not self.circuit.allow_request():
            CIRCUIT_STATE.labels(provider="openai").set(1)
            raise RuntimeError("Circuit breaker OPEN for OpenAI")

        CIRCUIT_STATE.labels(provider="openai").set(0)
        start = time.time()

        try:
            with LLM_LATENCY.labels(provider="openai").time():
                response = await self.client.chat.completions.create(
                    model=kwargs.get("model", self.model),
                    messages=messages,
                    max_tokens=kwargs.get("max_tokens", settings.llm_max_tokens),
                    temperature=kwargs.get("temperature", settings.llm_temperature),
                )

            latency = (time.time() - start) * 1000
            usage = response.usage
            self.circuit.record_success()

            LLM_CALLS.labels(provider="openai", model=self.model, status="success").inc()
            if usage:
                LLM_TOKENS.labels(provider="openai", direction="input").inc(usage.prompt_tokens)
                LLM_TOKENS.labels(provider="openai", direction="output").inc(usage.completion_tokens)

            return LLMResponse(
                content=response.choices[0].message.content or "",
                model=response.model,
                provider="openai",
                tokens_input=usage.prompt_tokens if usage else 0,
                tokens_output=usage.completion_tokens if usage else 0,
                latency_ms=latency,
            )
        except Exception as e:
            self.circuit.record_failure()
            LLM_CALLS.labels(provider="openai", model=self.model, status="error").inc()
            log.error("OpenAI call failed", error=str(e))
            raise

    async def embed(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )
        return response.data[0].embedding


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider."""

    def __init__(self) -> None:
        super().__init__()
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        if not self.circuit.allow_request():
            CIRCUIT_STATE.labels(provider="anthropic").set(1)
            raise RuntimeError("Circuit breaker OPEN for Anthropic")

        CIRCUIT_STATE.labels(provider="anthropic").set(0)
        start = time.time()

        try:
            # Extract system message
            system_msg = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    user_messages.append(msg)

            with LLM_LATENCY.labels(provider="anthropic").time():
                response = await self.client.messages.create(
                    model=kwargs.get("model", self.model),
                    max_tokens=kwargs.get("max_tokens", settings.llm_max_tokens),
                    system=system_msg,
                    messages=user_messages,
                )

            latency = (time.time() - start) * 1000
            self.circuit.record_success()

            LLM_CALLS.labels(provider="anthropic", model=self.model, status="success").inc()
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            LLM_TOKENS.labels(provider="anthropic", direction="input").inc(tokens_in)
            LLM_TOKENS.labels(provider="anthropic", direction="output").inc(tokens_out)

            return LLMResponse(
                content=response.content[0].text if response.content else "",
                model=response.model,
                provider="anthropic",
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                latency_ms=latency,
            )
        except Exception as e:
            self.circuit.record_failure()
            LLM_CALLS.labels(provider="anthropic", model=self.model, status="error").inc()
            log.error("Anthropic call failed", error=str(e))
            raise

    async def embed(self, text: str) -> list[float]:
        # Anthropic doesn't have an embedding API; fall back to OpenAI
        raise NotImplementedError("Use OpenAI for embeddings with Anthropic")


class OllamaProvider(BaseLLMProvider):
    """Ollama local LLM provider for cost control."""

    def __init__(self) -> None:
        super().__init__()
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=120)

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        if not self.circuit.allow_request():
            CIRCUIT_STATE.labels(provider="ollama").set(1)
            raise RuntimeError("Circuit breaker OPEN for Ollama")

        CIRCUIT_STATE.labels(provider="ollama").set(0)
        start = time.time()

        try:
            with LLM_LATENCY.labels(provider="ollama").time():
                resp = await self._http.post(
                    "/api/chat",
                    json={
                        "model": kwargs.get("model", self.model),
                        "messages": messages,
                        "stream": False,
                        "options": {
                            "temperature": kwargs.get("temperature", settings.llm_temperature),
                            "num_predict": kwargs.get("max_tokens", settings.llm_max_tokens),
                        },
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            latency = (time.time() - start) * 1000
            self.circuit.record_success()
            LLM_CALLS.labels(provider="ollama", model=self.model, status="success").inc()

            return LLMResponse(
                content=data.get("message", {}).get("content", ""),
                model=data.get("model", self.model),
                provider="ollama",
                tokens_input=data.get("prompt_eval_count", 0),
                tokens_output=data.get("eval_count", 0),
                latency_ms=latency,
                raw=data,
            )
        except Exception as e:
            self.circuit.record_failure()
            LLM_CALLS.labels(provider="ollama", model=self.model, status="error").inc()
            log.error("Ollama call failed", error=str(e))
            raise

    async def embed(self, text: str) -> list[float]:
        resp = await self._http.post(
            "/api/embeddings",
            json={"model": self.model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    async def close(self) -> None:
        await self._http.aclose()


# ── Provider Factory ──────────────────────────────────────────────


def create_llm_provider() -> BaseLLMProvider:
    """Create the configured LLM provider."""
    match settings.llm_provider:
        case LLMProvider.OPENAI:
            return OpenAIProvider()
        case LLMProvider.ANTHROPIC:
            return AnthropicProvider()
        case LLMProvider.OLLAMA:
            return OllamaProvider()
        case _:
            raise ValueError(f"Unknown provider: {settings.llm_provider}")
