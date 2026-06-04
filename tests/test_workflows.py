"""Integration tests for Store, Tap, and Polish workflows using MockLLM."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def mind(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    return CopperMind.forge("test-mind", "inteligencia artificial")


@pytest.fixture
def source_file(tmp_path):
    f = tmp_path / "articulo.md"
    f.write_text(
        "# Transformers\n\n"
        "Los transformers usan mecanismos de atención para procesar secuencias. "
        "Introducidos por Vaswani et al. en 2017.\n"
    )
    return f


# ------------------------------------------------------------------ #
# Store                                                               #
# ------------------------------------------------------------------ #


class TestStoreWorkflow:
    def test_store_basic(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(
            [
                "<wiki_updates>"
                '<page slug="transformers" title="Transformers" action="create">'
                "<content>Los transformers usan atención. [Fuente: articulo.md]</content>"
                "</page>"
                "<index># Índice\n\n- [[transformers]] — Arquitectura transformer</index>"
                "</wiki_updates>",
                "Meta summary.",  # regenerate_meta call after store
            ]
        )
        workflow = StoreWorkflow(mind, llm)
        result = workflow.run(source_file)

        assert result.source == "articulo.md"
        assert "transformers" in result.pages_written
        assert llm._call_count == 2  # archivist + meta

    def test_store_no_polish_skips_consolidation(self, mind, tmp_path, monkeypatch):
        """auto_polish=False skips the post-store polish but still refreshes _meta."""
        from copper.workflows.store import StoreWorkflow
        from copper.workflows import polish as polish_mod
        from copper.llm.mock import MockLLM

        # Source large enough to split into multiple ingots (> MAX_CHUNK_CHARS).
        big = tmp_path / "big.md"
        big.write_text("# Big\n\n" + ("Mucho contenido de relleno. " * 1200))

        polish_calls = {"n": 0}
        orig_run = polish_mod.PolishWorkflow.run

        def spy_run(self, *a, **k):
            polish_calls["n"] += 1
            return orig_run(self, *a, **k)

        monkeypatch.setattr(polish_mod.PolishWorkflow, "run", spy_run)

        # Deferred polish: no consolidation, but _meta is still written.
        StoreWorkflow(mind, MockLLM()).run(big, auto_polish=False)
        assert polish_calls["n"] == 0
        assert (mind.wiki_dir / "_meta.md").exists()

        # Default: consolidation polish runs once.
        StoreWorkflow(mind, MockLLM()).run(big, auto_polish=True)
        assert polish_calls["n"] == 1

    def test_store_creates_wiki_page(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(
            [
                "<wiki_updates>"
                '<page slug="resumen" title="Resumen" action="create">'
                "<content>Resumen de la fuente.</content>"
                "</page>"
                "<index># Índice actualizado</index>"
                "</wiki_updates>"
            ]
        )
        workflow = StoreWorkflow(mind, llm)
        workflow.run(source_file)

        assert (mind.wiki_dir / "resumen.md").exists()

    def test_store_updates_log(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM()
        workflow = StoreWorkflow(mind, llm)
        workflow.run(source_file)

        log_content = mind.log_path.read_text()
        assert "store" in log_content
        assert "articulo.md" in log_content

    def test_store_fallback_when_no_xml(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Respuesta sin formato XML"])
        workflow = StoreWorkflow(mind, llm)
        result = workflow.run(source_file)

        # Fallback should still create a page
        assert len(result.pages_written) > 0

    def test_store_missing_file_raises(self, mind):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM()
        workflow = StoreWorkflow(mind, llm)
        with pytest.raises(FileNotFoundError):
            workflow.run(Path("/no/existe.md"))


# ------------------------------------------------------------------ #
# Routed Store — Phase 4                                             #
# ------------------------------------------------------------------ #


class TestRoutedStore:
    """Router decides where new content lands in a hierarchical coppermind."""

    def _wiki_xml(self, slug="resumen", title="Resumen"):
        return (
            "<wiki_updates>"
            f'<page slug="{slug}" title="{title}" action="create">'
            f"<content>Contenido de {slug}. [Fuente: src]</content>"
            "</page>"
            f"<index># Índice\n\n- [[{slug}]]</index>"
            "</wiki_updates>"
        )

    def test_store_flat_router_not_invoked(self, mind, source_file):
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM([self._wiki_xml(), "Meta."])
        result = StoreWorkflow(mind, llm).run(source_file)

        assert result.routed_to is None
        assert llm._call_count == 2  # archivist + meta (no router — flat mind)

    def test_store_routes_to_child(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM(
            [
                "<route>fase-1</route>",  # router → fase-1
                self._wiki_xml(),  # archivist in fase-1
                "Meta.",  # regenerate_meta for fase-1 (parent wiki empty → skipped)
            ]
        )
        result = StoreWorkflow(parent, llm).run(source_file)

        assert result.routed_to == "fase-1"
        assert llm._call_count == 3  # router + child archivist + child meta
        # Page landed in the child's wiki, not the parent's
        child = parent.children()[0]
        assert len(child.wiki_pages()) > 0
        assert len(parent.wiki_pages()) == 0

    def test_store_routes_to_parent(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM(
            [
                "<route>parent</route>",  # router → stay at parent
                self._wiki_xml(),
                "Meta.",  # regenerate_meta for parent
            ]
        )
        result = StoreWorkflow(parent, llm).run(source_file)

        assert result.routed_to is None
        assert llm._call_count == 3  # router + archivist + meta
        assert len(parent.wiki_pages()) > 0

    def test_store_router_creates_new_child(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM(
            [
                "<route>new_child:fase-2</route>\n<topic>Segunda fase del módulo</topic>",
                self._wiki_xml(),
                "Meta.",  # regenerate_meta for new child (parent wiki empty → skipped)
            ]
        )
        result = StoreWorkflow(parent, llm).run(source_file)

        assert result.routed_to == "fase-2"
        assert llm._call_count == 3  # router + child archivist + child meta
        child_names = {c.name for c in parent.children()}
        assert "fase-2" in child_names

    def test_store_no_route_flag_skips_router(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([self._wiki_xml(), "Meta."])
        result = StoreWorkflow(parent, llm).run(source_file, no_route=True)

        assert result.routed_to is None
        assert llm._call_count == 2  # archivist + meta (no router)

    def test_store_into_explicit_child(self, tmp_minds_dir, source_file):
        from copper.core.coppermind import CopperMind
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "adventure")
        parent.forge_child("fase-1", "first phase")

        llm = MockLLM([self._wiki_xml(), "Meta."])
        result = StoreWorkflow(parent, llm).run(source_file, into="fase-1")

        assert result.routed_to == "fase-1"
        assert (
            llm._call_count == 2
        )  # child archivist + child meta (no router; parent wiki empty → skip)


# ------------------------------------------------------------------ #
# Tap                                                                 #
# ------------------------------------------------------------------ #


class TestTapWorkflow:
    def test_tap_returns_answer(self, mind):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(
            ["Los transformers son arquitecturas de redes neuronales. [Fuente: transformers]"]
        )
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Qué son los transformers?")

        assert "transformers" in result.answer.lower()
        assert result.minds_used == ["test-mind"]

    def test_tap_multi_mind(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        mind1 = CopperMind.forge("mente-a", "tema A")
        mind2 = CopperMind.forge("mente-b", "tema B")

        llm = MockLLM(["Respuesta combinada de A y B."])
        workflow = TapWorkflow([mind1, mind2], llm)
        result = workflow.run("pregunta")

        assert set(result.minds_used) == {"mente-a", "mente-b"}
        # 2 phase-1 selection calls (one per mind) + 1 phase-2 answer call
        assert llm._call_count == 3

    def test_tap_save_to_outputs(self, mind):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Respuesta guardada."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Pregunta de prueba?", save_to_outputs=True)

        assert len(result.saved_to) == 1
        assert result.saved_to[0].exists()
        assert "Respuesta guardada" in result.saved_to[0].read_text()

    def test_tap_updates_log(self, mind):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Respuesta."])
        workflow = TapWorkflow([mind], llm)
        workflow.run("pregunta log")

        log_content = mind.log_path.read_text()
        assert "tap" in log_content

    def test_tap_with_history_passes_prior_turns(self, mind):
        from copper.llm.base import Message
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        llm = MockLLM(["Respuesta de seguimiento."])
        history = [
            Message(role="user", content="¿Qué son los transformers?"),
            Message(role="assistant", content="Son arquitecturas de atención."),
        ]
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Cuándo se introdujeron?", history=history)

        assert result.answer == "Respuesta de seguimiento."
        # The LLM must receive system + 2 history turns + current user = 4 messages
        last_call = llm.calls[-1]
        assert len(last_call) == 4
        assert last_call[1].role == "user"
        assert last_call[1].content == history[0].content
        assert last_call[2].role == "assistant"
        assert last_call[3].role == "user"

    def test_tap_without_history_unchanged(self, mind):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        llm = MockLLM(["Respuesta normal."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿Pregunta simple?")

        # system + user only (no history)
        last_call = llm.calls[-1]
        assert len(last_call) == 2
        assert last_call[0].role == "system"
        assert last_call[1].role == "user"


# ------------------------------------------------------------------ #
# Hierarchical Tap — Phase 2                                          #
# ------------------------------------------------------------------ #


class TestHierarchicalTap:
    """Scanner-guided two-stage tap on hierarchical copperminds."""

    def _make_tree(self, tmp_minds_dir, children: list[str] | None = None):
        """Return (parent, {child_name: child}) with simple wiki pages."""
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager

        parent = CopperMind.forge("aventura", "adventure module")
        WikiManager(parent.wiki_dir).create_page(
            "overview", "Overview", "General adventure overview. [Fuente: overview]"
        )

        child_map = {}
        for name in children or []:
            child = parent.forge_child(name, f"topic for {name}")
            WikiManager(child.wiki_dir).create_page(
                f"pagina-{name}", f"Page {name}", f"Content of {name}. [Fuente: src]"
            )
            child_map[name] = child

        return parent, child_map

    def test_flat_mind_scanner_not_invoked(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("plana", "flat mind")
        WikiManager(mind.wiki_dir).create_page("resumen", "Resumen", "Info. [Fuente: src]")
        llm = MockLLM(["PAGE: resumen", "Respuesta plana."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿pregunta?")

        assert result.answer == "Respuesta plana."
        # flat path: 1 retriever call + 1 answer = 2
        assert llm._call_count == 2

    def test_hierarchical_scanner_picks_one_child(self, tmp_minds_dir):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        parent, children = self._make_tree(tmp_minds_dir, ["fase-1", "fase-2"])
        llm = MockLLM(
            [
                "<descend>\nfase-1\n</descend>",  # scanner → pick fase-1
                "PAGE: pagina-fase-1",  # retriever on fase-1
                "Respuesta sobre fase-1.",  # final answer
            ]
        )
        workflow = TapWorkflow([parent], llm)
        result = workflow.run("¿qué pasa en la fase 1?")

        assert result.answer == "Respuesta sobre fase-1."
        # 1 scanner + 1 retriever on fase-1 + 1 answer
        assert llm._call_count == 3
        # Context in the answer call includes the child's page
        answer_call_user = llm.calls[-1][-1].content
        assert "fase-1" in answer_call_user

    def test_hierarchical_scanner_picks_two_children(self, tmp_minds_dir):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        parent, _ = self._make_tree(tmp_minds_dir, ["fase-1", "fase-2"])
        llm = MockLLM(
            [
                "<descend>\nfase-1\nfase-2\n</descend>",  # scanner → both
                "PAGE: pagina-fase-1",  # retriever on fase-1
                "PAGE: pagina-fase-2",  # retriever on fase-2
                "Respuesta que abarca ambas fases.",  # final answer
            ]
        )
        workflow = TapWorkflow([parent], llm)
        result = workflow.run("¿resumen de todas las fases?")

        assert result.answer == "Respuesta que abarca ambas fases."
        # 1 scanner + 2 retrievers + 1 answer
        assert llm._call_count == 4

    def test_hierarchical_scanner_parent_only(self, tmp_minds_dir):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        parent, _ = self._make_tree(tmp_minds_dir, ["fase-1"])
        llm = MockLLM(
            [
                "<descend>\n</descend>",  # scanner → parent only
                "Respuesta solo del padre.",
            ]
        )
        workflow = TapWorkflow([parent], llm)
        result = workflow.run("¿pregunta general?")

        assert result.answer == "Respuesta solo del padre."
        # 1 scanner + 0 retrievers + 1 answer
        assert llm._call_count == 2
        # No child pages in context
        answer_call_user = llm.calls[-1][-1].content
        assert "fase-1" not in answer_call_user

    def test_hierarchical_three_level_nested_scan(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        root = CopperMind.forge("root", "root topic")
        child = root.forge_child("child", "child topic")
        grandchild = child.forge_child("grandchild", "grandchild topic")
        WikiManager(grandchild.wiki_dir).create_page(
            "deep-page", "Deep Page", "Deep content. [Fuente: deep]"
        )

        llm = MockLLM(
            [
                "<descend>\nchild\n</descend>",  # scanner on root
                "<descend>\ngrandchild\n</descend>",  # scanner on child
                "PAGE: deep-page",  # retriever on grandchild
                "Respuesta profunda.",  # final answer
            ]
        )
        workflow = TapWorkflow([root], llm)
        result = workflow.run("¿contenido profundo?")

        assert result.answer == "Respuesta profunda."
        # 2 scanners + 1 retriever + 1 answer
        assert llm._call_count == 4


# ------------------------------------------------------------------ #
# Polish                                                              #
# ------------------------------------------------------------------ #


class TestPolishWorkflow:
    def test_polish_generates_report(self, mind):
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["# Informe\n\n🔵 Wiki en buen estado."])
        workflow = PolishWorkflow(mind, llm)
        result = workflow.run()

        assert result.report_path.exists()
        assert "Informe" in result.report_path.read_text()

    def test_polish_structural_checks(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        # Create a page without source citations
        wm = WikiManager(mind.wiki_dir)
        wm.create_page("sin-fuente", "Sin Fuente", "Contenido sin citas.")

        llm = MockLLM(["Informe mock."])
        workflow = PolishWorkflow(mind, llm)
        result = workflow.run()

        assert any("sin-fuente" in issue for issue in result.structural_issues)

    def test_polish_updates_log(self, mind):
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        llm = MockLLM(["Informe."])
        workflow = PolishWorkflow(mind, llm)
        workflow.run()

        log_content = mind.log_path.read_text()
        assert "polish" in log_content

    def test_polish_flat_creates_meta(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        WikiManager(mind.wiki_dir).create_page("info", "Info", "Content. [Fuente: src]")
        llm = MockLLM(["Informe.", "Meta summary of this mind."])
        result = PolishWorkflow(mind, llm).run()

        assert mind.meta_summary_path.exists()
        assert mind.meta_summary == "Meta summary of this mind."
        assert result.meta_path == mind.meta_summary_path
        # flat mind: 1 archivist call + 1 meta call = 2
        assert llm._call_count == 2

    def test_polish_hierarchical_bottom_up(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "parent topic")
        child1 = parent.forge_child("hijo-1", "child 1 topic")
        child2 = parent.forge_child("hijo-2", "child 2 topic")
        for mind in (child1, child2, parent):
            WikiManager(mind.wiki_dir).create_page(
                "info", "Info", f"Content of {mind.name}. [Fuente: src]"
            )

        # Cycle: archivist responses on even calls, meta responses on odd
        llm = MockLLM(["# Informe.", "Meta generada."])
        result = PolishWorkflow(parent, llm).run()

        # All three minds have _meta.md
        assert child1.meta_summary_path.exists()
        assert child2.meta_summary_path.exists()
        assert parent.meta_summary_path.exists()

        # All three have lint reports
        assert any(parent.wiki_dir.glob("lint-report-*.md"))
        assert any(child1.wiki_dir.glob("lint-report-*.md"))
        assert any(child2.wiki_dir.glob("lint-report-*.md"))

        # Result tree
        assert len(result.children_results) == 2
        child_names = {r.mind_name for r in result.children_results}
        assert child_names == {"hijo-1", "hijo-2"}

        # 3 minds × 2 calls each = 6 total
        assert llm._call_count == 6

    def test_polish_idempotent(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        WikiManager(mind.wiki_dir).create_page("info", "Info", "Content. [Fuente: src]")
        llm = MockLLM(["# Informe.", "Meta summary."])

        PolishWorkflow(mind, llm).run()
        meta_after_first = mind.meta_summary

        llm2 = MockLLM(["# Informe.", "Meta summary."])
        PolishWorkflow(mind, llm2).run()
        meta_after_second = mind.meta_summary

        # _meta.md is overwritten, not appended — same content, same length
        assert meta_after_first == meta_after_second

    def test_polish_max_depth_zero_skips_children(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.workflows.polish import PolishWorkflow
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("padre", "topic")
        parent.forge_child("hijo", "child topic")
        WikiManager(parent.wiki_dir).create_page("info", "Info", "Parent content. [Fuente: src]")

        llm = MockLLM(["# Informe.", "Meta."])
        PolishWorkflow(parent, llm).run(max_depth=0)

        # max_depth=0: only parent polished, child untouched
        assert not parent.children()[0].meta_summary_path.exists()
        assert llm._call_count == 2  # archivist + meta for parent only


# ------------------------------------------------------------------ #
# Tap — Fallback cap (C.1)                                           #
# ------------------------------------------------------------------ #


class TestTapFallbackCap:
    def test_fallback_degrades_when_wiki_exceeds_cap(self, tmp_minds_dir, monkeypatch):
        """Large wiki + empty retriever → degraded context, no exception."""
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("grande", "big mind")
        wiki = WikiManager(mind.wiki_dir)
        for i in range(3):
            wiki.create_page(f"page-{i}", f"Page {i}", f"Content {i}. [Fuente: src]")
        # Write _meta.md so the degraded context has content.
        mind.meta_summary_path.write_text("Resumen general del wiki.\n")

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_fallback_max_pages", 2)
        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_fallback_head_lines", 3)

        # Retriever returns no slugs (empty response → no PAGE: markers).
        llm = MockLLM(["", "Respuesta degradada."])
        workflow = TapWorkflow([mind], llm)

        # Should NOT raise — degraded context is built instead.
        result = workflow.run("¿pregunta?")
        assert result.answer == "Respuesta degradada."

        # Degraded context includes _meta and page headers (capped at 2).
        context = llm.calls[-1][-1].content
        assert "Resumen general" in context
        assert "page-0" in context or "page-1" in context

    def test_fallback_degraded_context_contains_meta_and_headers(self, tmp_minds_dir, monkeypatch):
        """Degraded context should include _meta.md + first N lines of pages."""
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("pequeña", "small mind")
        wiki = WikiManager(mind.wiki_dir)
        for i in range(2):
            wiki.create_page(f"pg-{i}", f"Pg {i}", f"Content {i}. [Fuente: src]")
        mind.meta_summary_path.write_text("_meta content.\n")

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_fallback_max_pages", 5)
        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_fallback_head_lines", 3)

        llm = MockLLM(["", "Respuesta fallback."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿pregunta?")

        assert result.answer == "Respuesta fallback."
        context = llm.calls[-1][-1].content
        assert "_meta content" in context
        assert "pg-0" in context
        assert "pg-1" in context

    def test_fallback_returns_empty_for_empty_wiki(self, tmp_minds_dir):
        """Empty wiki with no _meta.md: tap degrades gracefully, no exception."""
        from copper.core.coppermind import CopperMind
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("vacio", "empty mind")
        # No pages, no _meta.md — but wiki_dir was created by forge.

        llm = MockLLM(["", "Respuesta con wiki vacío."])
        workflow = TapWorkflow([mind], llm)

        # Should NOT raise — returns answer based on index context alone.
        result = workflow.run("¿pregunta?")
        assert result.answer == "Respuesta con wiki vacío."

    def test_fallback_not_triggered_when_retriever_returns_pages(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("normal", "normal mind")
        wiki = WikiManager(mind.wiki_dir)
        wiki.create_page("info", "Info", "Content. [Fuente: src]")

        llm = MockLLM(["PAGE: info", "Respuesta."])
        workflow = TapWorkflow([mind], llm)
        result = workflow.run("¿qué hay en info?")

        assert result.answer == "Respuesta."
        assert "info" in llm.calls[-1][-1].content


# ------------------------------------------------------------------ #
# Tap — Profiler instrumentation (C.2)                               #
# ------------------------------------------------------------------ #


class TestTapProfiler:
    def test_profiler_enabled_runs_without_error(self, tmp_minds_dir, monkeypatch):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("profiled", "topic")
        WikiManager(mind.wiki_dir).create_page("p", "P", "Content. [Fuente: src]")

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_profile", True)

        llm = MockLLM(["PAGE: p", "Respuesta."])
        result = TapWorkflow([mind], llm).run("¿pregunta?")
        assert result.answer == "Respuesta."

    def test_profiler_disabled_runs_without_error(self, tmp_minds_dir, monkeypatch):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        mind = CopperMind.forge("noprofile", "topic")
        WikiManager(mind.wiki_dir).create_page("p", "P", "Content. [Fuente: src]")

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_profile", False)

        llm = MockLLM(["PAGE: p", "Respuesta."])
        result = TapWorkflow([mind], llm).run("¿pregunta?")
        assert result.answer == "Respuesta."


# ------------------------------------------------------------------ #
# Tap — Parallel vs sequential descent (C.3)                         #
# ------------------------------------------------------------------ #


class TestTapParallelDescent:
    def _make_hierarchical(self, names: list[str]):
        from copper.core.coppermind import CopperMind
        from copper.core.wiki import WikiManager

        parent = CopperMind.forge("raiz", "root topic")
        WikiManager(parent.wiki_dir).create_page("top", "Top", "Top info. [Fuente: src]")
        for name in names:
            child = parent.forge_child(name, f"topic {name}")
            WikiManager(child.wiki_dir).create_page(
                f"pag-{name}", f"Pag {name}", f"Content {name}. [Fuente: src]"
            )
        return parent

    def test_parallel_returns_correct_answer(self, tmp_minds_dir, monkeypatch):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_legacy_sequential", False)

        parent = self._make_hierarchical(["alpha", "beta"])
        llm = MockLLM(
            [
                "<descend>\nalpha\nbeta\n</descend>",
                "PAGE: pag-alpha",
                "PAGE: pag-beta",
                "Respuesta paralela.",
            ]
        )
        result = TapWorkflow([parent], llm).run("¿resumen?")
        assert result.answer == "Respuesta paralela."
        assert llm._call_count == 4

    def test_sequential_returns_correct_answer(self, tmp_minds_dir, monkeypatch):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_legacy_sequential", True)

        parent = self._make_hierarchical(["alpha", "beta"])
        llm = MockLLM(
            [
                "<descend>\nalpha\nbeta\n</descend>",
                "PAGE: pag-alpha",
                "PAGE: pag-beta",
                "Respuesta secuencial.",
            ]
        )
        result = TapWorkflow([parent], llm).run("¿resumen?")
        assert result.answer == "Respuesta secuencial."
        assert llm._call_count == 4

    def test_parallel_unknown_child_skipped(self, tmp_minds_dir, monkeypatch):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_legacy_sequential", False)

        parent = self._make_hierarchical(["alpha"])
        llm = MockLLM(
            [
                "<descend>\nalpha\ngamma\n</descend>",  # gamma doesn't exist
                "PAGE: pag-alpha",
                "Respuesta sin gamma.",
            ]
        )
        result = TapWorkflow([parent], llm).run("¿resumen?")
        assert result.answer == "Respuesta sin gamma."
        assert llm._call_count == 3

    def test_parallel_empty_scanner_response_uses_parent_only(self, tmp_minds_dir, monkeypatch):
        from copper.llm.mock import MockLLM
        from copper.workflows.tap import TapWorkflow

        monkeypatch.setattr("copper.workflows.tap.settings.copper_tap_legacy_sequential", False)

        parent = self._make_hierarchical(["alpha"])
        llm = MockLLM(
            [
                "<descend>\n</descend>",  # no children selected
                "Respuesta solo padre.",
            ]
        )
        result = TapWorkflow([parent], llm).run("¿resumen?")
        assert result.answer == "Respuesta solo padre."
        assert llm._call_count == 2


# ------------------------------------------------------------------ #
# Phase D — Parser robustness + richer orphan-marker logging          #
# ------------------------------------------------------------------ #


class TestNormalizeXml:
    def test_strips_xml_code_fence(self):
        from copper.workflows.store import _normalize_xml

        text = "```xml\n<wiki_updates></wiki_updates>\n```"
        assert _normalize_xml(text) == "<wiki_updates></wiki_updates>"

    def test_strips_plain_code_fence(self):
        from copper.workflows.store import _normalize_xml

        text = "```\n<wiki_updates></wiki_updates>\n```"
        assert _normalize_xml(text) == "<wiki_updates></wiki_updates>"

    def test_replaces_smart_double_quotes(self):
        from copper.workflows.store import _normalize_xml

        assert _normalize_xml("“test”") == '"test"'

    def test_replaces_smart_single_quotes(self):
        from copper.workflows.store import _normalize_xml

        assert _normalize_xml("‘hello’") == "'hello'"

    def test_strips_whitespace(self):
        from copper.workflows.store import _normalize_xml

        assert _normalize_xml("  <x/>  ") == "<x/>"

    def test_empty_returns_empty(self):
        from copper.workflows.store import _normalize_xml

        assert _normalize_xml("") == ""
        assert _normalize_xml("   ") == ""


class TestParseWikiPages:
    def test_parses_standard_page(self):
        from copper.workflows.store import _parse_wiki_pages

        xml = (
            '<page slug="foo" title="Foo" action="create">'
            "<content>Hello world</content>"
            "</page>"
        )
        pages = _parse_wiki_pages(xml)
        assert len(pages) == 1
        assert pages[0] == ("foo", "Foo", "create", "Hello world", False)

    def test_attributes_in_reverse_order(self):
        from copper.workflows.store import _parse_wiki_pages

        xml = '<page action="update" title="Bar" slug="bar">' "<content>Body</content>" "</page>"
        pages = _parse_wiki_pages(xml)
        assert len(pages) == 1
        slug, title, action, content, was_auto_closed = pages[0]
        assert slug == "bar"
        assert title == "Bar"
        assert action == "update"
        assert content == "Body"
        assert was_auto_closed is False

    def test_truncated_page_auto_closes(self):
        from copper.workflows.store import _parse_wiki_pages

        xml = '<page slug="cut" title="Cut" action="create"><content>Truncated content'
        pages = _parse_wiki_pages(xml)

        assert len(pages) == 1
        assert pages[0][0] == "cut"
        assert "Truncated content" in pages[0][3]
        # The flag must propagate so the consumer can refuse destructive overwrites.
        assert pages[0][4] is True

    def test_multiple_pages_parsed(self):
        from copper.workflows.store import _parse_wiki_pages

        xml = (
            '<page slug="a" title="A" action="create"><content>Alpha</content></page>'
            '<page slug="b" title="B" action="update"><content>Beta</content></page>'
        )
        pages = _parse_wiki_pages(xml)
        assert len(pages) == 2
        assert pages[0][0] == "a"
        assert pages[1][0] == "b"

    def test_markdown_fenced_response_parsed_after_normalize(self):
        from copper.workflows.store import _normalize_xml, _parse_wiki_pages

        raw = (
            "```xml\n"
            '<page slug="x" title="X" action="create">'
            "<content>Content</content></page>\n"
            "```"
        )
        pages = _parse_wiki_pages(_normalize_xml(raw))
        assert len(pages) == 1
        assert pages[0][0] == "x"

    def test_missing_slug_or_title_skipped(self):
        from copper.workflows.store import _parse_wiki_pages

        xml = '<page title="NoSlug" action="create"><content>Body</content></page>'
        assert _parse_wiki_pages(xml) == []

    def test_missing_content_tags_uses_segment_as_body(self):
        """Some models (e.g. gemma4) skip the inner <content> tags. The parser
        must recover the body from the <page>...</page> segment instead of
        silently producing an empty page (which would overwrite real content)."""
        from copper.workflows.store import _parse_wiki_pages

        xml = (
            '<page slug="nightblood" title="Nightblood" action="create">'
            "On the planet Nalthis, Nightblood is a sentient sword."
            "</page>"
        )
        pages = _parse_wiki_pages(xml)
        assert len(pages) == 1
        slug, title, _, content, was_auto_closed = pages[0]
        assert slug == "nightblood"
        assert title == "Nightblood"
        assert "On the planet Nalthis" in content
        # </page> bounded the body — not a truncation.
        assert was_auto_closed is False

    def test_missing_content_and_page_close_marks_auto_closed(self):
        """When neither <content> nor </page> is present, the body bounds are
        unreliable. The flag must be True so the consumer refuses to overwrite."""
        from copper.workflows.store import _parse_wiki_pages

        xml = '<page slug="cut" title="Cut" action="update">Partial body without closing'
        pages = _parse_wiki_pages(xml)
        assert len(pages) == 1
        assert pages[0][0] == "cut"
        assert pages[0][4] is True

    def test_empty_content_tags_skipped(self):
        """A <page> with explicit but empty <content></content> must not be
        persisted — would silently wipe an existing page on update."""
        from copper.workflows.store import _parse_wiki_pages

        xml = '<page slug="empty" title="Empty" action="create"><content></content></page>'
        assert _parse_wiki_pages(xml) == []

    def test_empty_page_skipped(self):
        """A <page>...</page> with no body at all must not be persisted."""
        from copper.workflows.store import _parse_wiki_pages

        xml = '<page slug="ghost" title="Ghost" action="create"></page>'
        assert _parse_wiki_pages(xml) == []


class TestApplyWikiUpdatesDestructiveGuard:
    """A truncated LLM response must never overwrite an existing page with a
    partial stub. The original Yu-Thorak regression (2026-05-11) was caused by
    this exact scenario — a truncated update wrote a 1-line stub over a full
    bestiary entry."""

    def test_auto_closed_update_does_not_overwrite_existing_page(self, tmp_path):
        from copper.core.wiki import WikiManager
        from copper.workflows.store import _apply_wiki_updates

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        wiki = WikiManager(wiki_dir)
        original_body = "Full bestiary entry. Lots of stats and abilities."
        wiki.create_page("yu-thorak", "Yu-Thorak", original_body)

        # Truncated LLM response: <content> opens but never closes.
        truncated = (
            '<page slug="yu-thorak" title="Yu-Thorak" action="update">' "<content>Tier 2 Boss"
        )
        pages_written = _apply_wiki_updates(truncated, "src.pdf", wiki)

        # yu-thorak must NOT be in the written list.
        assert "yu-thorak" not in pages_written
        # Existing content preserved — never overwritten by the truncated stub.
        body_after = wiki.page("yu-thorak").body
        assert original_body in body_after
        assert "Tier 2 Boss" not in body_after

    def test_auto_closed_create_for_new_page_still_written(self, tmp_path):
        """Partial content on a new page is acceptable — something is better
        than nothing when the page didn't exist before."""
        from copper.core.wiki import WikiManager
        from copper.workflows.store import _apply_wiki_updates

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        wiki = WikiManager(wiki_dir)

        truncated = (
            '<page slug="newentry" title="New Entry" action="create">'
            "<content>Partial but useful body"
        )
        pages_written = _apply_wiki_updates(truncated, "src.pdf", wiki)

        assert "newentry" in pages_written
        assert "Partial but useful body" in wiki.page("newentry").body


