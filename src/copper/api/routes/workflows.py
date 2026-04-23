"""Routes for the three core workflows: store, tap, polish."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from copper.api.deps import get_ingest_describer, get_store_llm, get_tap_llm
from copper.api.models import PolishResponse, StoreResponse, TapRequest, TapResponse
from copper.api.routes.minds import _get_or_404
from copper.core.coppermind import CopperMind
from copper.workflows.polish import PolishWorkflow
from copper.workflows.store import StoreWorkflow
from copper.workflows.tap import TapWorkflow

router = APIRouter(prefix="/minds", tags=["workflows"])


# ------------------------------------------------------------------ #
# Store                                                               #
# ------------------------------------------------------------------ #


@router.post("/{name}/store", response_model=StoreResponse)
async def store(
    name: str,
    file: UploadFile = File(...),
):
    """Upload a file and ingest it into the coppermind."""
    mind = _get_or_404(name)
    llm = get_store_llm(mind)
    describer = get_ingest_describer(mind)
    workflow = StoreWorkflow(mind, llm, image_describer=describer)

    # Save directly to raw/ under the original filename so StoreWorkflow
    # preserves the correct source name in the wiki (avoids tempfile slugs).
    original_name = file.filename or "source.md"
    raw_path = mind.raw_dir / original_name
    content = await file.read()
    raw_path.write_bytes(content)

    result = workflow.run(raw_path)

    return StoreResponse(
        source=result.source,
        pages_written=result.pages_written,
        tokens_used=result.tokens_used,
        cost_usd=result.cost_usd,
    )


# ------------------------------------------------------------------ #
# Tap                                                                 #
# ------------------------------------------------------------------ #


@router.post("/{name}/tap", response_model=TapResponse)
def tap(
    name: str,
    body: TapRequest,
):
    """Query a coppermind (and optionally its linked minds)."""
    mind = _get_or_404(name)
    llm = get_tap_llm(mind)
    minds = mind.expand_with_links() if body.with_links else [mind]
    workflow = TapWorkflow(minds, llm)
    result = workflow.run(body.question, save_to_outputs=body.save)

    return TapResponse(
        question=result.question,
        answer=result.answer,
        minds_used=result.minds_used,
        connections=result.connections,
        tokens_used=result.tokens_used,
        saved_to=[str(p) for p in result.saved_to],
        cost_usd=result.cost_usd,
    )


@router.post("/{name}/tap/stream")
def tap_stream(
    name: str,
    body: TapRequest,
):
    """Stream a tap response token by token via Server-Sent Events."""
    from copper.llm.base import Message
    from copper.retrieval import build_default_retriever
    from copper.workflows.tap import _build_context, _build_tap_prompt

    mind = _get_or_404(name)
    llm = get_tap_llm(mind)
    minds = mind.expand_with_links() if body.with_links else [mind]

    # Phase 1: delegate to the retrieval pipeline
    retrieval = build_default_retriever(llm).retrieve(body.question, minds)

    context = _build_context(minds, retrieval.selected)
    prompt = _build_tap_prompt(context, body.question, multi=len(minds) > 1)
    messages = [
        Message(role="system", content=_tap_system()),
        Message(role="user", content=prompt),
    ]

    def event_stream():
        for chunk in llm.stream(messages):
            # SSE format: "data: <text>\n\n"
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ------------------------------------------------------------------ #
# Polish                                                              #
# ------------------------------------------------------------------ #


@router.post("/{name}/polish", response_model=PolishResponse)
def polish(
    name: str,
):
    """Run a health check on a coppermind's wiki."""
    mind = _get_or_404(name)
    llm = get_store_llm(mind)
    workflow = PolishWorkflow(mind, llm)
    result = workflow.run()

    return PolishResponse(
        mind_name=result.mind_name,
        report=result.report_text,
        structural_issues=result.structural_issues,
        tokens_used=result.tokens_used,
        cost_usd=result.cost_usd,
    )


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _tap_system() -> str:
    from copper.workflows.tap import TAP_SYSTEM

    return TAP_SYSTEM
