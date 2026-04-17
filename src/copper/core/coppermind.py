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

from copper.config import settings

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
            f for f in self.wiki_dir.glob("*.md")
            if f.name not in ("index.md", "log.md") and not f.name.startswith("lint-report")
        ]

    # ------------------------------------------------------------------ #
    # Linking                                                              #
    # ------------------------------------------------------------------ #

    def link(self, other: "CopperMind") -> None:
        """Establish a bidirectional link between this mind and another."""
        if other.name == self.name:
            raise ValueError("Una mentecobre no puede enlazarse consigo misma.")
        if not other.exists():
            raise FileNotFoundError(f"Mentecobre '{other.name}' no encontrada.")

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
        """Return all minds linked to this one (that still exist)."""
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
    def forge(cls, name: str, topic: str, model: str = "default") -> "CopperMind":
        """Create a new mentecobre (forge it from copper)."""
        mind = cls(MINDS_DIR / name)
        if mind.exists():
            raise FileExistsError(f"Ya existe una mentecobre llamada '{name}'.")

        # Create directory structure
        for d in [mind.raw_dir, mind.raw_dir / "assets", mind.wiki_dir,
                  mind.outputs_dir, mind.meta_dir]:
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
        mind.index_path.write_text(f"# Índice — {name}\n\n*La mentecobre está vacía. Almacena conocimiento con `copper store`.*\n")
        mind.log_path.write_text(f"# Log — {name}\n")
        mind.append_log("forge", f"Mentecobre '{name}' creada sobre el tema: {topic}")

        return mind

    @classmethod
    def list_all(cls) -> list["CopperMind"]:
        if not MINDS_DIR.exists():
            return []
        return [
            cls(p) for p in sorted(MINDS_DIR.iterdir())
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


def _default_schema(name: str, topic: str) -> str:
    return f"""\
# Schema — {name}

## Identidad
Esta mentecobre almacena conocimiento sobre: **{topic}**
Mantenida por el Archivista (LLM). El usuario aporta fuentes y hace preguntas.

## Arquitectura
- `raw/` contiene fuentes originales. **NUNCA modificar.**
- `wiki/` es propiedad del Archivista. Aquí se compila el conocimiento.
- `outputs/` almacena respuestas y análisis generados.

## Convenciones del wiki
- Cada tema tiene su propio `.md` en `wiki/`
- Cada página empieza con frontmatter YAML:
  ```
  ---
  title: [Nombre del tema]
  created: [Fecha]
  last_updated: [Fecha]
  source_count: [Número de fuentes]
  status: draft | reviewed | needs_update
  ---
  ```
- Referencias internas: `[[nombre-de-pagina]]`
- Cada afirmación cita su fuente: `[Fuente: nombre-fichero]`
- Contradicciones se marcan explícitamente:
  > CONTRADICCIÓN: [afirmación vieja] vs [nueva] de [fuente]

## Index y Log
- `wiki/index.md` lista todas las páginas por categoría con descripción de una línea
- `wiki/log.md` es registro cronológico append-only
- Formato de entrada: `## [YYYY-MM-DD] acción | Descripción`

## Workflow: Almacenar (store)
Al procesar una nueva fuente:
1. Leer el documento completo
2. Crear o actualizar página resumen en `wiki/`
3. Actualizar `wiki/index.md`
4. Actualizar todas las páginas de entidades y conceptos relacionados
5. Añadir backlinks desde páginas existentes al nuevo contenido
6. Marcar contradicciones con contenido existente
7. Añadir entrada en `wiki/log.md`
8. Una fuente debe tocar entre 10-15 páginas del wiki

## Workflow: Extraer (tap)
Al responder una pregunta:
1. Leer `wiki/index.md` para identificar páginas relevantes
2. Leer todas las páginas relevantes
3. Sintetizar respuesta con citas `[Fuente: nombre-pagina]`
4. Si la respuesta revela nuevas conexiones, ofrecerse a guardarla en el wiki
5. Guardar respuestas valiosas en `outputs/`

## Workflow: Pulir (polish)
Comprobaciones de salud:
- Contradicciones entre páginas
- Afirmaciones obsoletas
- Páginas huérfanas sin enlaces entrantes
- Conceptos mencionados pero no explicados
- Referencias cruzadas ausentes
- Afirmaciones sin citar fuente
Salida: `wiki/lint-report-[fecha].md` con severidades 🔴🟡🔵

## Áreas de enfoque
- {topic}
"""
