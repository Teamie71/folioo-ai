FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON=3.12
ENV UV_PYTHON_DOWNLOADS=automatic
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
ENV PORT=8080
ENV JAVA_TOOL_OPTIONS=-Xmx512m
ENV PPTX_WORKER_RECYCLE_AFTER=20
ENV ANTHROPIC_PPTX_SKILL_DIR=/app/apps/pptx-worker/toolchain
ENV PYTHONPATH=/app:/app/apps/pptx-worker
ENV PATH=/app/.venv/bin:$PATH

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fontconfig \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    libreoffice-core \
    libreoffice-impress \
    poppler-utils \
    python3 \
    python3-pip \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --only-group pptx-worker

COPY common/ ./common/
COPY features/visualization/ ./features/visualization/
COPY templates/ ./templates/
COPY apps/pptx-worker/ ./apps/pptx-worker/

RUN mkdir -p /tmp \
    && chmod 1777 /tmp \
    && bash apps/pptx-worker/scripts/verify_runtime_image.sh

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /opt/uv-python

USER appuser

CMD ["sh", "-c", "exec uvicorn pptx_worker.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
