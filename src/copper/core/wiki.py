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

from core_utils.logger import logger

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
            if p.name not in ("index.md", "log.md", "_meta.md")
            and not p.name.startswith("lint-report")
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

    def move_page(self, slug: str, target_wiki: WikiManager) -> None:
        """Move a page from this wiki to target_wiki (copy + delete)."""
        page = self.page(slug)
        if not page.exists():
            raise FileNotFoundError(f"Wiki page '{slug}' does not exist.")
        target_wiki.page(slug).write(page.raw)
        page.path.unlink()

    def find_pages_mentioning(self, term: str) -> list[WikiPage]:
        return [p for p in self.all_pages() if term.lower() in p.raw.lower()]

    def merge_page(self, src_slug: str, dst_slug: str) -> None:
        """Merge page src_slug into dst_slug within this wiki.

        Steps (in order):
        1. Guard: both pages must exist; src != dst. Raises ValueError otherwise.
           If src does not exist (already merged), logs a warning and returns.
        2. Append src body to dst body, separated by a blank line.
           Bump dst source_count by src source_count. Keep dst frontmatter/title.
        3. Rewrite all [[src_slug]] wikilinks across every page and index.md to
           [[dst_slug]]. Uses a bracket-exact regex — never touches [[src_slug-extra]].
        4. Strip self-links in dst: remove any [[dst_slug]] inside dst's body.
        5. Update index.md: remove the line(s) mentioning src_slug.
        6. Delete src page file.
        7. append_log("merge", "src_slug → dst_slug").

        Chained merges (a→b followed by b→c) work naturally when executed
        serially: each call operates on the current wiki state.
        """
        src = self.page(src_slug)
        dst = self.page(dst_slug)

        if src_slug == dst_slug:
            raise ValueError(f"Cannot merge a page into itself: '{src_slug}'")

        if not src.exists():
            logger.warning(f"[wiki] merge_page: src '{src_slug}' no existe — no-op")
            return

        if not dst.exists():
            raise ValueError(f"Cannot merge into non-existent page '{dst_slug}'")

        # Step 2: append src body to dst, bump source_count.
        src_fm = src.frontmatter
        dst_fm = dst.frontmatter

        src_count = int(src_fm.get("source_count", 1))
        dst_count = int(dst_fm.get("source_count", 1))
        dst_fm["source_count"] = dst_count + src_count
        dst_fm["last_updated"] = datetime.now().strftime("%Y-%m-%d")

        merged_body = dst.body.rstrip() + "\n\n" + src.body.strip()
        new_dst_content = f"---\n{yaml.dump(dst_fm, default_flow_style=False, allow_unicode=True)}---\n\n{merged_body}"
        dst.write(new_dst_content)

        # Step 3: rewrite [[src_slug]] → [[dst_slug]] across all pages + index.
        src_pattern = re.compile(rf"\[\[{re.escape(src_slug)}\]\]")
        dst_link = f"[[{dst_slug}]]"

        pages_to_rewrite: list[WikiPage] = self.all_pages() + [self.index()]
        for page in pages_to_rewrite:
            if not page.exists():
                continue
            rewritten = src_pattern.sub(dst_link, page.raw)
            if rewritten != page.raw:
                page.write(rewritten)

        # Step 4: strip self-links in dst (now that src links point to dst).
        dst_page = self.page(dst_slug)  # re-read after rewrite
        self_pattern = re.compile(rf"\[\[{re.escape(dst_slug)}\]\]")
        cleaned = self_pattern.sub("", dst_page.raw)
        if cleaned != dst_page.raw:
            dst_page.write(cleaned)

        # Step 5: remove src entry from index.md.
        index = self.index()
        if index.exists():
            lines = index.raw.splitlines(keepends=True)
            filtered = [ln for ln in lines if src_slug not in ln]
            index.write("".join(filtered))

        # Step 6: delete src file.
        src.path.unlink()

        # Step 7: log.
        self.append_log("merge", f"{src_slug} → {dst_slug}")


def _to_slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text


_SOURCE_EXTENSIONS = {".pdf", ".txt", ".md", ".epub", ".docx", ".html"}


def source_to_slug(text: str) -> str:
    """Like _to_slug but strips known file extensions first.

    Ensures [Source: Mistborn.pdf] and path stem "Mistborn" resolve to the
    same slug so saved image filenames match the markers in wiki bodies.
    """
    stem = re.sub(
        r"\.(" + "|".join(e.lstrip(".") for e in _SOURCE_EXTENSIONS) + r")$",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    return _to_slug(stem)
