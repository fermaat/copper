"""
Polish normalize — end-to-end demo (FASE A1 + A2 + A3).

Builds a temporary coppermind with near-duplicate slug pages, runs the full
normalize flow (detect → LLM proposal → merge), and prints the before/after
state of the wiki.

Run:
    python examples/polish_normalize.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from copper.core.slug_normalize import find_self_links, find_slug_clusters, propose_merges
from copper.core.wiki import WikiManager
from copper.llm.mock import MockLLM


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def _print_wiki_state(wiki: WikiManager, label: str) -> None:
    pages = wiki.all_pages()
    print(f"\n[{label}] — {len(pages)} página(s):")
    for page in pages:
        title = page.frontmatter.get("title", page.name)
        src_count = page.frontmatter.get("source_count", "?")
        print(f"  • {page.name}  (title={title!r}, source_count={src_count})")
        body_preview = page.body.strip().splitlines()[:2]
        for line in body_preview:
            print(f"      {line}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()

        # Minimal index + log
        (wiki_dir / "index.md").write_text(
            "# Índice\n\n"
            "- [[masting]] — Habilidad alomántica (errata de misting)\n"
            "- [[misting]] — Uso de un único metal\n"
            "- [[allomancy]] — Magia alomántica\n"
        )
        (wiki_dir / "log.md").write_text("# Log\n")

        wiki = WikiManager(wiki_dir)

        wiki.create_page(
            "masting",
            "Masting",
            "Una habilidad alomántica mal escrita. Ver [[masting]]. [Fuente: libro.md]",
            source_count=1,
        )
        wiki.create_page(
            "misting",
            "Misting",
            "Un Misting usa un solo metal. Ver [[masting]] como referencia. [Fuente: libro.md]",
            source_count=2,
        )
        wiki.create_page(
            "allomancy",
            "Allomancy",
            "La alomancia es el arte de quemar metales. [Fuente: libro.md]",
        )

        # ── FASE A1 — Structural detection ───────────────────────────────── #
        _print_section("FASE A1 — Detección estructural")
        _print_wiki_state(wiki, "ANTES")

        threshold = 0.85
        slugs = [p.name for p in wiki.all_pages()]
        clusters = find_slug_clusters(slugs, threshold)
        self_links = find_self_links(wiki.all_pages())

        print(f"\nClusters (umbral={threshold}):")
        for c in clusters:
            print(f"  🟠 {c.slugs}  ({c.reason})")

        print("\nSelf-links:")
        for sl in self_links:
            print(f"  🟠 {sl.slug}")

        # ── FASE A2 — LLM merge proposals ────────────────────────────────── #
        _print_section("FASE A2 — Propuestas LLM")

        # MockLLM configured to confirm the masting→misting merge.
        mock_llm = MockLLM(
            [
                '<merge canonical="misting">'
                "<duplicate>masting</duplicate>"
                "<reason>Errata de 'Misting' — misma habilidad</reason>"
                "</merge>",
                "Meta summary actualizado.",  # for regenerate_meta
            ]
        )

        proposals = propose_merges(wiki, mock_llm, threshold=threshold)
        print(f"\nPropuestas de fusión: {len(proposals)}")
        for p in proposals:
            print(f"  🟠 {p.duplicates} → {p.canonical!r}  ({p.reason})")

        # ── FASE A3 — Execute merge ───────────────────────────────────────── #
        _print_section("FASE A3 — Aplicar fusión")

        for proposal in proposals:
            for dup in proposal.duplicates:
                print(f"\nFusionando: {dup} → {proposal.canonical}")
                wiki.merge_page(dup, proposal.canonical)

        _print_wiki_state(wiki, "DESPUÉS")

        # Verify the merged page has both bodies and no self-link.
        merged = wiki.page("misting")
        print(f"\nPágina fusionada ({merged.name!r}):")
        print(f"  source_count = {merged.frontmatter.get('source_count')}")
        has_self_link = "[[misting]]" in merged.body
        print(f"  self-link eliminado = {not has_self_link}")
        has_src_body = "mal escrita" in merged.body
        print(f"  cuerpo de masting absorbido = {has_src_body}")

        index_content = wiki.read_index()
        print(f"  'masting' en índice = {'masting' in index_content}")

        # ── FASE A1 — After merge, no more clusters ───────────────────────── #
        slugs_after = [p.name for p in wiki.all_pages()]
        clusters_after = find_slug_clusters(slugs_after, threshold)
        print(f"\nClusters tras fusión: {len(clusters_after)}  (esperado: 0)")

        print("\nFin del demo polish_normalize.")


if __name__ == "__main__":
    main()
