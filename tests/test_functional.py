"""
Functional tests — one per phase, showing the feature end-to-end.

Run with:
    pdm run pytest tests/test_functional.py -v -s

Each test prints a human-readable trace so you can verify the feature works
as intended, not just that assertions pass.
"""

import re
import pytest


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


def _parse_descend(text: str) -> list[str]:
    """Parse child names from a scanner <descend> response."""
    match = re.search(r"<descend>(.*?)</descend>", text, re.DOTALL)
    if not match:
        return []
    return [n.strip() for n in match.group(1).splitlines() if n.strip()]


# ------------------------------------------------------------------ #
# Phase 1 — Recursive CopperMind data model                          #
# ------------------------------------------------------------------ #


def test_phase1_tree_navigation(tmp_minds_dir):
    """
    A parent coppermind can forge children, discover them, and navigate
    the tree in both directions. A 3-level tree proves arbitrary depth.
    """
    from copper.core.coppermind import CopperMind

    print("\n=== Phase 1 — Árbol jerárquico de copperminds ===\n")

    aventura = CopperMind.forge("aventura", "Adventure module")
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    fase2 = aventura.forge_child("fase-2", "El Bosque Maldito")
    enc = fase1.forge_child("encuentro-inicial", "El encuentro en la taberna")

    fase1.meta_summary_path.write_text(
        "Fase 1: los héroes son convocados por el rey y viajan a Luthadel."
    )
    fase2.meta_summary_path.write_text(
        "Fase 2: el bosque entre las ciudades está maldito y debe ser cruzado."
    )

    print(aventura.format_tree())
    all_desc = [d.name for d in aventura.descendants()]
    print(f"\naventura.descendants() → {all_desc}")
    print(f"enc.parent.parent.name  → '{enc.parent.parent.name}'")  # type: ignore[union-attr]
    print(f"enc.meta_summary        → '{enc.meta_summary}' (nunca escrito)")

    assert aventura.is_root
    assert not fase1.is_root
    assert fase1.parent.name == "aventura"  # type: ignore[union-attr]
    assert enc.parent.parent.name == "aventura"  # type: ignore[union-attr]
    assert {c.name for c in aventura.children()} == {"fase-1", "fase-2"}
    assert {d.name for d in aventura.descendants()} == {"fase-1", "fase-2", "encuentro-inicial"}
    assert "Luthadel" in fase1.meta_summary
    assert enc.meta_summary == ""

    print("\n✓ Árbol de 3 niveles, navegación bidireccional, meta_summary OK")


# ------------------------------------------------------------------ #
# Phase 2 — Tap with progressive disclosure                          #
# ------------------------------------------------------------------ #


def test_phase2_hierarchical_tap(tmp_minds_dir):
    """
    Tap on a 2-level coppermind routes the question through a scanner first.
    The scanner picks which sub-copperminds to search; the answer comes from there.
    A second query shows the 'parent only' path (no child descent).
    """
    from copper.core.coppermind import CopperMind
    from copper.core.wiki import WikiManager
    from copper.llm.mock import MockLLM
    from copper.workflows.tap import TapWorkflow

    print("\n=== Phase 2 — Tap jerárquico con scanner ===\n")

    # Build tree
    aventura = CopperMind.forge("aventura", "Adventure module")
    WikiManager(aventura.wiki_dir).create_page(
        "overview",
        "Overview",
        "Three-phase adventure. Heroes: Vin, Elend, Sazed. [Fuente: overview]",
    )
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    fase1.meta_summary_path.write_text("Fase 1: los héroes son convocados al palacio.")
    WikiManager(fase1.wiki_dir).create_page(
        "convocatoria",
        "La Convocatoria",
        "The king summons heroes to Luthadel before the new moon. [Fuente: module]",
    )
    fase2 = aventura.forge_child("fase-2", "El Bosque")
    fase2.meta_summary_path.write_text("Fase 2: cruce del bosque maldito.")
    WikiManager(fase2.wiki_dir).create_page(
        "bosque",
        "El Bosque",
        "The cursed forest lies between the two cities. [Fuente: module]",
    )

    print(aventura.format_tree())

    # --- Query 1: scanner routes to fase-1 ---
    q1 = "¿Qué deben hacer los héroes en la fase 1?"
    print(f'\nPregunta 1: "{q1}"')

    llm1 = MockLLM(
        [
            "<descend>\nfase-1\n</descend>",  # scanner
            "PAGE: convocatoria",  # retriever
            "Los héroes deben viajar a Luthadel antes de la luna nueva. [Source: convocatoria]",  # answer
        ]
    )
    result1 = TapWorkflow([aventura], llm1).run(q1)

    chosen1 = _parse_descend(llm1._responses[0])
    print(f"  [Scanner] eligió:    {chosen1}")
    print(f"  [Calls total]:       {llm1._call_count}  (scanner + retriever + respuesta)")
    print(f"  [Respuesta]:         {result1.answer}")

    assert chosen1 == ["fase-1"], f"Expected ['fase-1'], got {chosen1}"
    assert llm1._call_count == 3
    assert "Luthadel" in result1.answer

    # --- Query 2: scanner says parent context is enough ---
    q2 = "¿Quiénes son los personajes principales?"
    print(f'\nPregunta 2: "{q2}"')

    llm2 = MockLLM(
        [
            "<descend>\n</descend>",  # scanner → parent only
            "Los personajes son Vin, Elend y Sazed. [Source: overview]",  # answer
        ]
    )
    result2 = TapWorkflow([aventura], llm2).run(q2)

    chosen2 = _parse_descend(llm2._responses[0])
    print(f"  [Scanner] eligió:    {chosen2 or '(solo padre — sin descenso a hijos)'}")
    print(f"  [Calls total]:       {llm2._call_count}  (scanner + respuesta, sin retriever)")
    print(f"  [Respuesta]:         {result2.answer}")

    assert chosen2 == []
    assert llm2._call_count == 2
    assert "Vin" in result2.answer

    print("\n✓ Scanner enrutó a fase-1; camino 'solo padre' también funciona")


