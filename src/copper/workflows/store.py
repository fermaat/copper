"""
Store workflow — fill the coppermind with knowledge.

The Archivist reads a source, extracts knowledge, and weaves it
into the existing wiki. A single source should touch 10-15 pages.
"""

from __future__ import annotations

from pathlib import Path

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.llm.base import LLMBase, Message


STORE_SYSTEM = """\
Eres el Archivista de una mentecobre. Tu misión es mantener un wiki estructurado en markdown.
Lees fuentes, extraes conocimiento y lo integras en el wiki existente.
Sigues estrictamente el schema de la mentecobre.
Nunca modificas los ficheros de raw/. Solo escribes en wiki/.
"""


class StoreWorkflow:
    """Processes a source file and updates the wiki."""

    def __init__(self, mind: CopperMind, llm: LLMBase):
        self.mind = mind
        self.llm = llm
        self.wiki = WikiManager(mind.wiki_dir)

    def run(self, source_path: Path) -> StoreResult:
        if not source_path.exists():
            raise FileNotFoundError(f"Fuente no encontrada: {source_path}")

        # Copy to raw/ if not already there, so the source is always preserved
        raw_path = self.mind.raw_dir / source_path.name
        if source_path.resolve() != raw_path.resolve():
            import shutil
            shutil.copy2(source_path, raw_path)

        source_text = raw_path.read_text()
        source_name = raw_path.name
        index_content = self.wiki.read_index()
        schema = self.mind.schema()

        # Ask the LLM to process the source and return wiki updates
        prompt = _build_store_prompt(schema, source_name, source_text, index_content)
        messages = [
            Message(role="system", content=STORE_SYSTEM),
            Message(role="user", content=prompt),
        ]
        response = self.llm.complete(messages)

        # Parse the LLM response into wiki operations
        pages_written = _apply_wiki_updates(response.text, source_name, self.wiki)

        # Log
        self.mind.append_log(
            "store",
            f"Fuente '{source_name}' almacenada → {len(pages_written)} páginas actualizadas",
        )

        return StoreResult(
            source=source_name,
            pages_written=pages_written,
            tokens_used=response.tokens_used,
        )


def _build_store_prompt(schema: str, source_name: str, source_text: str, index: str) -> str:
    return f"""\
## Schema de esta mentecobre
{schema}

## Índice actual del wiki
{index}

## Nueva fuente a almacenar: {source_name}
{source_text}

---

Procesa esta fuente siguiendo el workflow de almacenamiento del schema.
Devuelve las actualizaciones del wiki en el siguiente formato XML:

<wiki_updates>
  <page slug="nombre-de-pagina" title="Título de la Página" action="create|update">
    <content>
    Contenido completo de la página (sin frontmatter, lo añado yo).
    </content>
  </page>
  ... (repite por cada página a crear o actualizar)
  <index>
    Contenido completo del nuevo index.md
  </index>
</wiki_updates>

Importante:
- Incluye entre 3 y 15 páginas
- El slug debe ser kebab-case y descriptivo
- Añade [[backlinks]] donde corresponda
- Cita fuentes como [Fuente: {source_name}]
- Marca contradicciones si las hay
"""


def _apply_wiki_updates(llm_output: str, source_name: str, wiki: WikiManager) -> list[str]:
    """Parse <wiki_updates> XML from LLM output and write pages."""
    import re

    pages_written: list[str] = []

    # Extract page blocks
    page_pattern = re.compile(
        r'<page\s+slug="([^"]+)"\s+title="([^"]+)"\s+action="([^"]+)"[^>]*>\s*<content>(.*?)</content>\s*</page>',
        re.DOTALL,
    )
    for m in page_pattern.finditer(llm_output):
        slug, title, action, content = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        wiki.upsert_page(slug=slug, title=title, body=content, bump_source_count=(action == "update"))
        pages_written.append(slug)

    # Extract index update
    index_match = re.search(r"<index>(.*?)</index>", llm_output, re.DOTALL)
    if index_match:
        wiki.update_index(index_match.group(1).strip())

    # Fallback: if no XML found, create a basic summary page
    if not pages_written:
        slug = source_name.replace(".", "-").lower()
        wiki.upsert_page(
            slug=slug,
            title=f"Resumen: {source_name}",
            body=llm_output,
        )
        pages_written.append(slug)

    return pages_written


class StoreResult:
    def __init__(self, source: str, pages_written: list[str], tokens_used: int):
        self.source = source
        self.pages_written = pages_written
        self.tokens_used = tokens_used

    def __repr__(self) -> str:
        return f"StoreResult(source={self.source!r}, pages={len(self.pages_written)}, tokens={self.tokens_used})"
