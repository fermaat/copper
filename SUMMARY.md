# copper — Claude reference summary

## Purpose
AI-maintained knowledge bases ("copperminds"). An LLM Archivist reads raw sources and compiles a structured markdown wiki. Users ingest sources and query the compiled wiki — no RAG, no embeddings, just folders and text files. Inspired by Karpathy's wiki system + Cosmere copperminds.

## Architecture

```
src/copper/
├── core/
│   ├── coppermind.py    # CopperMind class — forge/get/link/stats; MINDS_DIR = ~/.copper/minds/
│   └── wiki.py          # WikiManager — page CRUD, frontmatter, index, log
├── ingest/
│   ├── base.py          # IngestPlugin (abstract) + naive_split(); default to_chunks() impl
│   ├── plain.py         # PlainTextPlugin — .md, .txt, any UTF-8 (sniff-based detection)
│   ├── obsidian.py      # ObsidianPlugin — normalises [[wikilinks]] and ![[embeds]]
│   ├── pdf.py           # PDFPlugin — pdfplumber + hybrid chunking (TOC → LLM → naive)
│   ├── image_describer.py # ImageDescriber — sends image bytes to a vision model → description text
│   └── registry.py      # IngestRegistry — ordered dispatch; to_markdown() + to_chunks()
├── llm/
│   ├── base.py          # LLMBase (abstract): complete(messages) → LLMResponse; stream()
│   ├── mock.py          # MockLLM — deterministic fake for tests
│   └── bridge_adapter.py # Wraps core-llm-bridge BridgeEngine → LLMBase
├── prompts/
│   ├── __init__.py      # render_prompt() / list_prompts() — registers YAMLs into bridge's PromptManager
│   │                    #   (bypasses bridge's load_from_yaml() to preserve trailing whitespace)
│   └── *.yaml           # Built-in prompts: tap.archivist/gamemaster/scholar/inquisitor (system + personalities),
│                        #   tap.user, store.archivist, store.user, store.images,
│                        #   polish.archivist, polish.user, assay.librarian, assay.user,
│                        #   image.visual, pdf.section
├── retrieval/
│   ├── base.py          # Retriever Protocol + RetrievalResult dataclass
│   ├── llm.py           # LLMRetriever — asks the LLM to select pages from the wiki index
│   ├── keyword.py       # KeywordRetriever — keyword matching against page slugs/titles (no LLM)
│   ├── alloy.py         # AlloyRetriever — fuses multiple retrievers into one ordered list
│   └── factory.py       # build_default_retriever() — LLMRetriever + KeywordRetriever via AlloyRetriever
├── workflows/
│   ├── store.py         # StoreWorkflow: source → chunks → LLM → XML wiki updates → auto-polish
│   ├── tap.py           # TapWorkflow: question (+ optional history) + wiki context → LLM → TapResult
│   └── polish.py        # PolishWorkflow: structural checks + LLM audit → lint report
├── api/
│   ├── app.py           # FastAPI application factory
│   ├── routes/          # REST routes: minds (CRUD, wiki, links) + workflows (store, tap, polish)
│   ├── deps.py          # get_llm() — wires provider from Settings, passes all config explicitly
│   └── templates/       # Jinja2 + HTMX web UI
├── watch.py             # watch_raw_dir() — watchdog Observer + stability polling
├── cli.py               # typer + rich CLI (forge, store, tap, polish, watch, serve, …)
├── config.py            # Settings(CoreSettings) — single source of truth for all config
└── server.py            # uvicorn entry point
```

## Key classes

**CopperMind** (`core/coppermind.py`)
- `CopperMind.forge(name, topic)` — creates `~/.copper/minds/<name>/` with raw/, wiki/, outputs/, .copper/
- `CopperMind.get(name)` — load existing
- `CopperMind.resolve_many("a,b" | "--all")` — multi-mind resolution
- `mind.link(other)` / `unlink(other)` / `linked_minds()` / `expand_with_links()`
- `.config` → `CopperMindConfig` (name, topic, model, linked_minds); persisted as `.copper/config.yaml`
- `.schema()` → reads `.copper/schema.md` (auto-generated, user-editable)

**WikiManager** (`core/wiki.py`)
- `upsert_page(slug, title, body)`, `page(slug)`, `all_pages()`
- `read_index()`, `update_index(content)`, `append_log(action, description)`
- Pages have YAML frontmatter: title, created, last_updated, source_count, status

**IngestPlugin** (`ingest/base.py`)
- `can_handle(path) → bool`
- `to_markdown(path) → str`
- `to_chunks(path, max_chars, llm=None) → list[str]` — default: naive_split; override for smart splitting
- `PDFPlugin` overrides `to_chunks`: TOC keyword → TOC pattern → LLM section detection → naive
- `ImageDescriber` (`ingest/image_describer.py`) — sends image bytes to a vision provider, returns description text; used by `PDFPlugin` for diagrams. Returns `""` for decorative images.

**Prompts** (`prompts/__init__.py`)
- `render_prompt(name, **variables) → str` — renders a named YAML prompt; raises `ValueError` if unknown
- `list_prompts(prefix=None) → list[str]` — lists registered names, optional prefix filter (e.g. `"tap."`)
- Built-in prompts (system + user templates split per workflow):
  - **Tap personalities** (system): `tap.archivist`, `tap.gamemaster`, `tap.scholar`, `tap.inquisitor`
  - **Tap user**: `tap.user` (shared across all personalities)
  - **Store**: `store.archivist` (system), `store.user` (data + format spec), `store.images` (conditional markers block)
  - **Polish**: `polish.archivist` (system), `polish.user` (audit request)
  - **Assay/retrieval**: `assay.librarian` (system), `assay.user` (slug-selection request)
  - **Other**: `image.visual` (vision describer), `pdf.section` (LLM TOC fallback)
