"""
Store workflow — fill the coppermind with knowledge.

The Archivist reads a source, extracts knowledge, and weaves it
into the existing wiki. Large sources are automatically split into
chunks so they fit within the model's context window.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core_utils.logger import logger

from copper.config import settings
from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.ingest.registry import default_registry
from copper.llm.base import LLMBase, Message

if TYPE_CHECKING:
    from copper.ingest.image_describer import ImageDescriber


# Maximum characters sent to the LLM per chunk. Override via COPPER_STORE_MAX_CHUNK_CHARS.
MAX_CHUNK_CHARS = settings.copper_store_max_chunk_chars

STORE_SYSTEM = """\
Eres el Archivista de una mentecobre. Tu misión es mantener un wiki estructurado en markdown.
Lees fuentes, extraes conocimiento y lo integras en el wiki existente.
Sigues estrictamente el schema de la mentecobre.
Nunca modificas los ficheros de raw/. Solo escribes en wiki/.
"""


class StoreWorkflow:
    """Processes a source file and updates the wiki."""

    def __init__(
        self,
        mind: CopperMind,
        llm: LLMBase,
        image_describer: "ImageDescriber | None" = None,
    ):
        self.mind = mind
        self.llm = llm
        self.image_describer = image_describer
        self.wiki = WikiManager(mind.wiki_dir)

    def run(self, source_path: Path) -> StoreResult:
        if not source_path.exists():
            raise FileNotFoundError(f"Fuente no encontrada: {source_path}")

        # Copy to raw/ if not already there
        raw_path = self.mind.raw_dir / source_path.name
        if source_path.resolve() != raw_path.resolve():
            import shutil

            shutil.copy2(source_path, raw_path)

        source_name = raw_path.name
        logger.info(f"[store] Extracting text from '{source_name}'...")

        registry = default_registry()
        chunks = registry.to_chunks(
            raw_path,
            MAX_CHUNK_CHARS,
            llm=self.llm,
            image_describer=self.image_describer,
        )

        char_count = sum(len(c) for c in chunks)
        # Source is smelted into ingots — manageable chunks that fit the forge.
        total_ingots = len(chunks)
        logger.info(
            f"[store] '{source_name}' → {char_count:,} chars smelted into {total_ingots} ingot(s)"
        )

        schema = self.mind.schema()
        all_pages: list[str] = []
        total_tokens = 0
        total_cost = 0.0

        for i, chunk in enumerate(chunks, 1):
            ingot_label = f"ingot {i}/{total_ingots}" if total_ingots > 1 else None
            if ingot_label:
                logger.info(f"[store] Forging {ingot_label} ({len(chunk):,} chars)...")

            # Refresh index each iteration so the LLM sees pages created by previous ingots
            index_content = self.wiki.read_index()
            existing_slugs = [p.name for p in self.wiki.all_pages()]
            prompt = _build_store_prompt(
                schema,
                source_name,
                chunk,
                index_content,
                chunk_label=ingot_label,
                existing_slugs=existing_slugs,
            )

            logger.info(f"[store] Sending to LLM ({len(prompt):,} chars in prompt)...")
            messages = [
                Message(role="system", content=STORE_SYSTEM),
                Message(role="user", content=prompt),
            ]
            response = self.llm.complete(messages)
            total_tokens += response.tokens_used
            total_cost += response.cost_usd
            logger.info(f"[store] LLM responded ({response.tokens_used} tokens)")

            pages = _apply_wiki_updates(response.text, source_name, self.wiki)
            all_pages.extend(pages)
            logger.info(
                f"[store] Ingot {i}/{total_ingots} forged: {len(pages)} page(s) written → {pages}"
            )

        self.mind.append_log(
            "store",
            f"Fuente '{source_name}' almacenada → {len(all_pages)} páginas actualizadas",
        )
        logger.info(
            f"[store] Done: '{source_name}' → {len(all_pages)} pages, {total_tokens} tokens"
        )

        # After multi-ingot forging, run polish to consolidate duplicates and fix gaps
        if total_ingots > 1:
            logger.info(f"[store] Running consolidation polish ({len(all_pages)} wiki pages)...")
            from copper.workflows.polish import PolishWorkflow

            polish_result = PolishWorkflow(self.mind, self.llm).run()
            total_tokens += polish_result.tokens_used
            total_cost += polish_result.cost_usd
            logger.info(
                f"[store] Polish done → {len(polish_result.structural_issues)} structural issues, "
                f"report at {polish_result.report_path.name}"
            )

        return StoreResult(
            source=source_name,
            pages_written=all_pages,
            tokens_used=total_tokens,
            cost_usd=total_cost,
        )


def _build_store_prompt(
    schema: str,
    source_name: str,
    source_text: str,
    index: str,
    chunk_label: str | None = None,
    existing_slugs: list[str] | None = None,
) -> str:
    chunk_note = (
        f"\n> Nota: este texto es la {chunk_label} del documento. Integra el conocimiento con lo ya existente en el wiki.\n"
        if chunk_label
        else ""
    )

    update_note = ""
    if existing_slugs and chunk_label:
        slugs_str = ", ".join(existing_slugs)
        update_note = (
            f"\n## Páginas ya existentes en el wiki ({len(existing_slugs)} total)\n{slugs_str}\n\n"
            "> IMPORTANTE: Solo toca las páginas DIRECTAMENTE relevantes para este fragmento. "
            'Actualiza (action="update") si ya existe; crea (action="create") si no. '
            "No toques páginas no relacionadas con este fragmento.\n"
        )

    return f"""\
