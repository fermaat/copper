"""API integration tests using FastAPI TestClient."""

from __future__ import annotations

import io
import pytest

# ------------------------------------------------------------------ #
# Fixtures                                                            #
# ------------------------------------------------------------------ #


@pytest.fixture
def tmp_minds_dir(tmp_path, monkeypatch):
    """Redirect MINDS_DIR to a temp directory."""
    import copper.core.coppermind as cm_module

    monkeypatch.setattr(cm_module, "MINDS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def client(tmp_minds_dir):
    """TestClient with FastAPI app and MockLLM wired in."""
    from fastapi.testclient import TestClient
    from copper.api.app import create_app
    from copper.api import deps
    from copper.llm.mock import MockLLM

    app = create_app()
    # Override the LLM dependency with a mock
    app.dependency_overrides[deps.get_llm] = lambda: MockLLM()

    return TestClient(app)


@pytest.fixture
def mind_in_db(tmp_minds_dir):
    """Pre-forge a coppermind for tests that need one."""
    from copper.core.coppermind import CopperMind

    return CopperMind.forge("test-mind", "artificial intelligence")


# ------------------------------------------------------------------ #
# Minds CRUD                                                          #
# ------------------------------------------------------------------ #


class TestMindsRoutes:
    def test_list_empty(self, client):
        res = client.get("/minds")
        assert res.status_code == 200
        assert res.json() == []

    def test_forge_mind(self, client):
        res = client.post("/minds", json={"name": "alpha", "topic": "AI research"})
        assert res.status_code == 201
        data = res.json()
        assert data["name"] == "alpha"
        assert data["topic"] == "AI research"

    def test_forge_duplicate_returns_409(self, client):
        client.post("/minds", json={"name": "dup", "topic": "topic"})
        res = client.post("/minds", json={"name": "dup", "topic": "topic"})
        assert res.status_code == 409

    def test_list_returns_forged_minds(self, client):
        client.post("/minds", json={"name": "a", "topic": "A"})
        client.post("/minds", json={"name": "b", "topic": "B"})
        res = client.get("/minds")
        names = [m["name"] for m in res.json()]
        assert "a" in names and "b" in names

    def test_get_mind(self, client, mind_in_db):
        res = client.get("/minds/test-mind")
        assert res.status_code == 200
        assert res.json()["name"] == "test-mind"

    def test_get_nonexistent_returns_404(self, client):
        res = client.get("/minds/ghost")
        assert res.status_code == 404

    def test_delete_mind(self, client, mind_in_db):
        res = client.delete("/minds/test-mind")
        assert res.status_code == 204
        assert client.get("/minds/test-mind").status_code == 404

    def test_list_wiki_pages_empty(self, client, mind_in_db):
        res = client.get("/minds/test-mind/wiki")
        assert res.status_code == 200
        assert res.json() == []

    def test_get_wiki_page_not_found(self, client, mind_in_db):
        res = client.get("/minds/test-mind/wiki/nonexistent")
        assert res.status_code == 404

    def test_update_wiki_page(self, client, mind_in_db):
        from copper.core.wiki import WikiManager

        # Seed a page
        wm = WikiManager(mind_in_db.wiki_dir)
        wm.create_page(slug="alpha", title="Alpha", body="Original body")

        res = client.put("/minds/test-mind/wiki/alpha", json={"body": "New body content"})
        assert res.status_code == 200
        data = res.json()
        assert data["slug"] == "alpha"
        assert "New body content" in data["body"]

        # Round-trip
        got = client.get("/minds/test-mind/wiki/alpha").json()
        assert "New body content" in got["body"]

    def test_update_wiki_page_not_found(self, client, mind_in_db):
        res = client.put("/minds/test-mind/wiki/nonexistent", json={"body": "x"})
        assert res.status_code == 404

    def test_get_mind_image_missing(self, client, mind_in_db):
        res = client.get("/minds/test-mind/images/nonexistent-p1-img0.png")
        assert res.status_code == 404

    def test_get_mind_image_rejects_traversal(self, client, mind_in_db):
        res = client.get("/minds/test-mind/images/..%2Fsecret")
        # Either URL decoding catches the traversal (400) or FastAPI routes the
        # raw path (404). Both are acceptable — just not 200.
        assert res.status_code in (400, 404)

    def test_get_mind_image_serves_existing(self, client, mind_in_db):
        images_dir = mind_in_db.raw_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        # 1x1 PNG magic bytes
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
            b"\x00\x05\xfe\x02\xfe\xa7\x9a\x9c\xd4"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        (images_dir / "demo-p1-img0.png").write_bytes(png_bytes)

        res = client.get("/minds/test-mind/images/demo-p1-img0.png")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/")


# ------------------------------------------------------------------ #
# Linking via API                                                     #
# ------------------------------------------------------------------ #


class TestLinkRoutes:
    def test_link_and_reflect_in_stats(self, client):
        client.post("/minds", json={"name": "x", "topic": "X"})
        client.post("/minds", json={"name": "y", "topic": "Y"})

        res = client.post("/minds/link", json={"name_a": "x", "name_b": "y"})
        assert res.status_code == 204

        stats = client.get("/minds/x").json()
        assert "y" in stats["linked_minds"]

    def test_unlink(self, client):
        client.post("/minds", json={"name": "p", "topic": "P"})
        client.post("/minds", json={"name": "q", "topic": "Q"})
        client.post("/minds/link", json={"name_a": "p", "name_b": "q"})

        import json as _json

        res = client.request(
            "DELETE",
            "/minds/link",
            content=_json.dumps({"name_a": "p", "name_b": "q"}),
            headers={"Content-Type": "application/json"},
        )
        assert res.status_code == 204

        stats = client.get("/minds/p").json()
        assert "q" not in stats["linked_minds"]

    def test_graph_endpoint(self, client):
        client.post("/minds", json={"name": "g1", "topic": "G1"})
        client.post("/minds", json={"name": "g2", "topic": "G2"})
        client.post("/minds/link", json={"name_a": "g1", "name_b": "g2"})

        res = client.get("/minds/graph/all")
        assert res.status_code == 200
        data = res.json()
        assert data["edge_count"] == 1
        names = [n["name"] for n in data["nodes"]]
        assert "g1" in names and "g2" in names


# ------------------------------------------------------------------ #
# Workflows                                                           #
# ------------------------------------------------------------------ #


class TestWorkflowRoutes:
    def test_store_file(self, client, mind_in_db, tmp_path):
        content = b"# Test Article\n\nSome content about AI."
        res = client.post(
            "/minds/test-mind/store",
            files={"file": ("article.md", io.BytesIO(content), "text/markdown")},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["source"] == "article.md"
        assert len(data["pages_written"]) >= 1

    def test_tap_returns_answer(self, client, mind_in_db):
        res = client.post(
            "/minds/test-mind/tap",
            json={"question": "What is AI?"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["question"] == "What is AI?"
        assert len(data["answer"]) > 0
        assert data["minds_used"] == ["test-mind"]

    def test_tap_with_links(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind
        from copper.api import deps
        from copper.llm.mock import MockLLM

        mind_a = CopperMind.forge("linked-a", "topic A")
        mind_b = CopperMind.forge("linked-b", "topic B")
        mind_a.link(mind_b)

        res = client.post(
            "/minds/linked-a/tap",
            json={"question": "combined query", "with_links": True},
        )
        assert res.status_code == 200
        assert set(res.json()["minds_used"]) == {"linked-a", "linked-b"}

    def test_polish_returns_report(self, client, mind_in_db):
        res = client.post("/minds/test-mind/polish")
        assert res.status_code == 200
        data = res.json()
        assert data["mind_name"] == "test-mind"
        assert len(data["report"]) > 0

    def test_tap_stream_returns_sse(self, client, mind_in_db):
        res = client.post(
            "/minds/test-mind/tap/stream",
            json={"question": "stream test"},
        )
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        assert b"data:" in res.content


# ------------------------------------------------------------------ #
# UI                                                                  #
# ------------------------------------------------------------------ #


class TestUI:
    def test_root_returns_html(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
        assert b"COPPER" in res.content


# ------------------------------------------------------------------ #
# Phase 7 — tree API + child-mind routing                             #
# ------------------------------------------------------------------ #


class TestTreeAPI:
    def test_list_includes_children(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        parent = CopperMind.forge("aventura", "Adventure")
        parent.forge_child("parte-1", "La Convocatoria")
        parent.forge_child("parte-2", "El Viaje")

        res = client.get("/minds")
        assert res.status_code == 200
        data = res.json()
        aventura = next(m for m in data if m["name"] == "aventura")
        assert len(aventura["children"]) == 2
        child_names = {c["name"] for c in aventura["children"]}
        assert child_names == {"parte-1", "parte-2"}

    def test_get_child_mind_by_path(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        parent = CopperMind.forge("aventura", "Adventure")
        parent.forge_child("parte-1", "La Convocatoria")

        res = client.get("/minds/aventura/parte-1")
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "parte-1"
        assert data["topic"] == "La Convocatoria"
        assert data["is_root"] is False

    def test_get_child_wiki_pages_by_path(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        parent = CopperMind.forge("aventura", "Adventure")
        child = parent.forge_child("parte-1", "La Convocatoria")
        child.wiki.create_page("szeth", "Szeth", "Szeth content.")

        res = client.get("/minds/aventura/parte-1/wiki")
        assert res.status_code == 200
        assert "szeth" in res.json()

    def test_get_child_wiki_page_by_path(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        parent = CopperMind.forge("aventura", "Adventure")
        child = parent.forge_child("parte-1", "La Convocatoria")
        child.wiki.create_page("szeth", "Szeth", "Szeth content.")

        res = client.get("/minds/aventura/parte-1/wiki/szeth")
        assert res.status_code == 200
        assert res.json()["slug"] == "szeth"

    def test_unknown_child_returns_404(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        CopperMind.forge("aventura", "Adventure")
        res = client.get("/minds/aventura/noexiste")
        assert res.status_code == 404

    def test_root_mind_is_root_true(self, client, tmp_minds_dir):
        from copper.core.coppermind import CopperMind

        CopperMind.forge("solo", "A lone mind")
        res = client.get("/minds/solo")
        assert res.status_code == 200
        assert res.json()["is_root"] is True
        assert res.json()["children"] == []


# ------------------------------------------------------------------ #
# Phase 7 — watch with descendants                                    #
# ------------------------------------------------------------------ #


class TestWatchDescendants:
    def test_watch_schedules_all_descendants(self, tmp_minds_dir, monkeypatch):
        """watch_raw_dir should schedule one observer per descendant raw dir."""
        from copper.core.coppermind import CopperMind
        from copper.llm.mock import MockLLM

        parent = CopperMind.forge("aventura", "Adventure")
        parent.forge_child("parte-1", "La Convocatoria")
        parent.forge_child("parte-2", "El Viaje")

        scheduled = []

        class FakeObserver:
            def schedule(self, handler, path, recursive=False):
                scheduled.append(path)

            def start(self):
                pass

            def is_alive(self):
                return False

            def stop(self):
                pass

            def join(self):
                pass

        # Observer is imported inside watch_raw_dir from watchdog.observers,
        # so we patch it there.
        import watchdog.observers as wo_module

        monkeypatch.setattr(wo_module, "Observer", FakeObserver)

        from copper.watch import watch_raw_dir

        watch_raw_dir(parent, MockLLM([]))

        # Root + 2 children = 3 raw dirs watched
        assert len(scheduled) == 3
