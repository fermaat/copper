"""
Deep structural polish — reorganizes a hierarchical coppermind tree.

Three-pass map-reduce:
  1. Map  — per-child LLM call: extract named entities from each child's wiki.
  2. Reduce — single LLM call at parent level: propose move + transversal plan.
  3. Apply  — filesystem moves (no LLM) + per-transversal LLM synthesis call.

After applying, the parent's overview is refreshed (spine regeneration).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from copper.config import logger, settings
from copper.core.coppermind import CopperMind
from copper.llm.base import LLMBase, Message
from copper.prompts import render_prompt

_MAPPER_PROMPT = "polish.deep.mapper"
_REDUCER_PROMPT = "polish.deep.reducer"
_SYNTHESIZER_PROMPT = "polish.deep.synthesizer"
_SPINE_PROMPT = "polish.deep.spine"

_FRAGMENT_WINDOW = 400  # chars of page body used as fragment for synthesis


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class EntityEntry:
    name: str
    kind: str
    child_name: str
    pages: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class MoveAction:
    slug: str
    from_child: str
    to_child: str
    reason: str

    def description(self) -> str:
        return f"Move '{self.slug}': {self.from_child} → {self.to_child} ({self.reason})"


@dataclass
class TransversalAction:
    entity: str
    kind: str
    source_children: list[str]
    reason: str

    def description(self) -> str:
        children_str = ", ".join(self.source_children)
        return f"Synthesize '{self.entity}' ({self.kind}) from [{children_str}] ({self.reason})"


@dataclass
class DeepPolishPlan:
    moves: list[MoveAction] = field(default_factory=list)
    transversals: list[TransversalAction] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.moves and not self.transversals


@dataclass
class DeepPolishResult:
    mind_name: str
    plan: DeepPolishPlan
    moves_applied: list[MoveAction] = field(default_factory=list)
    transversals_applied: list[TransversalAction] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0

    @property
    def pages_moved(self) -> int:
        return len(self.moves_applied)

    @property
    def pages_created(self) -> int:
        return len(self.transversals_applied)


# ── XML parsers ───────────────────────────────────────────────────────────────


def _parse_entities(text: str) -> list[tuple[str, str, list[str]]]:
    """Parse mapper XML → list of (name, kind, [page_slugs])."""
    results = []
    for m in re.finditer(
        r'<entity\s+name="([^"]+)"\s+kind="([^"]+)"\s+pages="([^"]*)"',
        text,
    ):
        name, kind, pages_raw = m.group(1), m.group(2), m.group(3)
        pages = [p.strip() for p in pages_raw.split(",") if p.strip()]
        results.append((name, kind, pages))
    return results


def _parse_plan(text: str) -> DeepPolishPlan:
    """Parse reducer XML → DeepPolishPlan."""
    if re.search(r"<plan\s*/>", text):
        return DeepPolishPlan()

    moves = []
    for m in re.finditer(
        r'<move\s+slug="([^"]+)"\s+from="([^"]+)"\s+to="([^"]+)"\s+reason="([^"]*)"',
        text,
    ):
        moves.append(
            MoveAction(
                slug=m.group(1), from_child=m.group(2), to_child=m.group(3), reason=m.group(4)
            )
        )

    transversals = []
    for m in re.finditer(
        r'<transversal\s+entity="([^"]+)"\s+kind="([^"]+)"\s+children="([^"]+)"\s+reason="([^"]*)"',
        text,
    ):
        children = [c.strip() for c in m.group(3).split(",") if c.strip()]
        transversals.append(
            TransversalAction(
                entity=m.group(1), kind=m.group(2), source_children=children, reason=m.group(4)
            )
        )

    return DeepPolishPlan(moves=moves, transversals=transversals)


# ── Workflow ──────────────────────────────────────────────────────────────────


class DeepPolishWorkflow:
    """
    Reorganizes a hierarchical coppermind tree via map-reduce.

    Parameters
    ----------
    mind : CopperMind
        Root of the tree to reorganize.
    llm : LLMBase
        LLM for mapper, reducer, synthesizer, and spine calls.
    """

    def __init__(self, mind: CopperMind, llm: LLMBase):
        self.mind = mind
        self.llm = llm

    def run(
        self,
        dry_run: bool = True,
        auto_yes: bool = False,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> DeepPolishResult:
        """
        Execute the three-pass deep polish.

        Parameters
        ----------
        dry_run : bool
            If True, build the plan but do not apply any actions.
        auto_yes : bool
            If True, apply all proposed actions without prompting.
        confirm_fn : callable, optional
            ``confirm_fn(description) -> bool``.  When ``auto_yes`` is False and
            this is provided, called per action.  Defaults to ``input()`` prompt.
        """
        children = self.mind.children()
        if not children:
            logger.info(f"[deep_polish] {self.mind.name} has no children — nothing to do")
            return DeepPolishResult(mind_name=self.mind.name, plan=DeepPolishPlan())

        tokens_used = 0
        cost_usd = 0.0

        # Pass 1 — map
        all_entries: list[EntityEntry] = []
        for child in children:
            entries, t, c = self._map_child(child)
            all_entries.extend(entries)
            tokens_used += t
            cost_usd += c

        # Pass 2 — reduce (always called; may propose moves even with no entities)
        plan, t, c = self._reduce(all_entries, children)
        tokens_used += t
        cost_usd += c

        # Apply caps
        plan.moves = plan.moves[: settings.copper_deep_polish_max_moves]
        plan.transversals = plan.transversals[: settings.copper_deep_polish_max_transversal]

        if dry_run or plan.is_empty:
            return DeepPolishResult(
                mind_name=self.mind.name,
                plan=plan,
                tokens_used=tokens_used,
                cost_usd=cost_usd,
            )

        # Pass 3 — apply
        child_map = {c.name: c for c in children}
        moves_applied: list[MoveAction] = []
        transversals_applied: list[TransversalAction] = []

        for action in plan.moves:
            if _should_apply(action.description(), auto_yes, confirm_fn):
                self._apply_move(action, child_map)
                moves_applied.append(action)

        for t_action in plan.transversals:
            if _should_apply(t_action.description(), auto_yes, confirm_fn):
                t, c = self._synthesize(t_action, child_map, all_entries)
                tokens_used += t
                cost_usd += c
                transversals_applied.append(t_action)

        # Spine refresh
        if moves_applied or transversals_applied:
            t, c = self._refresh_spine(children)
            tokens_used += t
            cost_usd += c

        return DeepPolishResult(
            mind_name=self.mind.name,
            plan=plan,
            moves_applied=moves_applied,
            transversals_applied=transversals_applied,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
        )

    # ── Pass 1 ────────────────────────────────────────────────────────────────

    def _map_child(self, child: CopperMind) -> tuple[list[EntityEntry], int, float]:
        pages = child.wiki.all_pages()
        if not pages:
            return [], 0, 0.0

        listing_parts = []
        for p in pages:
            preview = p.body[:_FRAGMENT_WINDOW].replace("\n", " ").strip()
            listing_parts.append(f"[{p.name}] {preview}")
        pages_listing = "\n".join(listing_parts)

        prompt = render_prompt(_MAPPER_PROMPT, mind_name=child.name, pages_listing=pages_listing)
        response = self.llm.complete([Message(role="user", content=prompt)])

        entries = []
        for name, kind, page_slugs in _parse_entities(response.text):
            entries.append(
                EntityEntry(name=name, kind=kind, child_name=child.name, pages=page_slugs)
            )

        return entries, response.tokens_used, response.cost_usd

    # ── Pass 2 ────────────────────────────────────────────────────────────────

    def _reduce(
        self, all_entries: list[EntityEntry], children: list[CopperMind]
    ) -> tuple[DeepPolishPlan, int, float]:
        # Build the entity summary table
        lines = []
        for child in children:
            child_entries = [e for e in all_entries if e.child_name == child.name]
            if not child_entries:
                lines.append(f"[{child.name}] (no recurring entities)")
                continue
            for e in child_entries:
                lines.append(f"[{child.name}] {e.name} ({e.kind}): {', '.join(e.pages)}")
        entity_summary = "\n".join(lines)

        prompt = render_prompt(
            _REDUCER_PROMPT, parent_name=self.mind.name, entity_summary=entity_summary
        )
        response = self.llm.complete([Message(role="user", content=prompt)])
        plan = _parse_plan(response.text)
        return plan, response.tokens_used, response.cost_usd

    # ── Pass 3 helpers ────────────────────────────────────────────────────────

    def _apply_move(self, action: MoveAction, child_map: dict[str, CopperMind]) -> None:
        src_mind = child_map.get(action.from_child)
        dst_mind = child_map.get(action.to_child)
        if src_mind is None or dst_mind is None:
            logger.warning(
                f"[deep_polish] Move skipped — unknown child: {action.from_child} or {action.to_child}"
            )
            return
        try:
            src_mind.wiki.move_page(action.slug, dst_mind.wiki)
            logger.info(
                f"[deep_polish] Moved '{action.slug}': {action.from_child} → {action.to_child}"
            )
        except FileNotFoundError:
            logger.warning(
                f"[deep_polish] Move skipped — page '{action.slug}' not found in {action.from_child}"
            )

    def _synthesize(
        self,
        action: TransversalAction,
        child_map: dict[str, CopperMind],
        all_entries: list[EntityEntry],
    ) -> tuple[int, float]:
        # Build fragments: grep matching pages from each source child
        fragment_parts = []
        relevant_entries = [
            e
            for e in all_entries
            if e.name.lower() == action.entity.lower() and e.child_name in action.source_children
        ]
        for entry in relevant_entries:
            child = child_map.get(entry.child_name)
            if child is None:
                continue
            for slug in entry.pages:
                page = child.wiki.page(slug)
                if page.exists():
                    snippet = page.body[:_FRAGMENT_WINDOW].strip()
                    fragment_parts.append(f"[{entry.child_name}/{slug}]\n{snippet}")

        if not fragment_parts:
            logger.warning(
                f"[deep_polish] Synthesis skipped — no fragments found for '{action.entity}'"
            )
            return 0, 0.0

        fragments = "\n\n---\n\n".join(fragment_parts)
        prompt = render_prompt(
            _SYNTHESIZER_PROMPT,
            entity_name=action.entity,
            parent_name=self.mind.name,
            fragments=fragments,
        )
        response = self.llm.complete([Message(role="user", content=prompt)])
        slug = re.sub(r"[^\w-]", "-", action.entity.lower()).strip("-")
        self.mind.wiki.upsert_page(slug=slug, title=action.entity, body=response.text)
        logger.info(f"[deep_polish] Synthesized transversal page '{slug}' in {self.mind.name}")
        return response.tokens_used, response.cost_usd

    def _refresh_spine(self, children: list[CopperMind]) -> tuple[int, float]:
        lines = []
        for child in children:
            page_count = len(child.wiki.all_pages())
            lines.append(f"[{child.name}] topic={child.config.topic} pages={page_count}")
        children_listing = "\n".join(lines)

        prompt = render_prompt(
            _SPINE_PROMPT, parent_name=self.mind.name, children_listing=children_listing
        )
        response = self.llm.complete([Message(role="user", content=prompt)])
        self.mind.wiki.upsert_page(slug="overview", title="Overview", body=response.text)
        logger.info(f"[deep_polish] Refreshed spine for {self.mind.name}")
        return response.tokens_used, response.cost_usd


# ── Helpers ───────────────────────────────────────────────────────────────────


def _filter_candidates(entries: list[EntityEntry]) -> list[EntityEntry]:
    """Return entities that appear in ≥ min_children children AND ≥ min_pages pages total."""
    from collections import defaultdict

    by_entity: dict[str, list[EntityEntry]] = defaultdict(list)
    for e in entries:
        by_entity[e.name].append(e)

    result = []
    for name, group in by_entity.items():
        child_count = len({e.child_name for e in group})
        page_count = sum(e.page_count for e in group)
        if (
            child_count >= settings.copper_deep_polish_min_children
            and page_count >= settings.copper_deep_polish_min_pages
        ):
            result.extend(group)
    return result


def _should_apply(
    description: str, auto_yes: bool, confirm_fn: Callable[[str], bool] | None
) -> bool:
    if auto_yes:
        return True
    if confirm_fn is not None:
        return confirm_fn(description)
    answer = input(f"Apply: {description}\n[y]es / [n]o / (default n): ").strip().lower()
    return answer == "y"