class TestSendWithRetryEmptyResponse:
    """Verify empty response triggers its own hint, not the malformed-XML hint."""

    def _recording_llm(self, responses):
        """Return (llm, received_contents, call_count_ref) for inspecting call inputs."""
        from copper.llm.base import LLMResponse

        received = []
        call_count = [0]

        class _RecordingLLM:
            def complete(self_, msgs, **kw):
                received.append(msgs[-1].content)
                idx = call_count[0] % len(responses)
                call_count[0] += 1
                return LLMResponse(text=responses[idx], tokens_used=0, metadata={})

        return _RecordingLLM(), received, call_count

    def test_empty_response_retried_with_empty_hint(self):
        from copper.workflows.store import _send_with_retry, _EMPTY_RETRY_HINT

        llm, received, calls = self._recording_llm(
            [
                "",  # attempt 0: empty
                '<wiki_updates><page slug="p" title="P" action="create">'
                "<content>ok</content></page></wiki_updates>",
            ]
        )
        text, _, _ = _send_with_retry(llm, "system", "user", max_retries=2)

        assert calls[0] == 2
        assert _EMPTY_RETRY_HINT.splitlines()[0] in received[1]
        assert "<page" in text

    def test_malformed_xml_retried_with_malformed_hint(self):
        from copper.workflows.store import _send_with_retry, _MALFORMED_RETRY_HINT

        llm, received, calls = self._recording_llm(
            [
                "This is not XML at all",  # attempt 0: malformed
                '<wiki_updates><page slug="p" title="P" action="create">'
                "<content>ok</content></page></wiki_updates>",
            ]
        )
        text, _, _ = _send_with_retry(llm, "system", "user", max_retries=2)

        assert calls[0] == 2
        assert _MALFORMED_RETRY_HINT.splitlines()[0] in received[1]

    def test_max_retries_two_honored(self):
        from copper.workflows.store import _send_with_retry, _MAX_XML_RETRIES

        assert _MAX_XML_RETRIES == 2
        llm, _, calls = self._recording_llm(["bad", "bad", "bad"])
        _send_with_retry(llm, "system", "user", max_retries=2)
        assert calls[0] == 3  # attempts 0, 1, 2


