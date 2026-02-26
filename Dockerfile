FROM python:3.12-slim AS builder

# uv 설치
RUN pip install uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 의존성 파일만 먼저 복사하여 레이어 캐시 활용
COPY pyproject.toml uv.lock ./

# 프로덕션 의존성 설치 (개발 의존성 제외)
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim

WORKDIR /app

# 빌드 스테이지에서 가상환경 복사
COPY --from=builder /app/.venv .venv/

# 애플리케이션 코드 복사
COPY . .

CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
