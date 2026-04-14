"""Plugin registry — selects the right ingest plugin for a given file."""

from __future__ import annotations

from pathlib import Path

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

    def to_markdown(self, path: Path) -> str:
        """Convert *path* to markdown using the first matching plugin.

        Raises ValueError if no plugin can handle the file.
        """
        for plugin in self._plugins:
            if plugin.can_handle(path):
                return plugin.to_markdown(path)
        raise ValueError(
            f"No ingest plugin found for '{path.name}'. "
            "Supported formats: .pdf, .md, .txt, and most text files."
        )


def default_registry() -> IngestRegistry:
    """Return the default registry with all built-in plugins registered."""
    registry = IngestRegistry()
    registry.register(PDFPlugin())       # .pdf — binary, must come before plain-text sniff
    registry.register(ObsidianPlugin())  # .md — checked before PlainText to normalize wikilinks
    registry.register(PlainTextPlugin()) # everything else
    return registry
