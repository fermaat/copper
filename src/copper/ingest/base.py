"""Abstract base for ingest plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class IngestPlugin(ABC):
    """Converts a source file to a markdown string for the Archivist."""

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return True if this plugin knows how to process *path*."""

    @abstractmethod
    def to_markdown(self, path: Path) -> str:
        """Read *path* and return its content as a markdown string."""
