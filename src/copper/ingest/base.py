"""Abstract base for ingest plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class IngestPlugin(ABC):
    """Converts a source file to a markdown string for the Archivist."""

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return True if this plugin knows how to process *path*."""

    @abstractmethod
    def to_markdown(self, path: Path) -> str:
        """Read *path* and return its content as a markdown string."""

    def to_chunks(self, path: Path, max_chars: int, llm: Any = None) -> list[str]:
        """Split *path* content into chunks of at most *max_chars*.

        Default: convert to markdown then split naively at paragraph boundaries.
        Subclasses may override for smarter, format-aware splitting.
        """
        return naive_split(self.to_markdown(path), max_chars)


def naive_split(text: str, max_chars: int) -> list[str]:
    """Split *text* into chunks of at most *max_chars*, breaking at paragraphs."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, max_chars)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return [c for c in chunks if c]
