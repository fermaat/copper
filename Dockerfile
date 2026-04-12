# Copper API — multi-stage build
# Ollama runs as an EXTERNAL service; configure OLLAMA_BASE_URL in .env

FROM python:3.12-slim AS base
WORKDIR /app

# Install pdm
RUN pip install --no-cache-dir pdm

# ------------------------------------------------------------------ #
# Dependencies stage                                                  #
# ------------------------------------------------------------------ #
FROM base AS deps

COPY pyproject.toml pdm.lock* ./

# Install runtime + api extras (no dev, no llm — bridge installed separately if needed)
RUN pdm install --no-self --no-editable -G api 2>/dev/null || \
    pdm install --no-self --no-editable

# ------------------------------------------------------------------ #
# Application stage                                                   #
# ------------------------------------------------------------------ #
FROM base AS app

COPY --from=deps /app/.venv /app/.venv
COPY src/ ./src/
COPY pyproject.toml ./

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Minds directory — mount a volume here to persist copperminds
ENV COPPER_MINDS_DIR=/data/minds
RUN mkdir -p /data/minds

EXPOSE 8000

CMD ["python", "-m", "copper.server"]
