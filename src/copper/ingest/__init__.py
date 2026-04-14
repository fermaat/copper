"""
Ingest plugins — normalize any source format to markdown before the Archivist sees it.

Usage:
    from copper.ingest.registry import default_registry

    markdown = default_registry().to_markdown(Path("paper.pdf"))
"""

from copper.ingest.base import IngestPlugin
from copper.ingest.registry import IngestRegistry, default_registry

__all__ = ["IngestPlugin", "IngestRegistry", "default_registry"]
