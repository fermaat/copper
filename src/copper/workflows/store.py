"""
Store workflow — fill the coppermind with knowledge.

The Archivist reads a source, extracts knowledge, and weaves it
into the existing wiki. Large sources are automatically split into
chunks so they fit within the model's context window.
"""

from __future__ import annotations

from pathlib import Path

from core_utils.logger import logger

from copper.core.coppermind import CopperMind
from copper.core.wiki import WikiManager
from copper.ingest.registry import default_registry
from copper.llm.base import LLMBase, Message


# Maximum characters sent to the LLM in a single call.
# ~15 000 chars ≈ 3 750 tokens; leaves room for schema + index overhead.
# Override with COPPER_MAX_CHUNK_CHARS env var (via Settings) if needed.
MAX_CHUNK_CHARS = 15_000

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

        # Copy to raw/ if not already there
        raw_path = self.mind.raw_dir / source_path.name
        if source_path.resolve() != raw_path.resolve():
            import shutil
            shutil.copy2(source_path, raw_path)

        source_name = raw_path.name
        logger.info(f"[store] Extracting text from '{source_name}'...")

        registry = default_registry()
        chunks = registry.to_chunks(raw_path, MAX_CHUNK_CHARS, llm=self.llm)

        char_count = sum(len(c) for c in chunks)
        logger.info(f"[store] '{source_name}' → {char_count:,} chars in {len(chunks)} chunk(s)")
        total_chunks = len(chunks)

        schema = self.mind.schema()
        all_pages: list[str] = []
        total_tokens = 0
        total_cost = 0.0

        for i, chunk in enumerate(chunks, 1):
            chunk_label = f"parte {i}/{total_chunks}" if total_chunks > 1 else None
            if chunk_label:
                logger.info(f"[store] Processing chunk {i}/{total_chunks} ({len(chunk):,} chars)...")

            # Refresh index each iteration so the LLM sees pages created by previous chunks
            index_content = self.wiki.read_index()
            existing_slugs = [p.name for p in self.wiki.all_pages()]
            prompt = _build_store_prompt(
                schema, source_name, chunk, index_content,
                chunk_label=chunk_label,
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
            logger.info(f"[store] Chunk {i}/{total_chunks}: {len(pages)} page(s) written → {pages}")

        self.mind.append_log(
            "store",
            f"Fuente '{source_name}' almacenada → {len(all_pages)} páginas actualizadas",
        )
        logger.info(f"[store] Done: '{source_name}' → {len(all_pages)} pages, {total_tokens} tokens")

        # After multi-chunk ingestion, run polish to consolidate duplicates and fix gaps
        if total_chunks > 1:
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
    chunk_note = f"\n> Nota: este texto es la {chunk_label} del documento. Integra el conocimiento con lo ya existente en el wiki.\n" if chunk_label else ""

    update_note = ""
    if existing_slugs and chunk_label:
        slugs_list = "\n".join(f"- {s}" for s in existing_slugs)
        update_note = (
            f"\n## Páginas ya existentes en el wiki\n{slugs_list}\n\n"
            "> IMPORTANTE: Si el contenido de este fragmento es relevante para una página ya existente, "
            "ACTUALÍZALA (action=\"update\") en lugar de crear una página nueva. "
            "Solo crea páginas nuevas para conceptos que no tengan página equivalente.\n"
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

    page_pattern = re.compile(
        r'<page\s+slug="([^"]+)"\s+title="([^"]+)"\s+action="([^"]+)"[^>]*>\s*<content>(.*?)</content>\s*</page>',
        re.DOTALL,
    )
    for m in page_pattern.finditer(llm_output):
        slug, title, action, content = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        wiki.upsert_page(slug=slug, title=title, body=content, bump_source_count=(action == "update"))
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
    def __init__(self, source: str, pages_written: list[str], tokens_used: int, cost_usd: float = 0.0):
        self.source = source
        self.pages_written = pages_written
        self.tokens_used = tokens_used
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return f"StoreResult(source={self.source!r}, pages={len(self.pages_written)}, tokens={self.tokens_used}, cost=${self.cost_usd:.6f})"
