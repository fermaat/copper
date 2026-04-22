"""Plugin registry — selects the right ingest plugin for a given file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copper.ingest.base import IngestPlugin
from copper.ingest.pdf import PDFPlugin
from copper.ingest.obsidian import ObsidianPlugin
from copper.ingest.plain import PlainTextPlugin


class IngestRegistry:
    """Ordered list of plugins. First match wins."""

    def __init__(self) -> None:
        self._plugins: list[IngestPlugin] = []

    def register(self, plugin: IngestPlugin) -> None:
        self._plugins.append(plugin)

    def _match(self, path: Path) -> IngestPlugin:
        for plugin in self._plugins:
            if plugin.can_handle(path):
                return plugin
        raise ValueError(
            f"No ingest plugin found for '{path.name}'. "
            "Supported formats: .pdf, .md, .txt, and most text files."
        )

    def to_markdown(self, path: Path, image_describer: Any = None) -> str:
        """Convert *path* to markdown using the first matching plugin."""
        return self._match(path).to_markdown(path, image_describer=image_describer)

    def to_chunks(
        self, path: Path, max_chars: int, llm: Any = None, image_describer: Any = None
    ) -> list[str]:
        """Split *path* into chunks using the first matching plugin."""
        return self._match(path).to_chunks(
            path, max_chars, llm=llm, image_describer=image_describer
        )


def default_registry() -> IngestRegistry:
    """Return the default registry with all built-in plugins registered."""
    registry = IngestRegistry()
    registry.register(PDFPlugin())  # .pdf — binary, must come before plain-text sniff
    registry.register(ObsidianPlugin())  # .md — checked before PlainText to normalize wikilinks
    registry.register(PlainTextPlugin())  # everything else
    return registry
