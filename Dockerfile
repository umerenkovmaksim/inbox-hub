FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    API__HOST=0.0.0.0 \
    API__PORT=8000 \
    DATA_DIR=/app/data \
    DATABASE__URL=sqlite+aiosqlite:////app/data/inbox.db \
    TELEGRAM__SESSION_PATH=/app/data/telegram

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN uv sync --frozen --no-dev

VOLUME /app/data
EXPOSE 8000

CMD ["sh", "-c", "inbox-hub init-db && exec inbox-hub serve"]
