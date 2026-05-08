"""
CopperMind — represents a single knowledge base instance.

In Feruchemical terms: the metallic mind that stores memories.
The Archivist fills it; the Feruchemist taps it.
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.config import settings

if TYPE_CHECKING:
    from copper.core.wiki import WikiManager

MINDS_DIR = settings.minds_path


@dataclass
class CopperMindConfig:
    name: str
    topic: str
    created: str
    model: str = "default"
    linked_minds: list[str] = field(default_factory=list)
    # Per-mind LLM overrides (empty string = use global settings)
    store_provider: str = ""
    store_model: str = ""
    tap_provider: str = ""
    tap_model: str = ""
    ingest_provider: str = ""
    ingest_model: str = ""
    # Tap personality override — name of a prompt registered in copper.prompts.
    tap_personality: str = ""
    # Per-mind recursion depth cap; None means fall back to the global setting.
    max_depth: int | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.name,
            "topic": self.topic,
            "created": self.created,
            "model": self.model,
            "linked_minds": self.linked_minds,
        }
        # Only write override fields when set, to keep config.yaml clean
        if self.store_provider:
            d["store_provider"] = self.store_provider
        if self.store_model:
            d["store_model"] = self.store_model
        if self.tap_provider:
            d["tap_provider"] = self.tap_provider
        if self.tap_model:
            d["tap_model"] = self.tap_model
        if self.ingest_provider:
            d["ingest_provider"] = self.ingest_provider
        if self.ingest_model:
            d["ingest_model"] = self.ingest_model
        if self.tap_personality:
            d["tap_personality"] = self.tap_personality
        if self.max_depth is not None:
            d["max_depth"] = self.max_depth
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "CopperMindConfig":
        return cls(
            name=data["name"],
            topic=data["topic"],
            created=data["created"],
            model=data.get("model", "default"),
            linked_minds=data.get("linked_minds", []),
            store_provider=data.get("store_provider", ""),
            store_model=data.get("store_model", ""),
            tap_provider=data.get("tap_provider", ""),
            tap_model=data.get("tap_model", ""),
            ingest_provider=data.get("ingest_provider", ""),
            ingest_model=data.get("ingest_model", ""),
            tap_personality=data.get("tap_personality", ""),
            max_depth=data.get("max_depth", None),
        )


class CopperMind:
    """A single knowledge base (mentecobre)."""

    def __init__(self, path: Path):
        self.path = path
        self._config: CopperMindConfig | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def raw_dir(self) -> Path:
        return self.path / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.path / "wiki"

    @property
    def wiki(self) -> "WikiManager":
        from copper.core.wiki import WikiManager

        return WikiManager(self.wiki_dir)

    @property
    def outputs_dir(self) -> Path:
        return self.path / "outputs"

    @property
    def meta_dir(self) -> Path:
        return self.path / ".copper"

    @property
    def config_path(self) -> Path:
        return self.meta_dir / "config.yaml"

    @property
    def schema_path(self) -> Path:
        return self.meta_dir / "schema.md"

    @property
    def index_path(self) -> Path:
        return self.wiki_dir / "index.md"

    @property
    def log_path(self) -> Path:
        return self.wiki_dir / "log.md"

    @property
    def meta_summary_path(self) -> Path:
        return self.wiki_dir / "_meta.md"

    @property
    def meta_summary(self) -> str:
        if not self.meta_summary_path.exists():
            return ""
        return self.meta_summary_path.read_text()

    @property
    def config(self) -> CopperMindConfig:
        if self._config is None:
            self._config = self._load_config()
        return self._config

    def _load_config(self) -> CopperMindConfig:
        with open(self.config_path) as f:
            return CopperMindConfig.from_dict(yaml.safe_load(f))

    def save_config(self) -> None:
        with open(self.config_path, "w") as f:
            yaml.dump(self.config.to_dict(), f, default_flow_style=False, allow_unicode=True)

    def schema(self) -> str:
        if self.schema_path.exists():
            return self.schema_path.read_text()
        return ""

    def exists(self) -> bool:
        return self.path.exists() and self.config_path.exists()

    def raw_files(self) -> list[Path]:
        if not self.raw_dir.exists():
            return []
        return [f for f in self.raw_dir.rglob("*") if f.is_file() and not f.name.startswith(".")]

    def wiki_pages(self) -> list[Path]:
        if not self.wiki_dir.exists():
            return []
        return [
            f
            for f in self.wiki_dir.glob("*.md")
            if f.name not in ("index.md", "log.md") and not f.name.startswith("lint-report")
        ]

    # ------------------------------------------------------------------ #
    # Linking                                                              #
    # ------------------------------------------------------------------ #

    def _root(self) -> "CopperMind":
        """Return the root ancestor of this mind (itself if already root)."""
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def link(self, other: "CopperMind") -> None:
        """Establish a bidirectional link between this mind and another.

        Links are bidirectional and may cross tree boundaries: a child may link
        to its parent, a sibling, or any unrelated mind. All are well-defined but
        same-tree links (ancestor/descendant or sibling) emit a warning because
        they are unusual — tap already walks the hierarchy automatically.
        """
        if other.name == self.name:
            raise ValueError("Una mentecobre no puede enlazarse consigo misma.")
        if not other.exists():
            raise FileNotFoundError(f"Mentecobre '{other.name}' no encontrada.")

        # Warn if the link crosses within the same tree (unusual, but allowed).
        self_root_path = self._root().path
        other_root_path = other._root().path
        if self_root_path == other_root_path:
            self_descendants = {d.path for d in self.descendants()}
            other_descendants = {d.path for d in other.descendants()}
            if other.path in self_descendants or self.path in other_descendants:
                logger.warning(
                    f"Linking '{self.name}' to '{other.name}' which is in the same tree "
                    "(ancestor/descendant). Tap behaviour is well-defined but unusual."
                )
            elif self.parent is not None and self.parent.path == other.parent.path if other.parent else False:
                logger.warning(
                    f"Linking '{self.name}' to '{other.name}' which is in the same tree "
                    "(sibling). Tap behaviour is well-defined but unusual."
                )

        # Reload configs fresh to avoid stale state
        self._config = self._load_config()
        other._config = other._load_config()

        if other.name not in self.config.linked_minds:
            self.config.linked_minds.append(other.name)
            self.save_config()

        if self.name not in other.config.linked_minds:
            other.config.linked_minds.append(self.name)
            other.save_config()

        self.append_log("link", f"Enlazada con '{other.name}'")

    def unlink(self, other: "CopperMind") -> None:
        """Remove a bidirectional link."""
        self._config = self._load_config()
        other._config = other._load_config()

        if other.name in self.config.linked_minds:
            self.config.linked_minds.remove(other.name)
            self.save_config()

        if self.name in other.config.linked_minds:
            other.config.linked_minds.remove(self.name)
            other.save_config()

        self.append_log("unlink", f"Desenlazada de '{other.name}'")

    def linked_minds(self) -> list["CopperMind"]:
        """Return all minds linked to this one (that still exist).

        Links are bidirectional and may cross tree boundaries: a linked mind may
        be a parent, sibling, descendant, or a completely unrelated mind. Deleted
        minds are silently skipped.
        """
        result = []
        for name in self.config.linked_minds:
            try:
                result.append(CopperMind.get(name))
            except FileNotFoundError:
                pass  # Linked mind was deleted — skip silently
        return result

    def expand_with_links(self) -> list["CopperMind"]:
        """Return this mind + all linked minds, deduped."""
        seen = {self.name}
        minds = [self]
        for linked in self.linked_minds():
            if linked.name not in seen:
                seen.add(linked.name)
                minds.append(linked)
        return minds

    # ------------------------------------------------------------------ #
    # Tree navigation                                                      #
    # ------------------------------------------------------------------ #

    @property
    def parent(self) -> "CopperMind | None":
        # A child lives at <parent_path>/children/<name>
        if self.path.parent.name == "children":
            candidate = CopperMind(self.path.parent.parent)
            if candidate.exists():
                return candidate
        return None

    @property
    def is_root(self) -> bool:
        return self.parent is None

    def children(self) -> list["CopperMind"]:
        children_dir = self.path / "children"
        if not children_dir.exists():
            return []
        return [
            CopperMind(p)
            for p in sorted(children_dir.iterdir())
            if p.is_dir() and (p / ".copper" / "config.yaml").exists()
        ]

    def descendants(self) -> list["CopperMind"]:
        result = []
        for child in self.children():
            result.append(child)
            result.extend(child.descendants())
        return result

    def format_tree(self) -> str:
        """Return an ASCII tree of this mind and all its descendants.

        Example output:
            aventura/  (2 páginas)
              ├── fase-1/  (3 páginas)  _meta: "Fase 1: convocatoria..."
              └── fase-2/  (0 páginas)
        """
        lines: list[str] = []
        _tree_lines(self, lines, prefix="", is_last=True, is_root=True)
        return "\n".join(lines)

    def forge_child(self, name: str, topic: str, model: str = "default") -> "CopperMind":
        children_dir = self.path / "children"
        children_dir.mkdir(exist_ok=True)
        return CopperMind._forge_at(children_dir / name, name, topic, model)

    def append_log(self, action: str, description: str) -> None:
        date = datetime.now().strftime("%Y-%m-%d")
        entry = f"\n## [{date}] {action} | {description}\n"
        with open(self.log_path, "a") as f:
            f.write(entry)

    def stats(self) -> dict:
        raw_count = len(self.raw_files())
        wiki_count = len(self.wiki_pages())
        log_size = self.log_path.stat().st_size if self.log_path.exists() else 0
        return {
            "name": self.name,
            "topic": self.config.topic,
            "raw_sources": raw_count,
            "wiki_pages": wiki_count,
            "log_entries": log_size,
            "linked_minds": self.config.linked_minds,
        }

    # ------------------------------------------------------------------ #
    # Class-level helpers                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def get(cls, name: str) -> "CopperMind":
        """Load an existing mentecobre by name."""
        mind = cls(MINDS_DIR / name)
        if not mind.exists():
            raise FileNotFoundError(
                f"No existe ninguna mentecobre llamada '{name}'. "
                f"Usa `copper forge {name}` para crearla."
            )
        return mind

    @classmethod
    def forge(
        cls,
        name: str,
        topic: str,
        model: str = "default",
        parent: "CopperMind | None" = None,
    ) -> "CopperMind":
        """Create a new mentecobre (forge it from copper).

        If *parent* is given, the mind is placed under parent's children/.
        """
        if parent is not None:
            children_dir = parent.path / "children"
            children_dir.mkdir(exist_ok=True)
            return cls._forge_at(children_dir / name, name, topic, model)
        return cls._forge_at(MINDS_DIR / name, name, topic, model)

    @classmethod
    def _forge_at(cls, path: Path, name: str, topic: str, model: str = "default") -> "CopperMind":
        mind = cls(path)
        if mind.exists():
            raise FileExistsError(f"Ya existe una mentecobre llamada '{name}'.")

        # Create directory structure
        for d in [
            mind.raw_dir,
            mind.raw_dir / "assets",
            mind.wiki_dir,
            mind.outputs_dir,
            mind.meta_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

        # Write config
        mind._config = CopperMindConfig(
            name=name,
            topic=topic,
            created=datetime.now().isoformat(),
            model=model,
        )
        mind.save_config()

        # Write schema from template
        schema = _default_schema(name, topic)
        mind.schema_path.write_text(schema)

        # Initialize index and log
        mind.index_path.write_text(
            f"# Index — {name}\n\n*This coppermind is empty. Store knowledge with `copper store`.*\n"
        )
        mind.log_path.write_text(f"# Log — {name}\n")
        mind.append_log("forge", f"Mentecobre '{name}' creada sobre el tema: {topic}")

        return mind

    @classmethod
    def list_all(cls) -> list["CopperMind"]:
        if not MINDS_DIR.exists():
            return []
        return [
            cls(p)
            for p in sorted(MINDS_DIR.iterdir())
            if p.is_dir() and (p / ".copper" / "config.yaml").exists()
        ]

    @classmethod
    def resolve_many(cls, names: str) -> list["CopperMind"]:
        """Resolve a comma-separated list of names, or '--all'."""
        if names.strip() == "--all":
            minds = cls.list_all()
            if not minds:
                raise ValueError("No hay mentecobres. Crea una con `copper forge`.")
            return minds
        return [cls.get(n.strip()) for n in names.split(",")]


def _tree_lines(
    mind: "CopperMind",
    lines: list[str],
    prefix: str,
    is_last: bool,
    is_root: bool,
) -> None:
    pages = len(mind.wiki_pages())
    page_label = f"{pages} página{'s' if pages != 1 else ''}"
    meta = f'  _meta: "{mind.meta_summary[:55]}"' if mind.meta_summary else ""
    connector = "" if is_root else ("└── " if is_last else "├── ")
    lines.append(f"{prefix}{connector}{mind.name}/  ({page_label}){meta}")

    children = mind.children()
    child_prefix = "  " if is_root else prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(children):
        _tree_lines(child, lines, child_prefix, i == len(children) - 1, False)


def _default_schema(name: str, topic: str) -> str:
    return f"""\