# ------------------------------------------------------------------ #
# Phase 3 — Recursive Polish                                         #
# ------------------------------------------------------------------ #


def test_phase3_recursive_polish(tmp_minds_dir):
    """
    Polishing a parent polishes all children first (bottom-up), then the
    parent itself. Every mind gets a fresh _meta.md after its pass.
    """
    from copper.core.coppermind import CopperMind
    from copper.core.wiki import WikiManager
    from copper.llm.mock import MockLLM
    from copper.workflows.polish import PolishWorkflow

    print("\n=== Phase 3 — Polish recursivo bottom-up ===\n")

    aventura = CopperMind.forge("aventura", "Adventure module")
    WikiManager(aventura.wiki_dir).create_page(
        "overview",
        "Overview",
        "Three-phase adventure. Heroes: Vin, Elend, Sazed. [Fuente: overview]",
    )
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    WikiManager(fase1.wiki_dir).create_page(
        "convocatoria",
        "La Convocatoria",
        "Heroes summoned to Luthadel before the new moon. [Fuente: module]",
    )
    fase2 = aventura.forge_child("fase-2", "El Bosque")
    WikiManager(fase2.wiki_dir).create_page(
        "bosque",
        "El Bosque",
        "Cursed forest between the two cities. Strange creatures. [Fuente: module]",
    )

    print("Árbol antes del polish:")
    print(aventura.format_tree())

    # Even calls = archivist lint report, odd calls = _meta summary
    llm = MockLLM(
        [
            "# Informe de salud\n\n🔵 Wiki en buen estado.",
            "Resumen generado para el scanner.",
        ]
    )

    result = PolishWorkflow(aventura, llm).run()

    print("\nDespués del polish:")
    print(aventura.format_tree())

    print(f"\n[Calls total]: {llm._call_count}  (3 mentes × 2 llamadas cada una)")
    print(f"[children en result]: {[r.mind_name for r in result.children_results]}")

    assert fase1.meta_summary_path.exists()
    assert fase2.meta_summary_path.exists()
    assert aventura.meta_summary_path.exists()
    assert "Resumen generado" in fase1.meta_summary
    assert any(fase1.wiki_dir.glob("lint-report-*.md"))
    assert any(fase2.wiki_dir.glob("lint-report-*.md"))
    assert any(aventura.wiki_dir.glob("lint-report-*.md"))
    assert len(result.children_results) == 2
    assert llm._call_count == 6

    print("\n✓ Bottom-up: hijos primero, _meta.md fresco en todos los nodos")


# ------------------------------------------------------------------ #
# Phase 4 — Store with routing                                       #
# ------------------------------------------------------------------ #


