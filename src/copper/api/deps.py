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

        provider = create_provider(provider_name, **({"model": model} if model else {}))
        engine = BridgeEngine(provider=provider)
        return BridgeAdapter(engine)
    except ImportError:
        from copper.llm.mock import MockLLM
        return MockLLM()