- All prompt instruction text lives in YAML; Python only assembles dynamic data (slugs, markers, schema, etc.)
- User overrides via `COPPER_USER_PROMPTS_DIR` — YAML files in that dir replace built-ins by name

**Retrieval** (`retrieval/`)
- `Retriever` protocol (`base.py`): `retrieve(question, minds) → RetrievalResult`
- `LLMRetriever` — asks LLM to pick pages from the wiki index (Phase-1 assay)
- `KeywordRetriever` — keyword matching against slugs/titles; no LLM cost; augments LLM picks
- `AlloyRetriever` — fuses a list of retrievers, deduplicates, respects `max_pages_total`
- `build_default_retriever(llm)` (`factory.py`) — wires LLM + keyword into an alloy from Settings

**Workflows**
- `StoreWorkflow(mind, llm).run(path)` → `registry.to_chunks()` → per-chunk LLM call (refreshes index between chunks) → `<wiki_updates>` XML → wiki pages → auto-polish if multi-chunk → `StoreResult`
- `TapWorkflow(minds, llm).run(question, history=None)` → retrieves relevant pages → builds context → appends optional prior turns → LLM → `TapResult`. `history` is a list of `Message(role, content)` for multi-turn chat.
- `PolishWorkflow(mind, llm).run()` → structural checks (orphans, stubs, missing backlinks) + LLM audit → `wiki/lint-report-<date>.md`

**Settings** (`config.py`)
- Subclasses `CoreSettings` from `core-utils`; adds `env_file` pointing to project `.env`
- All provider config declared here with `COPPER_` prefix: `copper_llm_provider`, `copper_ollama_base_url`, `copper_anthropic_api_key`, etc.
- `deps.py` passes values explicitly to `create_provider()` — no ambient env reading by dependencies

## CLI

```
copper forge <name> [--topic]
copper store <name> <file> [--all]
copper watch <name>                          # watchdog auto-ingest
copper tap <name|names|--all> <q> [--save] [--with-links]
copper chat <name> [--with-links]
copper polish <name>
copper list / status <name>
copper link/unlink <a> <b> / graph
copper serve [--host] [--port] [--reload]
```

## Coppermind folder layout

```
~/.copper/minds/<name>/
├── raw/           # immutable sources (user adds files here)
├── wiki/          # LLM-maintained wiki (index.md, log.md, lint-report-*.md, *.md)
├── outputs/       # saved tap answers
└── .copper/
    ├── config.yaml
    └── schema.md  # Archivist instructions (auto-generated, user-editable)
```

## Configuration (env vars, all prefixed COPPER_)

Common settings — see [`docs/configuration.md`](docs/configuration.md) for the full reference (PDF tuning, retrieval ceilings, custom prompts, recipes).

| Variable | Default | Notes |
|---|---|---|
| `COPPER_LLM_PROVIDER` | `mock` | `mock` \| `ollama` \| `anthropic` \| `openai` |
| `COPPER_LLM_MODEL` | _(empty)_ | Required for non-mock providers |
| `COPPER_STORE_PROVIDER` / `_MODEL` | _(empty)_ | Override for ingest + polish |
| `COPPER_TAP_PROVIDER` / `_MODEL` | _(empty)_ | Override for tap + chat |
| `COPPER_INGEST_PROVIDER` / `_MODEL` | _(empty)_ | Vision model for multimodal PDF ingest (empty = skip) |
| `COPPER_TAP_PERSONALITY` | `tap.archivist` | Default tap personality (prompt name) |
| `COPPER_USER_PROMPTS_DIR` | _(empty)_ | Folder of YAML prompts that override built-ins by name |
| `COPPER_MINDS_DIR` | `~/.copper/minds` | Override for Docker: `/data/minds` |
| `COPPER_OLLAMA_BASE_URL` | `http://localhost:11434` | Use `host.docker.internal` in Docker |
| `COPPER_ANTHROPIC_API_KEY` | _(empty)_ | |
| `COPPER_OPENAI_API_KEY` | _(empty)_ | |

Resolution order: **per-mind `.copper/config.yaml` → workflow env var → generic fallback → provider default**.

## Dependencies

- Runtime: fastapi, uvicorn, typer, rich, pyyaml, httpx, loguru, pydantic-settings
- `core-utils` @ github.com/fermaat/core-utils — `CoreSettings` base + `configure_logger`
- `core-llm-bridge` @ github.com/fermaat/core-llm-bridge — provider abstraction (Ollama, Anthropic, OpenAI)
- Optional: pdfplumber (`-G pdf`), watchdog (`-G watch`)
- Dev: pytest, black, ruff, mypy

## Phase status

- Phase 1 ✓ — core, workflows (store/tap/polish), CLI, MockLLM, tests
- Phase 2 ✓ — multi-mind linking, cross-mind tap, `--with-links`
- Phase 3 ✓ — FastAPI REST API + HTMX UI + Docker + SSE streaming
- Phase 4 ✓ — PDF/Obsidian/PlainText ingest plugins, watchdog auto-ingest, smart PDF chunking
- Phase 5 ✓ — multi-turn chat mode (stateless history, `/chat` + `/chat/stream` API, toggle UI)
- Phase 6 ✓ — two-stage retrieval (assay: LLM index scan + keyword augment → AlloyRetriever), prompt YAML system, image describer for multimodal PDFs, tap personalities

## Known technical debt

- **Tap context ceiling**: full wiki loaded per query if retrieval falls back — degrades at ~100+ pages. The `LLMRetriever` + `KeywordRetriever` pipeline mitigates this for most queries.
