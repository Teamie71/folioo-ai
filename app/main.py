"""FastAPI 애플리케이션 설정"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router
from common.checkpointer.factory import setup_checkpointer
from common.logging import setup_logging

# ===== 로깅 초기화 (uvicorn보다 먼저 설정) =====
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    애플리케이션 생명주기 관리

    - 시작 시: 리소스 초기화 (Checkpointer, InsightStore)
    - 종료 시: 리소스 정리
    """

    import logging
    import os

    import asyncpg

    from features.interview.agents.insight_store.pgvector_store import PgVectorInsightStore
    from features.interview.agents.insight_store.seed_data import (
        SEED_INSIGHTS,
        SEED_USER_ID,
    )
    from features.interview.agents.nodes.retriever import init_insight_store

    logger = logging.getLogger(__name__)

    # ===== InsightStore 초기화 (임시 pgvector) =====
    pool = None
    db_url = os.getenv("INSIGHT_DB_URL")

    if db_url:
        try:
            # PostgreSQL 커넥션 풀 생성
            pool = await asyncpg.create_pool(db_url)

            # PgVectorInsightStore 초기화 + 테이블 생성
            insight_store = PgVectorInsightStore(pool=pool)
            await insight_store.setup_table()

            # 시드 데이터 로드 (테스트용, 이미 있으면 UPSERT)
            for insight in SEED_INSIGHTS:
                await insight_store.add_insight(insight, user_id=SEED_USER_ID)

            # 글로벌 싱글톤 등록
            init_insight_store(insight_store)
            logger.info("InsightStore(pgvector) 초기화 완료")
        except Exception:
            logger.exception("InsightStore 초기화 실패 — 인사이트 검색 비활성화")
            if pool is not None:
                await pool.close()
            pool = None
    else:
        logger.warning(
            "INSIGHT_DB_URL이 설정되지 않음 — InsightStore 비활성화 "
            "(Retriever 노드는 빈 인사이트를 반환합니다)"
        )

    # ===== Checkpointer 초기화 =====
    async with setup_checkpointer():
        yield

    # ===== 종료 시: 커넥션 풀 정리 =====
    if pool:
        await pool.close()
        logger.info("InsightStore 커넥션 풀 정리 완료")


def create_app() -> FastAPI:
    """FastAPI 애플리케이션 생성"""

    app = FastAPI(
        title="Folioo AI",
        description="포트폴리오 정리를 도와주는 AI 인터뷰 에이전트 API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS 설정
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: 프로덕션에서는 특정 도메인만 허용
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 라우터 등록
    app.include_router(api_router)

    return app


# 애플리케이션 인스턴스
app = create_app()
