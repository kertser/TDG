"""
Centralized LLM client factory.

Provides a single function to get an AsyncOpenAI client that respects
the LOCAL_MODEL_URL / OPENAI_API_KEY configuration.  All backend code
that needs an LLM should call `get_llm_client()` instead of
constructing AsyncOpenAI directly.

Priority:
  1. If OPENAI_API_KEY is set → use OpenAI cloud
  2. Else if LOCAL_MODEL_URL is set → use local llama.cpp / vLLM / Ollama
  3. Else → return None  (caller should fall back to keyword / rule-based)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from backend.config import settings

logger = logging.getLogger(__name__)

# Module-level singletons (created on first call)
_cloud_client: AsyncOpenAI | None = None
_local_client: AsyncOpenAI | None = None


@dataclass(frozen=True)
class LLMClientInfo:
    """Wraps a client with its associated model names."""
    client: AsyncOpenAI
    model: str           # full / default model
    model_mini: str      # mid-tier model
    model_nano: str      # cheapest model
    is_local: bool       # True when using local model (no tiered routing)


def get_llm_client() -> LLMClientInfo | None:
    """
    Return an LLM client + model names based on current config.

    For local models all three tiers point to the same model name
    (small local models don't have quality tiers).
    """
    global _cloud_client, _local_client

    # ── Cloud (OpenAI) ────────────────────────────────
    if settings.OPENAI_API_KEY:
        if _cloud_client is None:
            _cloud_client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=15.0,
            )
            logger.info("LLM client: OpenAI cloud (%s)", settings.OPENAI_MODEL)
        cloud = _cloud_client
        return LLMClientInfo(
            client=cloud,
            model=settings.OPENAI_MODEL,
            model_mini=settings.OPENAI_MODEL_MINI,
            model_nano=settings.OPENAI_MODEL_NANO,
            is_local=False,
        )

    # ── Local (llama.cpp / vLLM / Ollama) ─────────────
    if settings.LOCAL_MODEL_URL:
        if _local_client is None:
            _local_client = AsyncOpenAI(
                api_key="local",
                base_url=settings.LOCAL_MODEL_URL,
            )
            logger.info("LLM client: local @ %s (model=%s)",
                        settings.LOCAL_MODEL_URL, settings.LOCAL_MODEL_NAME)
        local = _local_client
        local_name = settings.LOCAL_MODEL_NAME
        return LLMClientInfo(
            client=local,
            model=local_name,
            model_mini=local_name,
            model_nano=local_name,
            is_local=True,
        )

    # ── No LLM available ──────────────────────────────
    logger.warning("No LLM configured (OPENAI_API_KEY and LOCAL_MODEL_URL both empty)")
    return None


def get_local_client() -> LLMClientInfo | None:
    """
    Return a local LLM client ONLY (for triage classification tasks).

    Returns None if LOCAL_MODEL_URL is not configured.
    Unlike get_llm_client(), this never falls back to cloud.
    """
    global _local_client

    if not settings.LOCAL_MODEL_URL:
        return None

    if _local_client is None:
        _local_client = AsyncOpenAI(
            api_key="local",
            base_url=settings.LOCAL_MODEL_URL,
            timeout=settings.LOCAL_TRIAGE_TIMEOUT,
        )
        logger.info("Local triage client: %s (model=%s)",
                     settings.LOCAL_MODEL_URL, settings.LOCAL_MODEL_NAME)

    return LLMClientInfo(
        client=_local_client,
        model=settings.LOCAL_MODEL_NAME,
        model_mini=settings.LOCAL_MODEL_NAME,
        model_nano=settings.LOCAL_MODEL_NAME,
        is_local=True,
    )