def test_phase4_store_routing(tmp_minds_dir, tmp_path):
    """
    Storing content in a hierarchical coppermind first asks a router LLM
    where the content belongs. Content lands in the right child (or back at
    the parent for cross-cutting material), and a new child can be forged
    on the fly if the router decides so.
    """
    from copper.core.coppermind import CopperMind
    from copper.llm.mock import MockLLM
    from copper.workflows.store import StoreWorkflow

    def wiki_xml(slug):
        return (
            "<wiki_updates>"
            f'<page slug="{slug}" title="{slug.title()}" action="create">'
            f"<content>Contenido sobre {slug}. [Fuente: src]</content>"
            "</page>"
            f"<index># Índice\n\n- [[{slug}]]</index>"
            "</wiki_updates>"
        )

    print("\n=== Phase 4 — Store con enrutamiento ===\n")

    aventura = CopperMind.forge("aventura", "adventure module")
    fase1 = aventura.forge_child("fase-1", "La Convocatoria")
    fase1.meta_summary_path.write_text("Fase 1: convocatoria y viaje a Luthadel.")
    fase2 = aventura.forge_child("fase-2", "El Bosque")
    fase2.meta_summary_path.write_text("Fase 2: cruce del bosque maldito.")

    print("Árbol inicial:")
    print(aventura.format_tree())

    # --- Source 1: router picks fase-1 ---
    src1 = tmp_path / "convocatoria.md"
    src1.write_text("# La Convocatoria\n\nEl rey convoca a los héroes a Luthadel.\n")

    llm1 = MockLLM(["<route>fase-1</route>", wiki_xml("convocatoria")])
    result1 = StoreWorkflow(aventura, llm1).run(src1)

    print(f"\nFuente 1: '{src1.name}'")
    print(f"  [Router] → '{result1.routed_to}'")
    print(f"  [Calls]  {llm1._call_count}  (router + archivist)")
    print(f"  [Páginas escritas] {result1.pages_written} en '{result1.routed_to}'")

    assert result1.routed_to == "fase-1"
    assert llm1._call_count == 2
    assert len(fase1.wiki_pages()) > 0
    assert len(aventura.wiki_pages()) == 0

    # --- Source 2: router says parent (cross-cutting content) ---
    src2 = tmp_path / "personajes.md"
    src2.write_text("# Personajes\n\nVin, Elend y Sazed aparecen en todas las fases.\n")

    llm2 = MockLLM(["<route>parent</route>", wiki_xml("personajes")])
    result2 = StoreWorkflow(aventura, llm2).run(src2)

    print(f"\nFuente 2: '{src2.name}'")
    print(f"  [Router] → '{result2.routed_to or 'padre (contenido transversal)'}'")
    print(f"  [Calls]  {llm2._call_count}")
    print(f"  [Páginas escritas] {result2.pages_written} en 'aventura'")

    assert result2.routed_to is None
    assert llm2._call_count == 2
    assert len(aventura.wiki_pages()) > 0

    # --- Source 3: router proposes a new child ---
    src3 = tmp_path / "epilogo.md"
    src3.write_text(
        "# Epílogo\n\nTras derrotar al Lord Ruler, los héroes reconstruyen el Imperio.\n"
    )

    llm3 = MockLLM(
        [
            "<route>new_child:epilogo</route>\n<topic>Epílogo y reconstrucción del Imperio</topic>",
            wiki_xml("epilogo"),
        ]
    )
    result3 = StoreWorkflow(aventura, llm3).run(src3)

    print(f"\nFuente 3: '{src3.name}'")
    print(f"  [Router] → nuevo hijo '{result3.routed_to}' (forjado en el momento)")
    print(f"  [Calls]  {llm3._call_count}")
    child_names = {c.name for c in aventura.children()}
    print("  [Hijos ahora]")
    print(aventura.format_tree())

    assert result3.routed_to == "epilogo"
    assert llm3._call_count == 2
    assert "epilogo" in child_names

    print("\n✓ Enrutamiento a hijo, a padre y creación de hijo nuevo — todos OK")


# ------------------------------------------------------------------ #
# Phase 5 — PDF structural detection                                 #
# ------------------------------------------------------------------ #


