"""FastAPI dependencies — shared instances injected into routes."""

from __future__ import annotations

from functools import lru_cache

from copper.llm.base import LLMBase


@lru_cache(maxsize=1)
def get_llm() -> LLMBase:
    """Return the configured LLM backend (cached for the process lifetime)."""
    from copper.config import settings

    provider_name = settings.copper_llm_provider
    model = settings.copper_llm_model

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
