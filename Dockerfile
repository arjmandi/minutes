# Production image for the minutes backend (FastAPI + ingest pipeline).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Dependency layer (cached unless deps change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application.
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
# One uvicorn worker per pod; scale via replicas (admission cap is shared in Redis).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
