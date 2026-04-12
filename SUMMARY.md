# copper — Claude reference summary

## Purpose
AI-maintained knowledge bases ("mentecobres"). An LLM Archivist reads raw sources and compiles a structured markdown wiki. Users ingest sources and query the compiled wiki — no RAG, no embeddings, just folders and text files. Inspired by Karpathy's wiki system + Cosmere copperminds.

## Architecture

```
src/copper/
├── core/
│   ├── coppermind.py    # CopperMind class — forge/get/link/stats; MINDS_DIR = ~/.copper/minds/
│   └── wiki.py          # WikiManager — page CRUD, frontmatter, index, log
├── llm/
│   ├── base.py          # LLMBase (abstract): complete(messages) → LLMResponse; chat(), stream()
│   ├── mock.py          # MockLLM — deterministic fake for tests
│   └── bridge_adapter.py # Wraps core-llm-bridge BridgeEngine → LLMBase
├── workflows/
│   ├── store.py         # StoreWorkflow: source file → LLM → XML wiki updates
│   ├── tap.py           # TapWorkflow: question + wiki context → LLM → TapResult
│   └── polish.py        # PolishWorkflow: wiki audit → lint report
└── cli.py               # typer + rich CLI
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
- `page(slug)`, `create_page(slug, title, body)`, `update_page(slug, body)`, `upsert_page(...)`
- `read_index()`, `update_index(content)`, `append_log(action, description)`
- Pages have YAML frontmatter: title, created, last_updated, source_count, status

**Workflows**
- `StoreWorkflow(mind, llm).run(path)` → sends source + schema + current index to LLM, parses `<wiki_updates>` XML, writes pages → `StoreResult`
- `TapWorkflow(minds, llm).run(question)` → builds multi-mind context, sends to LLM, extracts `[Conexión: A ↔ B]` markers → `TapResult` (answer, connections, saved_to)
- `PolishWorkflow(mind, llm).run()` → structural checks (no LLM) + LLM audit → lint report in `wiki/lint-report-<date>.md`

**LLM integration**
- Set `COPPER_LLM_PROVIDER` + `COPPER_LLM_MODEL` env vars
- `provider=mock` (default) → MockLLM, no API calls
- `provider=ollama` → needs `pdm install -G llm`, uses `core-llm-bridge` via `BridgeAdapter`
- `BridgeAdapter` wraps `BridgeEngine.chat()` / `chat_stream()`

## CLI

```
copper forge <name> [--topic]        # create coppermind
copper store <name> <file> [--all]   # ingest source(s)
copper tap <name|names|--all> <q> [--save] [--with-links]  # query
copper chat <name> [--with-links]    # interactive REPL
copper polish <name>                 # health check
copper list / status <name>          # inspect
copper link/unlink <a> <b>           # manage connections
copper graph                         # visualise link graph
```

## Coppermind folder layout

```
~/.copper/minds/<name>/
├── raw/           # immutable sources (user adds files here)
├── wiki/          # LLM-maintained wiki (index.md, log.md, *.md pages)
├── outputs/       # saved tap answers
└── .copper/
    ├── config.yaml
    └── schema.md  # Archivist instructions (auto-generated, user-editable)
```

## Dependencies
- Runtime: typer, rich, pyyaml, httpx
- Optional LLM: `core-llm-bridge` @ https://github.com/fermaat/core-llm-bridge
- Dev: pytest, black, ruff, mypy

## Phase status
- Phase 1 ✓ — core, workflows, CLI
- Phase 2 ✓ — multi-mind linking, cross-mind detection, --with-links
- Phase 3 — FastAPI REST API + minimal HTML UI + Docker (Ollama as external service via .env)
- Phase 4 — git export, file watchers, import plugins
