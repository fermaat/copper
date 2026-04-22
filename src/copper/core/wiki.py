"""
WikiManager — low-level operations on wiki markdown files.
The Archivist uses this to read, write and maintain the wiki.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class WikiPage:
    def __init__(self, path: Path):
        self.path = path
        self._raw: str | None = None

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def raw(self) -> str:
        if self._raw is None:
            self._raw = self.path.read_text() if self.path.exists() else ""
        return self._raw

    @property
    def frontmatter(self) -> dict[str, Any]:
        m = FRONTMATTER_RE.match(self.raw)
        if not m:
            return {}
        try:
            return yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return {}

    @property
    def body(self) -> str:
        m = FRONTMATTER_RE.match(self.raw)
        return self.raw[m.end() :] if m else self.raw

    def write(self, content: str) -> None:
        self.path.write_text(content)
        self._raw = content

    def exists(self) -> bool:
        return self.path.exists()


class WikiManager:
    """Manages read/write operations on a wiki directory."""

    def __init__(self, wiki_dir: Path):
        self.wiki_dir = wiki_dir

    def page(self, slug: str) -> WikiPage:
        slug = _to_slug(slug)
        return WikiPage(self.wiki_dir / f"{slug}.md")

    def index(self) -> WikiPage:
        return WikiPage(self.wiki_dir / "index.md")

    def log(self) -> WikiPage:
        return WikiPage(self.wiki_dir / "log.md")

    def all_pages(self) -> list[WikiPage]:
        return [
            WikiPage(p)
            for p in sorted(self.wiki_dir.glob("*.md"))
            if p.name not in ("index.md", "log.md") and not p.name.startswith("lint-report")
        ]

    def create_page(
        self,
        slug: str,
        title: str,
        body: str,
        source_count: int = 1,
        status: str = "draft",
    ) -> WikiPage:
        today = datetime.now().strftime("%Y-%m-%d")
        fm = {
            "title": title,
            "created": today,
            "last_updated": today,
            "source_count": source_count,
            "status": status,
        }
        content = f"---\n{yaml.dump(fm, default_flow_style=False, allow_unicode=True)}---\n\n{body}"
        page = self.page(slug)
        page.write(content)
        return page

    def update_page(self, slug: str, new_body: str, bump_source_count: bool = False) -> WikiPage:
        page = self.page(slug)
        if not page.exists():
            raise FileNotFoundError(f"Wiki page '{slug}' does not exist.")

        fm = page.frontmatter
        fm["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        if bump_source_count:
            fm["source_count"] = fm.get("source_count", 0) + 1

        content = (
            f"---\n{yaml.dump(fm, default_flow_style=False, allow_unicode=True)}---\n\n{new_body}"
        )
        page.write(content)
        return page

    def upsert_page(
        self,
        slug: str,
        title: str,
        body: str,
        bump_source_count: bool = True,
    ) -> WikiPage:
        """Create if it doesn't exist, update if it does."""
        page = self.page(slug)
        if page.exists():
            return self.update_page(slug, body, bump_source_count=bump_source_count)
        return self.create_page(slug, title, body)

    def read_index(self) -> str:
        return self.index().raw

    def update_index(self, new_content: str) -> None:
        self.index().write(new_content)

    def append_log(self, action: str, description: str) -> None:
        date = datetime.now().strftime("%Y-%m-%d")
        entry = f"\n## [{date}] {action} | {description}\n"
        log = self.log()
        current = log.raw
        log.write(current + entry)

    def find_pages_mentioning(self, term: str) -> list[WikiPage]:
        return [p for p in self.all_pages() if term.lower() in p.raw.lower()]


def _to_slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text
