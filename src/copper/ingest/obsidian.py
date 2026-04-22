"""Obsidian vault ingest plugin — normalizes Obsidian markdown to standard markdown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from copper.ingest.base import IngestPlugin


# Matches [[Page Name|Display Text]] or [[Page Name]]
_WIKILINK_ALIASED = re.compile(r'\[\[([^\]|]+)\|([^\]]+)\]\]')
_WIKILINK_PLAIN = re.compile(r'\[\[([^\]]+)\]\]')

# Matches ![[embed.png]] — file embeds (images, audio, other notes)
_EMBED = re.compile(r'!\[\[[^\]]*\]\]')


class ObsidianPlugin(IngestPlugin):
    """Normalizes Obsidian-flavored markdown to standard markdown.

    Handles:
    - [[Page Name|Display]] → Display
    - [[Page Name]] → Page Name
    - ![[image.png]] → stripped (embeds cannot be resolved)

    Safe to run on any .md file: operations are no-ops on standard markdown.
    """

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".md"

    def to_markdown(self, path: Path, image_describer: Any = None) -> str:
        content = path.read_text(encoding="utf-8", errors="replace")
        content = _EMBED.sub("", content)
        content = _WIKILINK_ALIASED.sub(r"\2", content)
        content = _WIKILINK_PLAIN.sub(r"\1", content)
        return content