class TestOrphanMarkerLog:
    """D.1 — orphan-drop warning includes entity name and keywords."""

    def test_orphan_warning_includes_entity_and_keywords(self, mind, tmp_path, monkeypatch):
        import copper.workflows.store as store_module
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        warnings: list[str] = []

        class _FakeLogger:
            def warning(self, msg, *a, **k):
                warnings.append(str(msg))

            def info(self, *a, **k):
                pass

        monkeypatch.setattr(store_module, "logger", _FakeLogger())

        marker = (
            "[Visual on page 8, image 0: Yu-Thorak. A gargantuan creature. "
            "(Keywords: yu-thorak, gargantuan, creature)]"
        )
        source = tmp_path / "src.md"
        source.write_text(f"# Intro\n\n{marker}\n\nSome text.\n")

        # LLM writes an unrelated page — marker scores below confidence floor.
        llm = MockLLM(
            [
                '<wiki_updates><page slug="intro" title="Intro" action="create">'
                "<content>Some text.</content></page></wiki_updates>",
                "Meta.",
            ]
        )
        StoreWorkflow(mind, llm).run(source)

        orphan_warnings = [w for w in warnings if "Dropping orphan marker" in w]
        assert orphan_warnings, "Expected at least one orphan-drop warning"
        msg = orphan_warnings[0]
        assert "Yu-Thorak" in msg
        assert "yu-thorak" in msg