def test_phase5_pdf_structural_detection(tmp_minds_dir, tmp_path, monkeypatch):
    """
    A large PDF is split into chunks. The structurer LLM proposes two clusters.
    StoreWorkflow builds a child coppermind per cluster and writes pages there.
    A flat response leaves the mind without children.
    """
    from copper.core.coppermind import CopperMind
    from copper.llm.mock import MockLLM
    from copper.workflows.store import StoreWorkflow

    print("\n=== Phase 5 — Detección de estructura PDF ===\n")

    def wiki_xml(slug):
        return (
            "<wiki_updates>"
            f'<page slug="{slug}" title="{slug.title()}" action="create">'
            f"<content>Contenido sobre {slug}. [Fuente: src]</content>"
            "</page>"
            f"<index># Índice\n\n- [[{slug}]]</index>"
            "</wiki_updates>"
        )

    # Build 10 fake chunks — enough to exceed the default threshold of 8
    fake_chunks = [
        f"# Sección {i}\n\nContenido de la sección {i} del módulo de aventura." for i in range(10)
    ]

    # Patch registry.to_chunks: return fake_chunks for the PDF; for cluster temp
    # files (.md), return their full text as one chunk so each cluster = 1 LLM call.
    monkeypatch.setattr(
        "copper.ingest.registry.IngestRegistry.to_chunks",
        lambda self, path, max_chars, **kw: (
            fake_chunks if path.suffix.lower() == ".pdf" else [path.read_text()]
        ),
    )

    # --- Run 1: structurer proposes two clusters → hierarchy built ---
    aventura = CopperMind.forge("aventura-pdf", "Adventure module PDF")

    cluster_response = (
        '<cluster name="fase-1" topic="La Convocatoria">\n'
        + "\n".join(str(i) for i in range(5))
        + "\n</cluster>\n"
        '<cluster name="fase-2" topic="El Bosque Maldito">\n'
        + "\n".join(str(i) for i in range(5, 10))
        + "\n</cluster>\n"
        "<confidence>high</confidence>"
    )

    src = tmp_path / "aventura.pdf"
    src.write_bytes(b"%PDF-1.4 fake")

    llm = MockLLM(
        [
            cluster_response,  # structurer
            wiki_xml("convocatoria"),  # archivist for fase-1
            wiki_xml("bosque"),  # archivist for fase-2
        ]
    )
    result = StoreWorkflow(aventura, llm).run(src)

    print(f"  [Calls total]: {llm._call_count}  (structurer + 2 archivists)")
    print(f"  [Clusters]:    {result.structural_clusters}")
    print(f"  [Páginas]:     {result.pages_written}")
    print("\nÁrbol resultante:")
    print(aventura.format_tree())

    assert llm._call_count == 3
    assert result.structural_clusters == ["fase-1", "fase-2"]
    child_names = {c.name for c in aventura.children()}
    assert "fase-1" in child_names
    assert "fase-2" in child_names
    assert len(aventura.wiki_pages()) == 0  # pages land in children, not parent

    # --- Run 2: structurer returns <flat/> → no children, flat store ---
    # 10 ingots + consolidation polish = ~13 LLM calls; supply enough valid
    # wiki responses so MockLLM never cycles back to "<flat/>".
    plano = CopperMind.forge("modulo-plano", "Flat module")

    llm2 = MockLLM(["<flat/>"] + [wiki_xml("resumen")] * 15)
    result2 = StoreWorkflow(plano, llm2).run(src)

    print(f"\n  [Páginas]:  {result2.pages_written}")
    print(f"  [Hijos]:    {plano.children()}")

    assert result2.structural_clusters is None
    assert plano.children() == []
    assert len(plano.wiki_pages()) > 0

    print("\n✓ Estructura detectada → hijos creados; <flat/> → almacenamiento plano")


# ------------------------------------------------------------------ #
# Phase 6 — CLI ergonomics                                           #
# ------------------------------------------------------------------ #


