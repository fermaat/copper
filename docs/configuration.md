# Configuration

Copper reads its settings from environment variables and per-coppermind config files. This page is the authoritative reference for every option, including defaults and resolution rules.

For a quick start, copy `.env.example` to `.env` and adjust:

```bash
cp .env.example .env
```

By default `COPPER_LLM_PROVIDER=mock` — Copper runs end-to-end with no real LLM calls (deterministic stubs). Switch to `ollama`, `anthropic`, or `openai` once you're ready to write to a real wiki.

---

## Resolution order

For each LLM call Copper picks the **provider** and **model** independently, in this order:

1. **Per-coppermind override** — `<mind>/.copper/config.yaml`
2. **Workflow-specific env var** — e.g. `COPPER_STORE_PROVIDER` for store + polish, `COPPER_TAP_PROVIDER` for tap + chat, `COPPER_INGEST_PROVIDER` for image description
3. **Generic fallback** — `COPPER_LLM_PROVIDER` / `COPPER_LLM_MODEL`
4. **Provider default** — used when `*_MODEL` is left empty

Other settings (paths, ports, timeouts, tuning) are read directly from env with no per-mind override.

---

## LLM — generic fallback

Used by every workflow that does not have a more specific override.

| Variable | Default | Description |
|---|---|---|
| `COPPER_LLM_PROVIDER` | `mock` | One of `mock`, `ollama`, `anthropic`, `openai` |
| `COPPER_LLM_MODEL` | _(empty)_ | Model name passed to the provider; empty = provider default |

## LLM — per-workflow overrides

Override the generic fallback for specific stages of the pipeline.

| Variable | Default | Description |
|---|---|---|
| `COPPER_STORE_PROVIDER` | _(empty)_ | Provider for **store + polish**. Typically a more capable model. |
| `COPPER_STORE_MODEL` | _(empty)_ | Model name for store + polish |
| `COPPER_TAP_PROVIDER` | _(empty)_ | Provider for **tap + chat**. Can be cheaper/local. |
| `COPPER_TAP_MODEL` | _(empty)_ | Model name for tap + chat |
| `COPPER_INGEST_PROVIDER` | _(empty)_ | Provider for **multimodal ingest** (PDF image description). Empty disables image description entirely. |
| `COPPER_INGEST_MODEL` | _(empty)_ | Vision-capable model — e.g. `gemma3:12b`, `llava`, `claude-sonnet-4-6` |

## Provider settings

### Ollama

| Variable | Default | Description |
|---|---|---|
| `COPPER_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL. Use `http://host.docker.internal:11434` from inside Docker. |
| `COPPER_OLLAMA_TIMEOUT` | `300` | Request timeout in seconds |

### Anthropic

| Variable | Default | Description |
|---|---|---|
| `COPPER_ANTHROPIC_API_KEY` | _(empty)_ | API key — required when provider is `anthropic` |
| `COPPER_ANTHROPIC_TIMEOUT` | `300` | Request timeout in seconds |

### OpenAI

| Variable | Default | Description |
|---|---|---|
| `COPPER_OPENAI_API_KEY` | _(empty)_ | API key — required when provider is `openai` |
| `COPPER_OPENAI_BASE_URL` | _(empty)_ | Override the OpenAI endpoint (e.g. for Azure or local OpenAI-compatible servers); empty = default |
| `COPPER_OPENAI_TIMEOUT` | `300` | Request timeout in seconds |

---

## Storage

| Variable | Default | Description |
|---|---|---|
| `COPPER_MINDS_DIR` | `~/.copper/minds` | Where copperminds live. Override to e.g. `/data/minds` in Docker. |

## Workflow tuning

| Variable | Default | Description |
|---|---|---|
| `COPPER_STORE_MAX_CHUNK_CHARS` | `15000` | Max characters per chunk sent to the LLM during ingest. Larger = fewer chunks, higher per-call token usage. Tune to your model's context window. |
| `COPPER_TAP_MAX_PAGES` | `12` | Phase 1 — max wiki pages the LLM-based retriever can pick from the index in a single call |
| `COPPER_TAP_MAX_PAGES_TOTAL` | `20` | Hard ceiling on total pages sent to Phase 2 (after keyword augmentation) |
| `COPPER_TAP_PERSONALITY` | `tap.archivist` | Default tap personality (prompt name) when none is set per-mind or per-request. See `copper personalities` for the full list. |

## Custom prompts

| Variable | Default | Description |
|---|---|---|
| `COPPER_USER_PROMPTS_DIR` | _(empty)_ | Folder with extra `*.yaml` prompt files. They override built-ins by matching `name`. Lets you add custom personalities without forking the codebase. |

