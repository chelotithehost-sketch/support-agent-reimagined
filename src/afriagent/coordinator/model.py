"""Coordinator LLM model loader — singleton llama-cpp-python for <100ms dispatch."""

from __future__ import annotations

import json
import logging
from typing import Any

from afriagent.config.logging import get_logger

log = get_logger(__name__)

# ── Singleton ─────────────────────────────────────────────────────

_model_instance: Any | None = None
_model_available: bool | None = None


def _check_availability() -> bool:
    """Check if llama-cpp-python is installed."""
    global _model_available
    if _model_available is None:
        try:
            import llama_cpp  # noqa: F401
            _model_available = True
        except ImportError:
            _model_available = False
            log.warning(
                "llama-cpp-python not installed — coordinator will use stub mode. "
                "Install with: pip install llama-cpp-python"
            )
    return _model_available


def get_model(model_path: str | None = None, model_name: str | None = None) -> Any | None:
    """Get or create the singleton coordinator LLM model.

    Args:
        model_path: Path to the GGUF model file. Uses config default if None.
        model_name: Model name for logging. Uses config default if None.

    Returns:
        llama_cpp.Llama instance or None if unavailable.
    """
    global _model_instance

    if _model_instance is not None:
        return _model_instance

    if not _check_availability():
        return None

    # Resolve model path from config
    from afriagent.config import settings

    path = model_path or settings.coordinator_model_path
    name = model_name or settings.coordinator_model_name

    if not path:
        log.warning(
            "No coordinator model path configured. "
            "Set AFRI_COORDINATOR_MODEL_PATH env var. Using stub mode."
        )
        return None

    try:
        from llama_cpp import Llama

        log.info("Loading coordinator model", path=path, name=name)
        _model_instance = Llama(
            model_path=path,
            n_ctx=2048,       # Context window — coordinator prompts are short
            n_threads=4,      # Keep threads low for latency
            n_gpu_layers=0,   # CPU-only for portability
            verbose=False,
        )
        log.info("Coordinator model loaded successfully", name=name)
        return _model_instance

    except Exception as e:
        log.error("Failed to load coordinator model", error=str(e), path=path)
        _model_available = False
        return None


def generate_json(
    prompt: str,
    system_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
) -> dict[str, Any] | None:
    """Generate a JSON response from the coordinator model.

    Args:
        prompt: The user message with context.
        system_prompt: The system prompt with tool registry etc.
        max_tokens: Max tokens to generate.
        temperature: Low temperature for deterministic dispatch.

    Returns:
        Parsed JSON dict or None on failure.
    """
    model = get_model()
    if model is None:
        return None

    try:
        # llama-cpp-python chat completion format
        response = model.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

        content = response["choices"][0]["message"]["content"]
        if not content:
            log.warning("Coordinator model returned empty response")
            return None

        parsed = json.loads(content)
        return parsed

    except json.JSONDecodeError as e:
        log.warning("Coordinator model returned invalid JSON", error=str(e), raw=content[:200])
        return None
    except Exception as e:
        log.error("Coordinator model inference failed", error=str(e))
        return None


def reset_model() -> None:
    """Reset the singleton model (for testing)."""
    global _model_instance, _model_available
    _model_instance = None
    _model_available = None