def test_phase6_cli_ergonomics(tmp_minds_dir, tmp_path, monkeypatch):
    """
    End-to-end CLI smoke test for all Phase 6 additions:
    - forge padre/hijo creates nested copperminds
    - list prints the tree with indentation
    - store --no-route, --into, --flat reach the workflow correctly
    - polish --depth 0 caps recursion at the root
    """
    import re
    from typer.testing import CliRunner
    from copper.cli import app
    from copper.core.coppermind import CopperMind
    from copper.workflows.store import StoreResult
    from copper.workflows.polish import PolishResult
    from copper.llm.mock import MockLLM

    runner = CliRunner()

    def strip_ansi(text):
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    print("\n=== Phase 6 — CLI ergonomics ===\n")

    # ── forge parent ──────────────────────────────────────────────
    r = runner.invoke(app, ["forge", "aventura", "--topic", "Adventure module"])
    assert r.exit_code == 0, r.output
    print(f"forge aventura         → exit {r.exit_code}")

    # ── forge parent/child via slash syntax ───────────────────────
    r = runner.invoke(app, ["forge", "aventura/fase-1", "--topic", "La Convocatoria"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["forge", "aventura/fase-2", "--topic", "El Bosque"])
    assert r.exit_code == 0, r.output
    print("forge aventura/fase-1  → exit 0")
    print("forge aventura/fase-2  → exit 0")

    aventura = CopperMind.get("aventura")
    child_names = {c.name for c in aventura.children()}
    assert child_names == {"fase-1", "fase-2"}

    # ── list shows indented tree ──────────────────────────────────
    r = runner.invoke(app, ["list"])
    out = strip_ansi(r.output)
    assert r.exit_code == 0
    assert "aventura/" in out
    assert "fase-1/" in out
    assert "fase-2/" in out
    assert out.index("aventura/") < out.index("fase-1/")
    print("\ncopper list output:")
    print(out.strip())

    # ── forge nonexistent/child fails cleanly ─────────────────────
    r = runner.invoke(app, ["forge", "noexiste/hijo", "--topic", "tema"])
    assert r.exit_code == 1
    assert "noexiste" in r.output
    print(f"\nforge noexiste/hijo    → exit {r.exit_code} (expected)")

    # ── store --no-route reaches workflow ─────────────────────────
    src = tmp_path / "doc.txt"
    src.write_text("Contenido de prueba.")

    captured_store: dict = {}

    def fake_store_run(self, source_path, no_route=False, into=None):
        captured_store["no_route"] = no_route
        captured_store["into"] = into
        return StoreResult(source=source_path.name, pages_written=["p1"], tokens_used=0)

    monkeypatch.setattr("copper.workflows.store.StoreWorkflow.run", fake_store_run)
    monkeypatch.setattr("copper.api.deps.get_store_llm", lambda m: MockLLM([]))
    monkeypatch.setattr("copper.api.deps.get_ingest_describer", lambda m: None)

    r = runner.invoke(app, ["store", "aventura", str(src), "--no-route"])
    assert r.exit_code == 0, r.output
    assert captured_store["no_route"] is True
    print(f"\nstore --no-route       → no_route={captured_store['no_route']}")

    r = runner.invoke(app, ["store", "aventura", str(src), "--into", "fase-1"])
    assert captured_store["into"] == "fase-1"
    print(f"store --into fase-1    → into='{captured_store['into']}'")

    r = runner.invoke(app, ["store", "aventura", str(src), "--flat"])
    assert captured_store["no_route"] is True
    print(f"store --flat           → no_route={captured_store['no_route']} (alias)")

    # ── polish --depth 0 caps recursion ──────────────────────────
    captured_polish: dict = {}

    def fake_polish_run(self, max_depth=None):
        captured_polish["max_depth"] = max_depth
        return PolishResult(
            mind_name=self.mind.name,
            report_path=tmp_path / "lint.md",
            report_text="ok",
            structural_issues=[],
            tokens_used=0,
            cost_usd=0.0,
        )

    monkeypatch.setattr("copper.workflows.polish.PolishWorkflow.run", fake_polish_run)

    r = runner.invoke(app, ["polish", "aventura", "--depth", "0"])
    assert r.exit_code == 0, r.output
    assert captured_polish["max_depth"] == 0
    print(f"polish --depth 0       → max_depth={captured_polish['max_depth']}")

    r = runner.invoke(app, ["polish", "aventura"])
    assert captured_polish["max_depth"] is None
    print(f"polish (sin --depth)   → max_depth={captured_polish['max_depth']} (recursivo)")

    print("\n✓ forge padre/hijo, list árbol, store flags, polish --depth — todo OK")


# ------------------------------------------------------------------ #
# Phase 6.5 — Deep structural polish                                  #
# ------------------------------------------------------------------ #


def test_phase65_deep_polish(tmp_minds_dir):
    """
    Deep polish reorganizes a two-child tree in three passes:

    1. Mapper (per child) — LLM extracts entities from each child's wiki.
    2. Reducer (parent) — LLM proposes a move + a transversal page.
    3. Apply — page is moved; transversal page is synthesized; spine refreshed.

    What we check:
    - After apply, the moved page lives in the target child, not the source.
    - A new transversal page appears in the parent.
    - DeepPolishResult reports the correct counts.
    - Dry-run leaves the tree unchanged.
    """
    from copper.core.coppermind import CopperMind
    from copper.core.wiki import WikiManager
    from copper.llm.mock import MockLLM
    from copper.workflows.deep_polish import DeepPolishWorkflow

    parent = CopperMind.forge("aventura", "Adventure module")
    parte1 = parent.forge_child("parte-1", "La Convocatoria")
    parte2 = parent.forge_child("parte-2", "El Viaje")

    # Seed each child with pages
    parte1.wiki.create_page("szeth", "Szeth", "Szeth-son-son-Vallano appears throughout.")
    parte1.wiki.create_page("kaladin", "Kaladin", "Kaladin Stormblessed is the protagonist.")
    parte1.wiki.create_page("misrouted", "Misrouted Page", "Content that belongs in parte-2.")
    parte2.wiki.create_page("dalinar", "Dalinar", "Dalinar Kholin leads the war.")
    parte2.wiki.create_page("szeth-arc", "Szeth Arc", "Szeth's arc continues here.")

    # ── Dry-run: plan is produced, nothing changed ──
    mapper1_dry = (
        '<entities><entity name="Szeth" kind="character" pages="szeth,kaladin"/></entities>'
    )
    mapper2_dry = '<entities><entity name="Szeth" kind="character" pages="szeth-arc"/></entities>'
    reducer_dry = (
        "<plan>"
        '<move slug="misrouted" from="parte-1" to="parte-2" reason="content fits parte-2"/>'
        '<transversal entity="Szeth" kind="character" children="parte-1,parte-2" reason="spans both"/>'
        "</plan>"
    )
    llm_dry = MockLLM([mapper1_dry, mapper2_dry, reducer_dry])
    dry_result = DeepPolishWorkflow(parent, llm_dry).run(dry_run=True, auto_yes=True)

    def pages_in(mind):
        return sorted(p.name for p in mind.wiki.all_pages())

    print("\n  Árbol inicial:")
    print(f"    aventura/parte-1/ → {pages_in(parte1)}")
    print(f"    aventura/parte-2/ → {pages_in(parte2)}")
    print(f"    aventura/         → {pages_in(parent)}")

    assert len(dry_result.plan.moves) == 1
    assert len(dry_result.plan.transversals) == 1
    assert dry_result.moves_applied == []
    assert dry_result.transversals_applied == []
    assert parte1.wiki.page("misrouted").exists(), "dry-run must not move pages"
    assert not parent.wiki.page("szeth").exists(), "dry-run must not create transversals"

    m = dry_result.plan.moves[0]
    t = dry_result.plan.transversals[0]
    print("\n  [Dry-run] el reducer propuso:")
    print(f"    MOVE       '{m.slug}'  {m.from_child} → {m.to_child}  ({m.reason})")
    print(
        f"    TRANSVERSAL '{t.entity}' ({t.kind}) desde [{', '.join(t.source_children)}]  ({t.reason})"
    )
    print("  → árbol sin cambios (dry-run) ✓")

    # ── Full run: plan is applied ──
    mapper1 = '<entities><entity name="Szeth" kind="character" pages="szeth,kaladin"/></entities>'
    mapper2 = '<entities><entity name="Szeth" kind="character" pages="szeth-arc"/></entities>'
    reducer = (
        "<plan>"
        '<move slug="misrouted" from="parte-1" to="parte-2" reason="content fits parte-2"/>'
        '<transversal entity="Szeth" kind="character" children="parte-1,parte-2" reason="spans both"/>'
        "</plan>"
    )
    synth_body = "## Szeth\n\nSzeth-son-son-Vallano is a central character across both parts."
    spine_body = "## Overview\n\nParte-1 covers the Convocatoria; Parte-2 covers the Viaje."

    llm = MockLLM([mapper1, mapper2, reducer, synth_body, spine_body])
    result = DeepPolishWorkflow(parent, llm).run(dry_run=False, auto_yes=True)

    assert result.pages_moved == 1
    assert result.pages_created == 1
    assert not parte1.wiki.page("misrouted").exists(), "page should have moved out of parte-1"
    assert parte2.wiki.page("misrouted").exists(), "page should now be in parte-2"
    assert parent.wiki.page("szeth").exists(), "transversal page should exist in parent"
    assert parent.wiki.page("overview").exists(), "spine should be refreshed"

    print("\n  [Apply] árbol resultante:")
    print(f"    aventura/parte-1/ → {pages_in(parte1)}  (misrouted eliminada)")
    print(f"    aventura/parte-2/ → {pages_in(parte2)}  (misrouted añadida)")
    print(f"    aventura/         → {pages_in(parent)}  (szeth + overview nuevas)")
    print(f"  → {result.pages_moved} página movida, {result.pages_created} transversal creada ✓")
    print("\n✓ Deep polish completo: move, synthesis, spine refresh")


# ------------------------------------------------------------------ #
# Phase 7 — API tree navigation                                       #
# ------------------------------------------------------------------ #


def test_phase7_api_tree_navigation(tmp_minds_dir):
    """
    The REST API exposes the full coppermind tree and resolves child minds
    via slash-separated paths.

    Scenario: a two-level tree (aventura → parte-1, parte-2).
    parte-1 has one wiki page.

    What we check:
    - GET /minds returns the tree with children nested under the parent.
    - GET /minds/aventura/parte-1 resolves the child by path (200, correct name).
    - GET /minds/aventura/parte-1/wiki lists the child's pages.
    - GET /minds/aventura/parte-1/wiki/szeth reads the specific page.
    - GET /minds/aventura/noexiste returns 404.
    - Root minds have is_root=True; children have is_root=False.
    """
    from fastapi.testclient import TestClient

    from copper.api.app import create_app
    from copper.core.coppermind import CopperMind

    parent = CopperMind.forge("aventura", "Adventure module")
    parte1 = parent.forge_child("parte-1", "La Convocatoria")
    parte2 = parent.forge_child("parte-2", "El Viaje")
    parte1.wiki.create_page("szeth", "Szeth", "Szeth-son-son-Vallano.")

    client = TestClient(create_app())

    # ── GET /minds — tree includes children ──
    res = client.get("/minds")
    assert res.status_code == 200
    data = res.json()
    aventura = next(m for m in data if m["name"] == "aventura")
    child_names = [c["name"] for c in aventura["children"]]

    print("\n  GET /minds → árbol completo:")
    print(f"    aventura  (is_root={aventura['is_root']})  children={child_names}")
    for c in aventura["children"]:
        print(f"      └─ {c['name']}  topic='{c['topic']}'  is_root={c['is_root']}")

    assert aventura["is_root"] is True
    assert set(child_names) == {"parte-1", "parte-2"}
    assert all(c["is_root"] is False for c in aventura["children"])

    # ── GET /minds/aventura/parte-1 — child resolved by path ──
    res = client.get("/minds/aventura/parte-1")
    assert res.status_code == 200
    child_data = res.json()
    print(
        f"\n  GET /minds/aventura/parte-1 → {child_data['name']}  wiki_pages={child_data['wiki_pages']}"
    )
    assert child_data["name"] == "parte-1"
    assert child_data["wiki_pages"] == 1

    # ── GET /minds/aventura/parte-1/wiki — child's page list ──
    res = client.get("/minds/aventura/parte-1/wiki")
    assert res.status_code == 200
    pages = res.json()
    print(f"  GET /minds/aventura/parte-1/wiki → {pages}")
    assert "szeth" in pages

    # ── GET /minds/aventura/parte-1/wiki/szeth — specific page ──
    res = client.get("/minds/aventura/parte-1/wiki/szeth")
    assert res.status_code == 200
    page = res.json()
    print(f"  GET .../wiki/szeth → slug='{page['slug']}'  body={page['body'][:40]!r}…")
    assert page["slug"] == "szeth"

    # ── 404 for unknown child ──
    res = client.get("/minds/aventura/noexiste")
    assert res.status_code == 404
    print(f"  GET /minds/aventura/noexiste → {res.status_code} ✓")

    print("\n✓ API tree: árbol anidado, rutas hijo por path, 404 correcto")


# ------------------------------------------------------------------ #
# Fase A.1 — copper move                                              #
# ------------------------------------------------------------------ #


def test_fase_a1_copper_move(tmp_minds_dir, tmp_path):
    """
    A page stored in a parent mind can be moved to a child via `copper move`.
    Both wiki files and log entries reflect the move.
    """
    from typer.testing import CliRunner
    from copper.cli import app
    from copper.core.coppermind import CopperMind
    from copper.llm.mock import MockLLM
    from copper.workflows.store import StoreResult
    import re

    runner = CliRunner()

    def strip_ansi(text):
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    print("\n=== Fase A.1 — copper move ===\n")

    # Build parent + child
    padre = CopperMind.forge("padre", "Parent mind")
    hijo = padre.forge_child("hijo", "Child mind")

    # Seed a page directly (no real LLM needed)
    padre.wiki.upsert_page("pagina-test", "Pagina Test", "Contenido demo para mover.")
    padre.wiki.update_index("# Índice\n\n- [[pagina-test]]")

    print(f"  Padre wiki antes del move: {[p.name for p in padre.wiki.all_pages()]}")
    print(f"  Hijo wiki antes del move:  {[p.name for p in hijo.wiki.all_pages()]}")

    # copper move pagina-test --from padre --to padre/hijo
    result = runner.invoke(
        app, ["move", "pagina-test", "--from", "padre", "--to", "padre/hijo"]
    )
    out = strip_ansi(result.output)
    print(f"\n  $ copper move pagina-test --from padre --to padre/hijo")
    print(f"  → exit {result.exit_code}")
    print(f"  → {out.strip()}")

    assert result.exit_code == 0, out

    padre2 = CopperMind.get("padre")
    hijo2 = next(c for c in padre2.children() if c.name == "hijo")

    after_padre = [p.name for p in padre2.wiki.all_pages()]
    after_hijo = [p.name for p in hijo2.wiki.all_pages()]
    print(f"\n  Padre wiki después: {after_padre}")
    print(f"  Hijo wiki después:  {after_hijo}")

    assert "pagina-test" not in after_padre, "La página debe haber salido del padre"
    assert "pagina-test" in after_hijo, "La página debe estar en el hijo"

    padre_log = padre2.log_path.read_text()
    hijo_log = hijo2.log_path.read_text()
    assert "pagina-test" in padre_log and "move" in padre_log
    assert "pagina-test" in hijo_log and "move" in hijo_log
    print("\n  Logs de padre e hijo registran el move ✓")

    # Error: slug collision in target
    hijo2.wiki.upsert_page("clash", "Clash", "ya existe")
    padre2.wiki.upsert_page("clash", "Clash", "en padre")
    result_err = runner.invoke(
        app, ["move", "clash", "--from", "padre", "--to", "padre/hijo"]
    )
    print(f"\n  $ copper move clash --from padre --to padre/hijo (colisión esperada)")
    print(f"  → exit {result_err.exit_code}  ({strip_ansi(result_err.output).strip()})")
    assert result_err.exit_code != 0
    assert padre2.wiki.page("clash").exists(), "Colisión no debe mover la página"

    print("\n✓ copper move: mover entre padre/hijo, logs en ambos, error en colisión")


# ------------------------------------------------------------------ #
# Fase A.2 — linked minds × hierarchy warning                        #
# ------------------------------------------------------------------ #


def test_fase_a2_link_same_tree_warning(tmp_minds_dir):
    """
    Linking minds within the same tree (sibling or ancestor/descendant) emits
    a warning; cross-tree links are silent. The link is always established.
    """
    from unittest.mock import patch
    from copper.core.coppermind import CopperMind

    print("\n=== Fase A.2 — linked minds × hierarchy warning ===\n")

    root1 = CopperMind.forge("tree1", "first tree")
    child_a = root1.forge_child("child-a", "first child")
    child_b = root1.forge_child("child-b", "second child")
    root2 = CopperMind.forge("tree2", "second tree")

    # Cross-tree link — should be SILENT
    with patch("copper.core.coppermind.logger") as mock_log:
        root1.link(root2)
    called_cross = mock_log.warning.called
    print(f"  link tree1 ↔ tree2  (árboles distintos) → warning={called_cross}")
    assert not called_cross, "No debe advertir en links entre árboles distintos"

    # Reload to avoid stale config
    root1 = CopperMind.get("tree1")
    child_a = next(c for c in root1.children() if c.name == "child-a")
    child_b = next(c for c in root1.children() if c.name == "child-b")

    # Sibling link — must WARN
    with patch("copper.core.coppermind.logger") as mock_log:
        child_a.link(child_b)
    called_sibling = mock_log.warning.called
    print(f"  link child-a ↔ child-b (hermanos)        → warning={called_sibling}")
    assert called_sibling, "Debe advertir en links entre hermanos"

    # Reload
    root1 = CopperMind.get("tree1")
    child_a = next(c for c in root1.children() if c.name == "child-a")

    # Parent-child link — must WARN
    with patch("copper.core.coppermind.logger") as mock_log:
        root1.link(child_a)
    called_parent_child = mock_log.warning.called
    print(f"  link tree1 ↔ child-a (padre-hijo)        → warning={called_parent_child}")
    assert called_parent_child, "Debe advertir en links entre ancestro y descendiente"

    # Confirm all three links exist despite warnings
    root1 = CopperMind.get("tree1")
    assert "tree2" in root1.config.linked_minds
    assert "child-b" in next(c for c in root1.children() if c.name == "child-a").config.linked_minds
    assert "child-a" in root1.config.linked_minds
    print("\n  Todos los links establecidos correctamente a pesar de los warnings ✓")

    print("\n✓ Cross-tree silencioso, same-tree (hermano/ancestro) advierte")


# ------------------------------------------------------------------ #
# Fase A.3 — max_depth per mind                                      #
# ------------------------------------------------------------------ #


def test_fase_a3_max_depth_per_mind(tmp_minds_dir):
    """
    A per-mind max_depth=0 prevents tap from descending into children,
    overriding the global setting. A mind without max_depth uses the global.
    """
    from copper.core.coppermind import CopperMind
    from copper.llm.mock import MockLLM
    from copper.workflows.tap import TapWorkflow
    import yaml

    print("\n=== Fase A.3 — max_depth por mente ===\n")

    # Build: parent with 1 child, child has a page
    parent = CopperMind.forge("par", "parent mind")
    child = parent.forge_child("kid", "child mind")
    child.wiki.upsert_page("kid-page", "Kid Page", "Info in the child mind.")

    # Verify config.yaml has NO max_depth key when unset
    raw_config = yaml.safe_load(parent.config_path.read_text())
    print(f"  config.yaml sin max_depth → claves: {list(raw_config.keys())}")
    assert "max_depth" not in raw_config

    # Set max_depth=0 on parent → tap must NOT descend into child
    parent.config.max_depth = 0
    parent.save_config()

    raw_config2 = yaml.safe_load(parent.config_path.read_text())
    print(f"  config.yaml con max_depth → max_depth={raw_config2['max_depth']}")
    assert raw_config2["max_depth"] == 0

    parent2 = CopperMind.get("par")
    assert parent2.config.max_depth == 0

    # Global default is 2, but per-mind is 0 → must not descend
    llm = MockLLM(["respuesta sin descender"])
    result = TapWorkflow([parent2], llm).run("¿qué hay aquí?")

    print(f"\n  Tap con max_depth=0 → minds_used={result.minds_used}")
    assert result.minds_used == ["par"], (
        f"Con max_depth=0 no debe descender al hijo; got {result.minds_used}"
    )
    print("  → solo el padre consultado, el hijo ignorado ✓")

    # Without per-mind override, global (2) is used — child IS reachable
    # (just verify the config round-trips to None cleanly)
    solo = CopperMind.forge("solo", "standalone")
    assert solo.config.max_depth is None
    raw_solo = yaml.safe_load(solo.config_path.read_text())
    assert "max_depth" not in raw_solo
    print(f"\n  Mente sin override: max_depth={solo.config.max_depth} (usa global) ✓")

    print("\n✓ max_depth per-mind: override funciona, round-trip YAML limpio")