YAML prompt schema:

```yaml
name: tap.mygamemaster
description: Custom narrative voice for my homebrew campaign.
template: |
  You are the GM of a brutal, low-magic dark fantasy world ...
```

Names registered by built-ins (see `copper personalities`):

- **Tap** (system + personalities): `tap.archivist`, `tap.gamemaster`, `tap.scholar`, `tap.inquisitor`
- **Tap user template**: `tap.user`
- **Store**: `store.archivist`, `store.user`, `store.images`
- **Polish**: `polish.archivist`, `polish.user`
- **Assay (retrieval)**: `assay.librarian`, `assay.user`
- **Other**: `image.visual` (vision describer), `pdf.section` (TOC fallback)

## PDF ingestion tuning

Heuristic filters applied before sending images to the multimodal model.

| Variable | Default | Description |
|---|---|---|
| `COPPER_PDF_MIN_IMAGE_WIDTH` | `120` | Min image width (pts) — smaller images are skipped as decorative |
| `COPPER_PDF_MIN_IMAGE_HEIGHT` | `120` | Min image height (pts) |
| `COPPER_PDF_MIN_IMAGE_AREA` | `20000` | Min image area in pts² (≈141×141 pts, ~2 in per side) |
| `COPPER_PDF_PAGE_SPAN_THRESHOLD` | `0.85` | Drop images whose clamped bounding box covers ≥ this fraction of the page (background art) — but only when the page has substantial extracted text. |
| `COPPER_PDF_PAGE_SPAN_SKIP_MIN_TEXT` | `200` | Minimum chars of text on the page before the page-span filter kicks in. If the page has less text than this, the "image is the content" and is kept. |
| `COPPER_INGEST_SAVE_IMAGES` | `true` | Persist described images to `<mind>/raw/images/` so the UI can render them next to `[Visual on page N, image M: ...]` markers. Set to `false` to save disk space (descriptions still flow into the wiki, only the PNG files are dropped). |

## API server

| Variable | Default | Description |
|---|---|---|
| `COPPER_HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` in Docker. |
| `COPPER_PORT` | `8000` | Port |
| `COPPER_RELOAD` | `false` | Hot-reload on code changes — for development only |

## Logging & environment

These come from `core-utils` and apply to all logs.

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logger level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FOLDER` | `logs` | Directory for rotating log files |
| `LOG_CONSOLE` | `true` | Mirror logs to stdout |
| `ENVIRONMENT` | `development` | Free-form tag stamped on log lines |

---

## Per-coppermind overrides (`.copper/config.yaml`)

Each coppermind has a `<mind>/.copper/config.yaml` that can override provider/model and personality for that mind only:

```yaml
# ~/.copper/minds/my-mind/.copper/config.yaml
name: my-mind
topic: AI safety research
linked_minds: []
store_provider: anthropic
store_model: claude-opus-4-7
tap_provider: ollama
tap_model: gemma3:4b
ingest_provider: ollama
ingest_model: gemma3:12b
tap_personality: tap.scholar
```

Only set the fields you want to override — absent fields inherit from the env. The file is auto-generated when you forge a coppermind; you can edit it freely afterwards.

The `schema.md` next to it is the Archivist's per-mind instruction set (taxonomies, fields to track, page conventions). Both files are user-editable.

---

## Recipes

### All-local with Ollama

```bash
COPPER_LLM_PROVIDER=ollama
COPPER_LLM_MODEL=gemma3:12b
COPPER_OLLAMA_BASE_URL=http://localhost:11434
```

### Hybrid: Claude for ingest, local Ollama for queries

```bash
# Heavy lifting on Anthropic — better page extraction and consolidation
COPPER_STORE_PROVIDER=anthropic
COPPER_STORE_MODEL=claude-opus-4-7
COPPER_ANTHROPIC_API_KEY=sk-ant-...

# Cheap, fast queries against the compiled wiki
COPPER_TAP_PROVIDER=ollama
COPPER_TAP_MODEL=llama3.2
COPPER_OLLAMA_BASE_URL=http://localhost:11434

# Multimodal PDF ingest needs a vision model
COPPER_INGEST_PROVIDER=ollama
COPPER_INGEST_MODEL=gemma3:12b
```

### CI / tests / first install

```bash
COPPER_LLM_PROVIDER=mock
```

No external services required; the mock LLM produces deterministic placeholder pages.

### Docker (Ollama on host)

```bash
COPPER_HOST=0.0.0.0
COPPER_MINDS_DIR=/data/minds
COPPER_OLLAMA_BASE_URL=http://host.docker.internal:11434
```
