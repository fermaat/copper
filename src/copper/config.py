"""
Runtime configuration for Copper.

Uses pydantic-settings to load typed settings from environment variables
and .env files. Follows the same pattern as core-llm-bridge/config.py.

Usage:
    from copper.config import settings, logger

    logger.info(f"LLM provider: {settings.copper_llm_provider}")
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """
    Copper settings loaded from environment variables or .env files.

    Priority order:
        1. Environment variables
        2. .env.local  (local overrides, not committed)
        3. .env        (project defaults)
        4. Hardcoded defaults below
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

    # ── LLM ─────────────────────────────────────────────────────────
    copper_llm_provider: str = "mock"   # mock | ollama | anthropic | openai
    copper_llm_model: str = ""          # passed to the provider; empty = provider default

    # ── Storage ─────────────────────────────────────────────────────
    copper_minds_dir: str = ""          # empty → ~/.copper/minds

    # ── API server ──────────────────────────────────────────────────
    copper_host: str = "127.0.0.1"
    copper_port: int = 8000
    copper_reload: bool = False

    # ── Logging ─────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_storage_folder: str = "logs"
    log_console_output: bool = True

    # ── Properties ──────────────────────────────────────────────────

    @property
    def minds_path(self) -> Path:
        """Resolved path to the copperminds directory (created if absent)."""
        p = Path(self.copper_minds_dir) if self.copper_minds_dir else Path.home() / ".copper" / "minds"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        """Resolved path to the logs directory (created if absent)."""
        p = PROJECT_ROOT / self.log_storage_folder
        p.mkdir(parents=True, exist_ok=True)
        return p


def configure_logger(
    level: str | None = None,
    enable_console: bool | None = None,
    log_file: str | None = None,
) -> None:
    """Configure the Copper logger (loguru)."""
    _level = level if level is not None else settings.log_level
    _console = enable_console if enable_console is not None else settings.log_console_output
    _file = log_file or str(settings.logs_dir / "copper.log")

    logger.remove()

    logger.add(
        _file,
        level=_level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        ),
        rotation="10 MB",
    )

    if _console:
        logger.add(
            sys.stderr,
            level=_level,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        )


# ── Initialise at import ────────────────────────────────────────────
settings = Settings()
configure_logger()

__all__ = ["settings", "logger", "configure_logger", "PROJECT_ROOT"]
