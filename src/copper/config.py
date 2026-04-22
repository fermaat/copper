"""
Runtime configuration for Copper.

Settings are loaded from .env / .env.local at the project root.
Logger is configured via core-utils' configure_logger.

Usage:
    from copper.config import settings, logger

    logger.info(f"LLM provider: {settings.copper_llm_provider}")
"""

from __future__ import annotations

from pathlib import Path

from core_utils.logger import configure_logger, logger
from core_utils.settings import CoreSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(CoreSettings):
    """
    Copper settings loaded from environment variables or .env files.

    Inherits environment/logging fields from CoreSettings.
    Adds Copper-specific configuration on top.
    """

    model_config = {
        "case_sensitive": False,
        "extra": "allow",
        "env_file": [
            str(PROJECT_ROOT / ".env"),
            str(PROJECT_ROOT / ".env.local"),
        ],
        "env_file_encoding": "utf-8",
    }

    # ── LLM — generic (fallback for all workflows) ──────────────────
    copper_llm_provider: str = "mock"   # mock | ollama | anthropic | openai
    copper_llm_model: str = ""          # passed to the provider; empty = provider default

    # ── LLM — store + polish (override generic) ─────────────────────
    # Used when building/updating the wiki. Typically a more capable model.
    copper_store_provider: str = ""     # empty = fall back to copper_llm_provider
    copper_store_model: str = ""        # empty = fall back to copper_llm_model

    # ── LLM — tap + chat (override generic) ─────────────────────────
    # Used when querying the wiki. Can be a faster/cheaper/local model.
    copper_tap_provider: str = ""       # empty = fall back to copper_llm_provider
    copper_tap_model: str = ""          # empty = fall back to copper_llm_model

    # ── LLM — ingest (multimodal, for describing PDF images) ────────
    # Empty provider disables image description entirely.
    # Model MUST support vision (e.g. gemma3:12b, llava, claude-opus-4-6).
    copper_ingest_provider: str = ""    # empty = skip image description
    copper_ingest_model: str = ""       # empty = fall back to copper_llm_model

    # ── LLM — Ollama ────────────────────────────────────────────────
    copper_ollama_base_url: str = "http://localhost:11434"
    copper_ollama_timeout: int = 300

    # ── LLM — Anthropic ─────────────────────────────────────────────
    copper_anthropic_api_key: str = ""
    copper_anthropic_timeout: int = 300

    # ── LLM — OpenAI ────────────────────────────────────────────────
    copper_openai_api_key: str = ""
    copper_openai_base_url: str = ""    # empty = OpenAI default endpoint
    copper_openai_timeout: int = 300

    # ── Storage ─────────────────────────────────────────────────────
    copper_minds_dir: str = ""          # empty → ~/.copper/minds

    # ── API server ──────────────────────────────────────────────────
    copper_host: str = "127.0.0.1"
    copper_port: int = 8000
    copper_reload: bool = False

    @property
    def minds_path(self) -> Path:
        """Resolved path to the copperminds directory (created if absent)."""
        p = Path(self.copper_minds_dir) if self.copper_minds_dir else Path.home() / ".copper" / "minds"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
configure_logger(settings, log_file=str(settings.logs_dir / "copper.log"))

__all__ = ["settings", "logger", "configure_logger", "PROJECT_ROOT"]
