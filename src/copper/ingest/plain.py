"""Plain-text ingest plugin — fallback for UTF-8 readable files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from copper.ingest.base import IngestPlugin

# Extensions that are always treated as plain text, regardless of sniffing.
_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".xml",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
}


class PlainTextPlugin(IngestPlugin):
    """Reads files that are valid UTF-8 text."""

    def can_handle(self, path: Path) -> bool:
        if path.suffix.lower() in _TEXT_EXTENSIONS:
            return True
        # Sniff: try reading the first 512 bytes
        try:
            with path.open("rb") as f:
                chunk = f.read(512)
            chunk.decode("utf-8")
            return True
        except (UnicodeDecodeError, OSError):
            return False

    def to_markdown(
        self,
        path: Path,
        image_describer: Any = None,
        image_save_dir: Any = None,
    ) -> str:
        return path.read_text(encoding="utf-8", errors="replace")
