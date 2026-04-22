"""Tests for Phase 2: linking, --with-links, and cross-mind detection."""

import pytest


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def two_minds(tmp_minds_dir):
    from copper.core.coppermind import CopperMind

    alpha = CopperMind.forge("alpha", "inteligencia artificial")
    beta = CopperMind.forge("beta", "cosmere lore")
    return alpha, beta


# ------------------------------------------------------------------ #
# Link / Unlink                                                       #
# ------------------------------------------------------------------ #


class TestLinking:
    def test_link_is_bidirectional(self, two_minds):
        alpha, beta = two_minds
        alpha.link(beta)

        # Reload from disk
        from copper.core.coppermind import CopperMind

        alpha2 = CopperMind.get("alpha")
        beta2 = CopperMind.get("beta")

        assert "beta" in alpha2.config.linked_minds
        assert "alpha" in beta2.config.linked_minds

    def test_link_self_raises(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        mind = CopperMind.forge("solo", "tema")
        with pytest.raises(ValueError):
            mind.link(mind)

    def test_link_nonexistent_raises(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        mind = CopperMind.forge("real", "tema")
        ghost = CopperMind(mind.path.parent / "ghost")
        with pytest.raises(FileNotFoundError):
            mind.link(ghost)

    def test_link_idempotent(self, two_minds):
        alpha, beta = two_minds
        alpha.link(beta)
        alpha.link(beta)  # Should not duplicate

        from copper.core.coppermind import CopperMind

        alpha2 = CopperMind.get("alpha")
        assert alpha2.config.linked_minds.count("beta") == 1

    def test_unlink_removes_both_sides(self, two_minds):
        alpha, beta = two_minds
        alpha.link(beta)
        alpha.unlink(beta)

        from copper.core.coppermind import CopperMind

        alpha2 = CopperMind.get("alpha")
        beta2 = CopperMind.get("beta")
        assert "beta" not in alpha2.config.linked_minds
        assert "alpha" not in beta2.config.linked_minds

    def test_linked_minds_returns_objects(self, two_minds):
        alpha, beta = two_minds
        alpha.link(beta)

        from copper.core.coppermind import CopperMind

        alpha2 = CopperMind.get("alpha")
        linked = alpha2.linked_minds()
        assert len(linked) == 1
        assert linked[0].name == "beta"

    def test_linked_minds_skips_deleted(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        import shutil

        alpha = CopperMind.forge("alpha", "tema")
        beta = CopperMind.forge("beta", "tema")
        alpha.link(beta)
        shutil.rmtree(beta.path)  # Delete beta

        alpha2 = CopperMind.get("alpha")
        # Should not raise, just return empty
        assert alpha2.linked_minds() == []

    def test_expand_with_links(self, two_minds):
        alpha, beta = two_minds
        alpha.link(beta)

        from copper.core.coppermind import CopperMind

        alpha2 = CopperMind.get("alpha")
        expanded = alpha2.expand_with_links()
        names = {m.name for m in expanded}
        assert names == {"alpha", "beta"}

    def test_expand_with_links_no_duplicates(self, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        alpha = CopperMind.forge("alpha", "A")
        beta = CopperMind.forge("beta", "B")
        gamma = CopperMind.forge("gamma", "C")

        alpha.link(beta)
        alpha.link(gamma)
        beta.link(gamma)

        alpha2 = CopperMind.get("alpha")
        expanded = alpha2.expand_with_links()
        names = [m.name for m in expanded]
        assert len(names) == len(set(names))  # No duplicates

    def test_link_updates_log(self, two_minds):
        alpha, beta = two_minds
        alpha.link(beta)
        log = alpha.log_path.read_text()
        assert "link" in log
        assert "beta" in log


# ------------------------------------------------------------------ #
# Cross-mind detection in TapWorkflow                                 #
# ------------------------------------------------------------------ #


class TestCrossMindTap:
    def test_tap_multi_includes_connections(self, two_minds):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        alpha, beta = two_minds
        answer = (
            "Los transformers usan atención. [Fuente: transformers]\n"
            "[Connection: alpha ↔ beta: both use memory mechanisms]"
        )
        llm = MockLLM([answer])
        workflow = TapWorkflow([alpha, beta], llm)
        result = workflow.run("¿qué comparten?")

        assert len(result.connections) == 1
        assert "alpha" in result.connections[0]
        assert "beta" in result.connections[0]

    def test_tap_single_no_connections(self, two_minds):
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM

        alpha, _ = two_minds
        llm = MockLLM(["Respuesta simple sin conexiones."])
        workflow = TapWorkflow([alpha], llm)
        result = workflow.run("pregunta simple")

        assert result.connections == []

    def test_tap_multi_prompt_includes_cross_mind_instructions(self, two_minds):
        from copper.workflows.tap import _build_tap_prompt

        prompt = _build_tap_prompt("contexto", "pregunta", multi=True)
        assert "MULTIPLE copperminds" in prompt
        assert "Connection" in prompt

    def test_tap_single_prompt_no_cross_mind_instructions(self, two_minds):
        from copper.workflows.tap import _build_tap_prompt

        prompt = _build_tap_prompt("contexto", "pregunta", multi=False)
        assert "VARIAS mentecobres" not in prompt

    def test_with_links_expansion_in_tap(self, two_minds):
        """Simulate --with-links: alpha.expand_with_links() feeds into TapWorkflow."""
        from copper.workflows.tap import TapWorkflow
        from copper.llm.mock import MockLLM
        from copper.core.coppermind import CopperMind

        alpha, beta = two_minds
        alpha.link(beta)

        alpha2 = CopperMind.get("alpha")
        minds = alpha2.expand_with_links()

        llm = MockLLM(["Respuesta con ambas mentes."])
        workflow = TapWorkflow(minds, llm)
        result = workflow.run("pregunta con links")

        assert set(result.minds_used) == {"alpha", "beta"}
