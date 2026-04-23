"""Routes for coppermind management: forge, list, status, link, graph.

IMPORTANT: literal-path routes (/link, /graph/all) must be declared
BEFORE path-parameter routes (/{name}) to avoid shadowing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from copper.api.models import (
    ForgeRequest,
    GraphNode,
    GraphResponse,
    LinkRequest,
    MindSummary,
    WikiPageUpdateRequest,
)
from copper.core.coppermind import CopperMind

router = APIRouter(prefix="/minds", tags=["minds"])


# ------------------------------------------------------------------ #
# Collection routes (no path params)                                  #
# ------------------------------------------------------------------ #


@router.get("", response_model=list[MindSummary])
def list_minds():
    """List all copperminds."""
    return [_to_summary(m) for m in CopperMind.list_all()]


@router.post("", response_model=MindSummary, status_code=status.HTTP_201_CREATED)
def forge_mind(body: ForgeRequest):
    """Forge a new coppermind."""
    try:
        mind = CopperMind.forge(body.name, body.topic, body.model)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_summary(mind)


# ------------------------------------------------------------------ #
# Literal sub-paths — must come before /{name}                        #
# ------------------------------------------------------------------ #


@router.post("/link", status_code=status.HTTP_204_NO_CONTENT)
def link_minds(body: LinkRequest):
    """Link two copperminds bidirectionally."""
    mind_a = _get_or_404(body.name_a)
    mind_b = _get_or_404(body.name_b)
    try:
        mind_a.link(mind_b)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/link", status_code=status.HTTP_204_NO_CONTENT)
def unlink_minds(body: LinkRequest):
    """Remove a link between two copperminds."""
    mind_a = _get_or_404(body.name_a)
    mind_b = _get_or_404(body.name_b)
    mind_a.unlink(mind_b)


@router.get("/prompts/tap", tags=["prompts"])
def list_tap_personalities():
    """Return available tap personalities (name + description from their YAML)."""
    from pathlib import Path

    import yaml

    from copper.prompts import list_prompts

    # PromptManager discards the YAML description field, so we re-read the
    # files directly to surface it to the UI. Check built-in and user dirs.
    descriptions: dict[str, str] = {}
    from copper.config import settings

    search_dirs = [Path(__file__).resolve().parents[2] / "prompts"]
    if settings.copper_user_prompts_dir:
        search_dirs.append(Path(settings.copper_user_prompts_dir).expanduser())

    for directory in search_dirs:
        if not directory.exists():
            continue
        for yaml_file in directory.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f) or {}
                name = data.get("name")
                if name and name.startswith("tap."):
                    desc = (data.get("description") or "").strip()
                    # First non-empty description wins per name (user dir appended
                    # last, so user descriptions take precedence on collision).
                    descriptions[name] = desc
            except (OSError, yaml.YAMLError):
                # Skip malformed files silently — the main PromptManager loader
                # already logs these warnings at startup.
                continue

    return [
        {"name": name, "description": descriptions.get(name, "")}
        for name in list_prompts(prefix="tap.")
    ]


@router.get("/graph/all", response_model=GraphResponse, tags=["graph"])
def get_graph():
    """Return the full coppermind link graph."""
    minds = CopperMind.list_all()
    nodes = [
        GraphNode(name=m.name, topic=m.config.topic, links=m.config.linked_minds) for m in minds
    ]
    edge_count = sum(len(n.links) for n in nodes) // 2
    return GraphResponse(nodes=nodes, edge_count=edge_count)


# ------------------------------------------------------------------ #
# Individual mind routes — /{name} must come last                     #
# ------------------------------------------------------------------ #


@router.get("/{name}", response_model=MindSummary)
def get_mind(name: str):
    """Get stats for a single coppermind."""
    mind = _get_or_404(name)
    return _to_summary(mind)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mind(name: str):
    """Delete a coppermind (irreversible)."""
    import shutil

    mind = _get_or_404(name)
    shutil.rmtree(mind.path)


@router.get("/{name}/wiki", response_model=list[str])
def list_wiki_pages(name: str):
    """List all wiki page slugs for a coppermind."""
    mind = _get_or_404(name)
    return [p.stem for p in mind.wiki_pages()]


@router.get("/{name}/images/{filename}")
def get_mind_image(name: str, filename: str):
    """Serve an image extracted during PDF ingestion.

    Images live under ``<mind>/raw/images/<filename>`` and are referenced from
    wiki pages via ``[Visual on page N, image M: ...]`` markers. The UI
    post-processes those markers to produce <img> tags pointing here.
    """
    from fastapi.responses import FileResponse

    mind = _get_or_404(name)
    # Restrict to bare filenames — no traversal outside the images folder.
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid image filename.")
    image_path = mind.raw_dir / "images" / filename
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail=f"Image '{filename}' not found.")
    return FileResponse(image_path)


@router.get("/{name}/wiki/{slug}")
def get_wiki_page(name: str, slug: str):
    """Read a specific wiki page. ``body`` is the markdown without frontmatter."""
    from copper.core.wiki import WikiManager

    mind = _get_or_404(name)
    wm = WikiManager(mind.wiki_dir)
    page = wm.page(slug)
    if not page.exists():
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found.")
    return {
        "slug": slug,
        "content": page.raw,
        "body": page.body,
        "frontmatter": page.frontmatter,
    }


@router.put("/{name}/wiki/{slug}")
def update_wiki_page(name: str, slug: str, request: WikiPageUpdateRequest):
    """Overwrite a wiki page body (frontmatter is preserved, last_updated refreshed)."""
    from copper.core.wiki import WikiManager

    mind = _get_or_404(name)
    wm = WikiManager(mind.wiki_dir)
    try:
        page = wm.update_page(slug, request.body)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    mind.append_log("edit", f"Página '{slug}' editada manualmente")
    return {"slug": slug, "body": page.body, "frontmatter": page.frontmatter}


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _get_or_404(name: str) -> CopperMind:
    try:
        return CopperMind.get(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _to_summary(mind: CopperMind) -> MindSummary:
    stats = mind.stats()
    return MindSummary(
        name=stats["name"],
        topic=stats["topic"],
        raw_sources=stats["raw_sources"],
        wiki_pages=stats["wiki_pages"],
        linked_minds=stats["linked_minds"],
        created=mind.config.created[:10],
    )
