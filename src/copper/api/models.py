"""Pydantic request/response models for the Copper API."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ------------------------------------------------------------------ #
# Requests                                                            #
# ------------------------------------------------------------------ #


class ForgeRequest(BaseModel):
    name: str = Field(..., description="Coppermind name (slug)")
    topic: str = Field(..., description="Knowledge domain")
    model: str = Field("default", description="LLM model identifier")


class TapRequest(BaseModel):
    question: str = Field(..., description="Question to answer from the wiki")
    save: bool = Field(False, description="Persist answer to outputs/")
    with_links: bool = Field(False, description="Include linked copperminds")
    personality: str | None = Field(
        None,
        description=(
            "Optional personality (prompt name) for this tap. Falls back to the "
            "mind's configured personality, then global settings."
        ),
    )


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    question: str = Field(..., description="Current user question")
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Prior turns as alternating user/assistant messages (without wiki context)",
    )
    with_links: bool = Field(False, description="Include linked copperminds")
    personality: str | None = Field(None, description="Personality override")


class LinkRequest(BaseModel):
    name_a: str
    name_b: str


class WikiPageUpdateRequest(BaseModel):
    body: str = Field(..., description="New markdown body (frontmatter is managed by the server)")


# ------------------------------------------------------------------ #
# Responses                                                           #
# ------------------------------------------------------------------ #


class MindSummary(BaseModel):
    name: str
    topic: str
    raw_sources: int
    wiki_pages: int
    linked_minds: list[str]
    created: str


class StoreResponse(BaseModel):
    source: str
    pages_written: list[str]
    tokens_used: int
    cost_usd: float = 0.0


class TapResponse(BaseModel):
    question: str
    answer: str
    minds_used: list[str]
    connections: list[str]
    tokens_used: int
    saved_to: list[str]
    cost_usd: float = 0.0


class ChatResponse(BaseModel):
    question: str
    answer: str
    minds_used: list[str]
    connections: list[str]
    tokens_used: int
    cost_usd: float = 0.0


class PolishResponse(BaseModel):
    mind_name: str
    report: str
    structural_issues: list[str]
    tokens_used: int
    cost_usd: float = 0.0


class GraphNode(BaseModel):
    name: str
    topic: str
    links: list[str]


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edge_count: int
