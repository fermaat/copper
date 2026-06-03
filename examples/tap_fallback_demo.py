"""
FASE B1 minitest — tap degraded fallback demo.

Creates a temporary wiki with >50 pages and no retriever selection, forces
the fallback path, and prints the degraded context so the behavior can be
eyeballed.

Run:
    python examples/tap_fallback_demo.py
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from copper.core.wiki import WikiManager
from copper.workflows.tap import _degraded_context


def make_mind(wiki_dir: Path, topic: str = "demo"):
    mind = MagicMock()
    mind.name = "demo-mind"
    mind.config.topic = topic
    mind.wiki_dir = wiki_dir
    mind.meta_summary_path = wiki_dir / "_meta.md"
    return mind


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "index.md").write_text("# Índice\n")
        (wiki_dir / "log.md").write_text("# Log\n")

        wiki = WikiManager(wiki_dir)

        # Create 60 pages to exceed the default cap of 50.
        for i in range(60):
            wiki.create_page(
                f"page-{i:02d}",
                f"Página {i}",
                f"Contenido de la página número {i}. Esta es la línea principal.\n"
                f"Línea adicional A de página {i}.\n"
                f"Línea adicional B de página {i}.\n"
                f"Línea adicional C de página {i}.\n"
                f"Línea adicional D de página {i}. [Fuente: libro.md]",
            )

        # Write a _meta.md summary.
        meta_content = (
            "Este wiki contiene 60 páginas sobre un tema de prueba. "
            "Las páginas cubren contenido numérico del 0 al 59."
        )
        (wiki_dir / "_meta.md").write_text(meta_content)

        mind = make_mind(wiki_dir)
        cap = 50
        head_lines = 3

        print("=" * 60)
        print("FASE B1 — Tap degraded fallback demo")
        print("=" * 60)
        print(f"\nWiki pages: {len(wiki.all_pages())} (cap: {cap})")
        print(f"_meta.md: {mind.meta_summary_path.exists()}")
        print(f"Head lines per page: {head_lines}")
        print("\nBuilding degraded context...")

        context = _degraded_context(mind, wiki, head_lines=head_lines, cap=cap)

        lines = context.splitlines()
        print(f"\nDegraded context: {len(lines)} lines total")
        print("\nFirst 15 lines:")
        for line in lines[:15]:
            print(f"  {line}")
        print("  ...")

        # Verify _meta content is included.
        assert "Este wiki contiene" in context, "FALLO: _meta no está en el contexto"
        assert "page-00" in context, "FALLO: primera página no está"
        assert "page-49" in context, "FALLO: última página del cap no está"
        assert "page-50" not in context, "FALLO: página 50 debería estar fuera del cap"

        print("\n✓ _meta.md incluido en el contexto")
        print(f"✓ Primeras {cap} páginas incluidas (page-00..page-49)")
        print("✓ Páginas fuera del cap excluidas (page-50..page-59)")
        print("\nFin del demo FASE B1.")


if __name__ == "__main__":
    main()
