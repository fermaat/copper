#!/usr/bin/env python
"""Profile real ingestion of one or more sources into a coppermind.

Times each `store` end-to-end against the *real* configured LLM, captures pages
written / tokens / cost, and writes a human-readable markdown report. Use it to
calibrate on a couple of small files before committing to a multi-hour run on
the big ones.

Usage:
    python scripts/profile_ingest.py <mind> <file_or_dir> [<file> ...] \
        [--flat] [--no-route] [--topic "..."] [--report path.md]

Examples:
    # Calibrate on the two small books first
    python scripts/profile_ingest.py stormlight \
        "data/mentecobres_cosmere/SL008 Advanced Adversaries digital.pdf" \
        "data/mentecobres_cosmere/SL005 Stormlight Scenarios digital.pdf" --flat

    # Then the rest of a folder
    python scripts/profile_ingest.py stormlight data/mentecobres_cosmere --flat
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# Make the package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from copper.api.deps import get_ingest_describer, get_store_llm  # noqa: E402
from copper.core.coppermind import CopperMind  # noqa: E402
from copper.workflows.store import StoreWorkflow  # noqa: E402

SUPPORTED = {".pdf", ".md", ".txt", ".markdown"}


def collect_sources(paths: list[str]) -> list[Path]:
    """Expand dirs into supported files; keep explicit files as-is. Sorted by size."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(f for f in p.iterdir() if f.suffix.lower() in SUPPORTED)
        elif p.exists():
            out.append(p)
        else:
            print(f"⚠  skip (not found): {p}")
    # Smallest first → fail fast and calibrate cheaply.
    return sorted(set(out), key=lambda f: f.stat().st_size)


def human_secs(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s" if m else f"{sec}s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mind", help="Coppermind name")
    ap.add_argument("sources", nargs="+", help="Files and/or directories to ingest")
    ap.add_argument("--flat", action="store_true", help="Disable PDF structure detection")
    ap.add_argument("--no-route", action="store_true", help="Disable child routing")
    ap.add_argument(
        "--no-polish",
        action="store_true",
        help="Skip per-file auto-polish (bulk mode; run a single polish afterwards)",
    )
    ap.add_argument("--topic", default="", help="Forge the mind with this topic if missing")
    ap.add_argument("--report", default="", help="Markdown report path (default: auto)")
    args = ap.parse_args()

    try:
        mind = CopperMind.get(args.mind)
    except FileNotFoundError:
        if not args.topic:
            print(f"✗ Mind '{args.mind}' does not exist. Pass --topic to forge it.")
            return 1
        mind = CopperMind.forge(args.mind, topic=args.topic)
        print(f"⚒  Forged mind '{args.mind}'")

    llm = get_store_llm(mind)
    # Wire the image describer exactly like the CLI does, so profiled timings
    # include multimodal image description (None when no vision model resolves).
    describer = get_ingest_describer(mind)
    sources = collect_sources(args.sources)
    if not sources:
        print("✗ No sources to ingest.")
        return 1

    no_route = args.no_route or args.flat
    rows: list[dict] = []
    print(f"\nProfiling {len(sources)} source(s) into '{args.mind}' (LLM: {llm})")
    print(f"Image describer: {'ON — ' + str(describer.model) if describer else 'OFF'}\n")
    print(f"{'file':40} {'MB':>6} {'pages':>6} {'tokens':>9} {'wall':>10} {'s/page':>7}")
    print("-" * 86)

    grand_start = time.time()
    for src in sources:
        mb = src.stat().st_size / 1_048_576
        t0 = time.time()
        try:
            result = StoreWorkflow(mind, llm, describer).run(
                src, no_route=no_route, into=None, auto_polish=not args.no_polish
            )
            elapsed = time.time() - t0
            n_pages = len(result.pages_written)
            tokens = result.tokens_used
            cost = result.cost_usd
            err = ""
        except Exception as e:  # noqa: BLE001 — profiling must survive a bad file
            elapsed = time.time() - t0
            n_pages = tokens = 0
            cost = 0.0
            err = type(e).__name__
            print(f"⚠  {src.name[:38]:40} FAILED after {human_secs(elapsed)}: {e}")
        s_per_page = elapsed / n_pages if n_pages else 0.0
        rows.append(
            {
                "file": src.name,
                "mb": mb,
                "pages": n_pages,
                "tokens": tokens,
                "cost": cost,
                "secs": elapsed,
                "error": err,
            }
        )
        if not err:
            print(
                f"{src.name[:38]:40} {mb:>6.1f} {n_pages:>6} {tokens:>9,} "
                f"{human_secs(elapsed):>10} {s_per_page:>7.1f}"
            )

    total_secs = time.time() - grand_start
    total_pages = sum(r["pages"] for r in rows)
    total_tokens = sum(r["tokens"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    avg_s_page = total_secs / total_pages if total_pages else 0.0

    print("-" * 86)
    print(
        f"{'TOTAL':40} {sum(r['mb'] for r in rows):>6.1f} {total_pages:>6} "
        f"{total_tokens:>9,} {human_secs(total_secs):>10} {avg_s_page:>7.1f}"
    )
    if total_cost:
        print(f"\nEstimated cost: ${total_cost:.4f}")

    # Write markdown report.
    report = (
        Path(args.report)
        if args.report
        else Path(f"ingest_profile_{args.mind}_{datetime.now():%Y%m%d_%H%M%S}.md")
    )
    lines = [
        f"# Ingestion profile — {args.mind}",
        "",
        f"- Date: {datetime.now():%Y-%m-%d %H:%M}",
        f"- LLM: `{llm}`",
        f"- Flags: flat={args.flat}, no_route={no_route}",
        f"- Total wall time: **{human_secs(total_secs)}** · pages: {total_pages} · "
        f"tokens: {total_tokens:,}" + (f" · cost: ${total_cost:.4f}" if total_cost else ""),
        f"- Avg **{avg_s_page:.1f} s/page** (use this to extrapolate the big books)",
        "",
        "| File | MB | Pages | Tokens | Wall | s/page | Error |",
        "|---|--:|--:|--:|--:|--:|---|",
    ]
    for r in rows:
        sp = r["secs"] / r["pages"] if r["pages"] else 0.0
        lines.append(
            f"| {r['file']} | {r['mb']:.1f} | {r['pages']} | {r['tokens']:,} | "
            f"{human_secs(r['secs'])} | {sp:.1f} | {r['error']} |"
        )
    report.write_text("\n".join(lines) + "\n")
    print(f"\n📄 Report written to: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
