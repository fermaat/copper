"""Routes for the three core workflows: store, tap, polish."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

from copper.api.deps import get_llm
from copper.api.models import PolishResponse, StoreResponse, TapRequest, TapResponse
from copper.api.routes.minds import _get_or_404
from copper.core.coppermind import CopperMind
from copper.llm.base import LLMBase
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
    llm: LLMBase = Depends(get_llm),
):
    """Upload a file and ingest it into the coppermind."""
    mind = _get_or_404(name)
    workflow = StoreWorkflow(mind, llm)

    # Write upload to a temp file, then run workflow
    suffix = Path(file.filename or "source.md").suffix or ".md"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        result = workflow.run(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return StoreResponse(
        source=file.filename or tmp_path.name,
        pages_written=result.pages_written,
        tokens_used=result.tokens_used,
    )


# ------------------------------------------------------------------ #
# Tap                                                                 #
# ------------------------------------------------------------------ #


@router.post("/{name}/tap", response_model=TapResponse)
def tap(
    name: str,
    body: TapRequest,
    llm: LLMBase = Depends(get_llm),
):
    """Query a coppermind (and optionally its linked minds)."""
    mind = _get_or_404(name)
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
    )


@router.post("/{name}/tap/stream")
def tap_stream(
    name: str,
    body: TapRequest,
    llm: LLMBase = Depends(get_llm),
):
    """Stream a tap response token by token via Server-Sent Events."""
    from copper.workflows.tap import _build_context, _build_tap_prompt
    from copper.llm.base import Message

    mind = _get_or_404(name)
    minds = mind.expand_with_links() if body.with_links else [mind]

    context = _build_context(minds)
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
    llm: LLMBase = Depends(get_llm),
):
    """Run a health check on a coppermind's wiki."""
    mind = _get_or_404(name)
    workflow = PolishWorkflow(mind, llm)
    result = workflow.run()

    return PolishResponse(
        mind_name=result.mind_name,
        report=result.report_text,
        structural_issues=result.structural_issues,
        tokens_used=result.tokens_used,
    )


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _tap_system() -> str:
    from copper.workflows.tap import TAP_SYSTEM
    return TAP_SYSTEM