# Schema — {name}

## Identity
This coppermind stores knowledge about: **{topic}**
Maintained by the Archivist (LLM). The user provides sources and asks questions.

## Architecture
- `raw/` contains original sources. **NEVER modify.**
- `wiki/` belongs to the Archivist. Compiled knowledge lives here.
- `outputs/` stores generated answers and analyses.

## Wiki conventions
- Each topic has its own `.md` in `wiki/`
- Each page starts with YAML frontmatter:
  ```
  ---
  title: [Topic name]
  created: [Date]
  last_updated: [Date]
  source_count: [Number of sources]
  status: draft | reviewed | needs_update
  ---
  ```
- Internal references: `[[page-name]]`
- Every claim cites its source: `[Source: filename]`
- Contradictions are marked explicitly:
  > CONTRADICTION: [old claim] vs [new] from [source]

## Index and Log
- `wiki/index.md` lists all pages by category with a one-line description
- `wiki/log.md` is a chronological append-only log
- Entry format: `## [YYYY-MM-DD] action | Description`

## Workflow: Store
When processing a new source:
1. Read the full document
2. Create or update a summary page in `wiki/`
3. Update `wiki/index.md`
4. Update all related entity and concept pages
5. Add backlinks from existing pages to the new content
6. Mark contradictions with existing content
7. Add entry to `wiki/log.md`
8. A source should touch between 10–15 wiki pages

## Workflow: Tap
When answering a question:
1. Read `wiki/index.md` to identify relevant pages
2. Read all relevant pages
3. Synthesize an answer with citations `[Source: page-name]`
4. If the answer reveals new connections, offer to save it in the wiki
5. Save valuable answers to `outputs/`

## Workflow: Polish
Health checks:
- Contradictions between pages
- Outdated claims
- Orphan pages with no incoming links
- Concepts mentioned but not explained
- Missing cross-references
- Claims without source citation
Output: `wiki/lint-report-[date].md` with severities 🔴🟡🔵

## Focus areas
- {topic}
"""
