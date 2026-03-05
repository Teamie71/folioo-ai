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

    - 시작 시: 리소스 초기화 (Checkpointer, InsightStore, 포트폴리오/첨삭 DB, httpx 클라이언트)
    - 종료 시: 리소스 정리
    """

    import logging
    import os

    from common.clients.correction_client import (
        init_correction_client,
        reset_correction_client,
    )
    from common.clients.portfolio_client import (
        init_portfolio_client,
        reset_portfolio_client,
    )
    from common.db.connection import close_pool, create_pool
    from features.interview.agents.insight_store.pgvector_store import (
        PgVectorInsightStore,
    )
    from features.interview.agents.insight_store.seed_data import (
        SEED_INSIGHTS,
        SEED_USER_ID,
    )
    from features.interview.agents.nodes.retriever import init_insight_store
    from features.portfolio.repository import PortfolioRepository

    logger = logging.getLogger(__name__)

    # ===== 공유 DB 커넥션 풀 초기화 (InsightStore + Portfolio 공용) =====
    pool = None
    db_url = os.getenv("DATABASE_URL")

    if db_url:
        try:
            pool = await create_pool()
        except Exception:
            logger.exception("DB 커넥션 풀 생성 실패 - InsightStore/포트폴리오 DB 비활성화")
    else:
        logger.warning("DATABASE_URL이 설정되지 않음 - InsightStore/포트폴리오 DB 비활성화")

    # ===== InsightStore 초기화 (임시 pgvector) =====
    if pool is not None:
        try:
            insight_store = PgVectorInsightStore(pool=pool)
            await insight_store.setup_table()

            # 시드 데이터 로드 (테스트용, 이미 있으면 UPSERT)
            for insight in SEED_INSIGHTS:
                await insight_store.add_insight(insight, user_id=SEED_USER_ID)

            init_insight_store(insight_store)
            logger.info("InsightStore(pgvector) 초기화 완료")
        except Exception:
            logger.exception("InsightStore 초기화 실패 - 인사이트 검색 비활성화")

    # ===== 포트폴리오 DB 초기화 =====
    if pool is not None:
        try:
            portfolio_repo = PortfolioRepository(pool)
            await portfolio_repo.setup_table()
            logger.info("포트폴리오 DB 초기화 완료")
        except Exception:
            logger.exception("포트폴리오 DB 초기화 실패")

    # ===== 메인 서버 httpx 클라이언트 초기화 =====
    correction_client = None
    portfolio_client = None
    main_backend_url = os.getenv("MAIN_BACKEND_URL")

    if main_backend_url:
        try:
            correction_client = init_correction_client()
            portfolio_client = init_portfolio_client()
            logger.info("메인 서버 httpx 클라이언트 초기화 완료")
        except Exception:
            logger.exception("메인 서버 httpx 클라이언트 초기화 실패 - 첨삭 서비스 비활성화")
            if correction_client is not None:
                await correction_client.close()
            reset_correction_client()
            reset_portfolio_client()
            correction_client = None
            portfolio_client = None
    else:
        logger.warning("MAIN_BACKEND_URL이 설정되지 않음 - 첨삭 서비스 비활성화")

    # ===== Checkpointer 초기화 =====
    async with setup_checkpointer():
        yield

    # ===== 종료 시: 리소스 정리 =====
    if correction_client:
        await correction_client.close()
    if portfolio_client:
        await portfolio_client.close()

    if pool:
        await close_pool()
        logger.info("DB 커넥션 풀 정리 완료")


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