# ------------------------------------------------------------------ #
# Phase E — Visual marker carry-over between ingots                  #
# ------------------------------------------------------------------ #


class TestInjectMissingMarkers:
    """Unit tests for _inject_missing_visual_markers carry-over return value."""

    def _write_page(self, wiki, slug, body):
        wiki.upsert_page(slug=slug, title=slug.title(), body=body)

    def test_unplaceable_marker_returned_as_unplaced(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.store import _inject_missing_visual_markers

        wiki = WikiManager(mind.wiki_dir)
        marker = "[Visual on page 1, image 0: Widget. (Keywords: widget)]"
        self._write_page(wiki, "unrelated", "Nothing relevant here.")

        unplaced = _inject_missing_visual_markers(
            chunk=f"text\n\n{marker}\n",
            page_slugs=["unrelated"],
            wiki=wiki,
        )
        assert len(unplaced) == 1
        assert "Widget" in unplaced[0]

    def test_carry_marker_placed_when_matching_page_exists(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.store import _inject_missing_visual_markers

        wiki = WikiManager(mind.wiki_dir)
        marker = (
            "[Visual on page 8, image 0: Yu-Thorak. A gargantuan creature. "
            "(Keywords: yu-thorak, gargantuan, creature)]"
        )
        # Ingot 1: no matching page → marker returned as unplaced.
        self._write_page(wiki, "intro", "Intro text.")
        unplaced = _inject_missing_visual_markers(
            chunk=f"Intro.\n\n{marker}\n",
            page_slugs=["intro"],
            wiki=wiki,
            carry_markers=[],
        )
        assert len(unplaced) == 1

        # Ingot 2: yu-thorak page now exists → carry marker placed.
        self._write_page(wiki, "yu-thorak", "Yu-Thorak description.")
        unplaced2 = _inject_missing_visual_markers(
            chunk="Yu-Thorak is a creature.",
            page_slugs=["yu-thorak"],
            wiki=wiki,
            carry_markers=unplaced,
        )
        assert unplaced2 == []
        assert "Visual on page 8" in wiki.page("yu-thorak").body

    def test_carry_marker_still_unplaced_after_two_ingots(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.store import _inject_missing_visual_markers

        wiki = WikiManager(mind.wiki_dir)
        marker = "[Visual on page 3, image 0: SpecialThing. (Keywords: specialthing)]"

        self._write_page(wiki, "page-a", "Nothing relevant.")
        unplaced1 = _inject_missing_visual_markers(
            chunk=f"text\n\n{marker}\n",
            page_slugs=["page-a"],
            wiki=wiki,
            carry_markers=[],
        )
        assert len(unplaced1) == 1

        self._write_page(wiki, "page-b", "Also nothing.")
        unplaced2 = _inject_missing_visual_markers(
            chunk="another chunk",
            page_slugs=["page-b"],
            wiki=wiki,
            carry_markers=unplaced1,
        )
        assert len(unplaced2) == 1

    def test_no_page_slugs_returns_all_candidates(self, mind):
        from copper.core.wiki import WikiManager
        from copper.workflows.store import _inject_missing_visual_markers

        wiki = WikiManager(mind.wiki_dir)
        carry = ["[Visual on page 1, image 0: Foo. (Keywords: foo)]"]
        marker = "[Visual on page 2, image 0: Bar. (Keywords: bar)]"

        unplaced = _inject_missing_visual_markers(
            chunk=f"\n{marker}\n",
            page_slugs=[],
            wiki=wiki,
            carry_markers=carry,
        )
        assert len(unplaced) == 2


class TestCarryOverIntegration:
    """Integration test: marker from ingot 1 placed in ingot 2 via StoreWorkflow.run()."""

    def test_two_ingot_carry_over_no_orphan_warning(self, mind, tmp_path, monkeypatch):
        import copper.workflows.store as store_module
        from copper.workflows.store import StoreWorkflow
        from copper.llm.mock import MockLLM

        warnings: list[str] = []

        class _FakeLogger:
            def warning(self, msg, *a, **k):
                warnings.append(str(msg))

            def info(self, *a, **k):
                pass

        monkeypatch.setattr(store_module, "logger", _FakeLogger())

        marker = (
            "[Visual on page 8, image 0: Yu-Thorak. A gargantuan creature. "
            "(Keywords: yu-thorak, gargantuan, creature)]"
        )
        chunk1 = f"Some intro text.\n\n{marker}\n"
        chunk2 = "Yu-Thorak appeared on the battlefield."

        class _MockRegistry:
            def to_chunks(self, *a, **kw):
                return [chunk1, chunk2]

        monkeypatch.setattr(store_module, "default_registry", lambda: _MockRegistry())

        source = tmp_path / "src.md"
        source.write_text("content")

        llm = MockLLM(
            [
                # Ingot 1: writes an unrelated page (marker can't be placed yet)
                '<wiki_updates><page slug="intro" title="Intro" action="create">'
                "<content>Some intro text.</content></page></wiki_updates>",
                # Ingot 2: writes the Yu-Thorak page (carry marker should land here)
                '<wiki_updates><page slug="yu-thorak" title="Yu-Thorak" action="create">'
                "<content>Yu-Thorak appeared on the battlefield.</content></page></wiki_updates>",
                # Polish call (multi-ingot triggers polish)
                "Polish report.",
                # Meta call inside polish
                "Meta summary.",
            ]
        )

        StoreWorkflow(mind, llm).run(source, no_route=True)

        yu_thorak = mind.wiki_dir / "yu-thorak.md"
        assert yu_thorak.exists()
        assert "Visual on page 8" in yu_thorak.read_text()
        assert not any("Dropping orphan marker" in w for w in warnings)

    def test_carry_buffer_cap_drops_oldest_with_warning(self, mind, tmp_path, monkeypatch):
        import copper.workflows.store as store_module
        from copper.workflows.store import StoreWorkflow, _CARRY_MARKER_CAP
        from copper.llm.mock import MockLLM

        warnings: list[str] = []

        class _FakeLogger:
            def warning(self, msg, *a, **k):
                warnings.append(str(msg))

            def info(self, *a, **k):
                pass

        monkeypatch.setattr(store_module, "logger", _FakeLogger())

        # Build chunk1 with _CARRY_MARKER_CAP + 1 distinct unplaceable markers.
        markers = [
            f"[Visual on page {n}, image 0: Entity{n}. (Keywords: entity{n})]"
            for n in range(_CARRY_MARKER_CAP + 1)
        ]
        chunk1 = "Intro.\n\n" + "\n\n".join(markers)
        chunk2 = "Unrelated second chunk."

        class _MockRegistry:
            def to_chunks(self, *a, **kw):
                return [chunk1, chunk2]

        monkeypatch.setattr(store_module, "default_registry", lambda: _MockRegistry())

        source = tmp_path / "src.md"
        source.write_text("content")

        llm = MockLLM(
            [
                # Ingot 1: writes an unrelated page — all markers remain unplaced
                '<wiki_updates><page slug="unrelated" title="Unrelated" action="create">'
                "<content>Nothing related.</content></page></wiki_updates>",
                # Ingot 2: also unrelated
                '<wiki_updates><page slug="unrelated2" title="Unrelated2" action="create">'
                "<content>Still nothing.</content></page></wiki_updates>",
                "Polish report.",
                "Meta summary.",
            ]
        )

        StoreWorkflow(mind, llm).run(source, no_route=True)

        cap_warnings = [w for w in warnings if "Carry-over buffer full" in w]
        assert cap_warnings, "Expected at least one cap warning when buffer exceeds limit"