## Schema de esta mentecobre
{schema}

## Índice actual del wiki
{index}
{update_note}
## Nueva fuente a almacenar: {source_name}{chunk_note}
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
- Incluye entre 1 y 5 páginas (solo las más relevantes para este fragmento)
- El slug debe ser kebab-case y descriptivo
- Añade [[backlinks]] donde corresponda
- Cita fuentes como [Fuente: {source_name}]
- Marca contradicciones si las hay
- PRESERVA los marcadores `[Visual on page N: ...]` tal cual cuando aporten
  información visual útil (colores, anatomía, escenas, diagramas). Son
  descripciones de figuras del documento original y añaden conocimiento
  que no está en el texto narrativo.
"""


def _apply_wiki_updates(llm_output: str, source_name: str, wiki: WikiManager) -> list[str]:
    """Parse <wiki_updates> XML from LLM output and write pages."""
    import re

    pages_written: list[str] = []

    page_pattern = re.compile(
        r'<page\s+slug="([^"]+)"\s+title="([^"]+)"\s+action="([^"]+)"[^>]*>\s*<content>(.*?)</content>\s*</page>',
        re.DOTALL,
    )
    for m in page_pattern.finditer(llm_output):
        slug, title, action, content = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        wiki.upsert_page(
            slug=slug, title=title, body=content, bump_source_count=(action == "update")
        )
        pages_written.append(slug)

    index_match = re.search(r"<index>(.*?)</index>", llm_output, re.DOTALL)
    if index_match:
        wiki.update_index(index_match.group(1).strip())

    if not pages_written:
        logger.warning(f"[store] LLM returned no valid XML — creating summary page")
        slug = source_name.replace(".", "-").lower()
        wiki.upsert_page(slug=slug, title=f"Resumen: {source_name}", body=llm_output)
        pages_written.append(slug)

    return pages_written


class StoreResult:
    def __init__(
        self, source: str, pages_written: list[str], tokens_used: int, cost_usd: float = 0.0
    ):
        self.source = source
        self.pages_written = pages_written
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return f"StoreResult(source={self.source!r}, pages={len(self.pages_written)}, tokens={self.tokens_used}, cost=${self.cost_usd:.6f})"
