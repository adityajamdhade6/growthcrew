# GrowthCrew API, worker and scheduler (one image; the command picks the role).
# Workspaces and the signing secret live in /data; the database is SQLite there by default,
# or Postgres when DATABASE_URL points at one (docker-compose.yml).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY evals ./evals
RUN uv sync --frozen --no-dev
# Chromium for rendering ad images (creative/render.py).
RUN uv run playwright install --with-deps chromium

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    DATABASE_URL="sqlite:////data/growthcrew.db"
# Relative paths (workspaces/, .cache/, .secret) resolve under /data.
WORKDIR /data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "growthcrew.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
