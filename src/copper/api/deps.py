"""FastAPI dependencies — shared instances injected into routes."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from copper.llm.base import LLMBase

if TYPE_CHECKING:
    from copper.core.coppermind import CopperMind
    from copper.ingest.image_describer import ImageDescriber


def _build_llm(provider_name: str, model: str) -> LLMBase:
    """Instantiate an LLMBase for the given provider and model."""
    from copper.config import settings

    if provider_name == "mock":
        from copper.llm.mock import MockLLM

        return MockLLM()

    try:
        from core_llm_bridge import BridgeEngine
        from core_llm_bridge.providers import create_provider
        from copper.llm.bridge_adapter import BridgeAdapter

        kwargs: dict = {}
        if model:
            kwargs["model"] = model

        if provider_name == "ollama":
            kwargs["base_url"] = settings.copper_ollama_base_url
            kwargs["timeout"] = settings.copper_ollama_timeout
        elif provider_name == "anthropic":
            kwargs["api_key"] = settings.copper_anthropic_api_key
            kwargs["timeout"] = settings.copper_anthropic_timeout
        elif provider_name == "openai":
            kwargs["api_key"] = settings.copper_openai_api_key
            kwargs["timeout"] = settings.copper_openai_timeout
            if settings.copper_openai_base_url:
                kwargs["base_url"] = settings.copper_openai_base_url

        provider = create_provider(provider_name, **kwargs)
        engine = BridgeEngine(provider=provider)
        return BridgeAdapter(engine)
    except ImportError:
        from copper.llm.mock import MockLLM

        return MockLLM()


def _resolve(
    mind_provider: str,
    mind_model: str,
    global_provider: str,
    global_model: str,
    fallback_provider: str,
    fallback_model: str,
) -> tuple[str, str]:
    """Apply the three-level resolution hierarchy for provider + model."""
    provider = mind_provider or global_provider or fallback_provider
    model = mind_model or global_model or fallback_model
    return provider, model


def get_store_llm(mind: "CopperMind") -> LLMBase:
    """Return the LLM for store + polish workflows.

    Resolution order:
      1. Per-mind override  (config.yaml: store_provider / store_model)
      2. Global store       (COPPER_STORE_PROVIDER / COPPER_STORE_MODEL)
      3. Global generic     (COPPER_LLM_PROVIDER   / COPPER_LLM_MODEL)
    """
    from copper.config import settings

    provider, model = _resolve(
        mind.config.store_provider,
        mind.config.store_model,
        settings.copper_store_provider,
        settings.copper_store_model,
        settings.copper_llm_provider,
        settings.copper_llm_model,
    )
    return _build_llm(provider, model)


def get_tap_llm(mind: "CopperMind") -> LLMBase:
    """Return the LLM for tap + chat workflows.

    Resolution order:
      1. Per-mind override  (config.yaml: tap_provider / tap_model)
      2. Global tap         (COPPER_TAP_PROVIDER / COPPER_TAP_MODEL)
      3. Global generic     (COPPER_LLM_PROVIDER / COPPER_LLM_MODEL)
    """
    from copper.config import settings

    provider, model = _resolve(
        mind.config.tap_provider,
        mind.config.tap_model,
        settings.copper_tap_provider,
        settings.copper_tap_model,
        settings.copper_llm_provider,
        settings.copper_llm_model,
    )
    return _build_llm(provider, model)


def get_ingest_describer(mind: "CopperMind") -> "ImageDescriber | None":
    """Return an ImageDescriber for multimodal PDF ingestion, or None if disabled.

    Resolution order:
      1. Per-mind override  (config.yaml: ingest_provider / ingest_model)
      2. Global ingest      (COPPER_INGEST_PROVIDER / COPPER_INGEST_MODEL)
    If neither is set, returns None — images are skipped.
    """
    from copper.config import settings
    from copper.ingest.image_describer import ImageDescriber

    provider = mind.config.ingest_provider or settings.copper_ingest_provider
    if not provider:
        return None

    model = mind.config.ingest_model or settings.copper_ingest_model or settings.copper_llm_model
    if not model:
        return None

    if provider == "ollama":
        return ImageDescriber(
            provider=provider,
            model=model,
            base_url=settings.copper_ollama_base_url,
            timeout=settings.copper_ollama_timeout,
        )
    # Future: add anthropic / openai multimodal branches here.
    return ImageDescriber(provider=provider, model=model)


@lru_cache(maxsize=1)
def get_llm() -> LLMBase:
    """Generic LLM using only the global fallback settings.

    Kept for backwards compatibility and simple use cases.
    """
    from copper.config import settings

    return _build_llm(settings.copper_llm_provider, settings.copper_llm_model)
