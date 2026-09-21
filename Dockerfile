FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON=3.12
ENV UV_PYTHON_DOWNLOADS=never
ENV PORT=8080
ENV UVICORN_HOST=0.0.0.0
ENV PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY common/ ./common/
COPY features/correction/ ./features/correction/
COPY features/experience_map/ ./features/experience_map/
COPY features/interview/ ./features/interview/
COPY features/portfolio/ ./features/portfolio/
COPY main.py ./

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
