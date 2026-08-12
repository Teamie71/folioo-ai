"""FastAPI 애플리케이션 설정"""

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api import router as api_router
from app.middleware.auth import DOCS_EXEMPT_PATHS, PUBLIC_EXEMPT_PATHS, ApiKeyAuthMiddleware
from app.middleware.experience_map_ticket import ExperienceMapTicketMiddleware
from common.checkpointer.factory import get_checkpointer, setup_checkpointer
from common.db.connection import get_pool_status
from common.logging import setup_logging

# ===== 로깅 초기화 (uvicorn보다 먼저 설정) =====
setup_logging()

APP_VERSION = "0.1.0"
OPENAPI_API_KEY_SCHEME_NAME = "ApiKeyAuth"
OPENAPI_HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}


def _attach_api_key_security(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAPI 스키마에 `X-API-Key` 보안 스키마를 반영한다."""
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    security_schemes[OPENAPI_API_KEY_SCHEME_NAME] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }

    for path, path_item in schema.get("paths", {}).items():
        if path in PUBLIC_EXEMPT_PATHS or path in DOCS_EXEMPT_PATHS:
            continue

        for method, operation in path_item.items():
            if method not in OPENAPI_HTTP_METHODS:
                continue
            operation["security"] = [{OPENAPI_API_KEY_SCHEME_NAME: []}]

    return schema


def _load_allowed_origins() -> list[str]:
    """환경변수 기반 CORS 허용 오리진 목록 반환"""
    default_origin = "http://localhost:3000"
    raw_origins = os.getenv("ALLOWED_ORIGINS", default_origin)
    parsed_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
    return parsed_origins or [default_origin]


def _get_checkpointer_status() -> str:
    """Checkpointer 연결 상태 문자열 반환"""
    try:
        get_checkpointer()
        return "connected"
    except RuntimeError:
        return "disconnected"


def _get_experience_map_db_status() -> str:
    """경험 맵 DB 커넥션 풀 상태 문자열 반환"""
    return get_pool_status()


def _get_ticket_secret() -> str:
    """경험정리 티켓 HS256 서명 키 반환

    `AI_SERVICE_API_KEY`와 별도 키다. 두 키의 회전 주기가 다르고, API 키가 유출되면
    티켓 위조까지 가능해진다.
    """
    return os.getenv("EXPMAP_TICKET_SECRET", "")


def _get_api_key_status() -> str:
    """서비스 간 API Key 설정 상태 문자열 반환"""
    return "configured" if os.getenv("AI_SERVICE_API_KEY", "") else "missing"


def _get_main_server_status() -> str:
    """메인 서버 클라이언트 연결 상태 문자열 반환"""
    try:
        from common.http_client import get_http_client

        get_http_client()
        return "connected"
    except RuntimeError:
        return "disconnected"


def get_health() -> dict[str, str]:
    """헬스체크 응답 생성"""
    api_key_status = _get_api_key_status()
    status = "ok" if api_key_status == "configured" else "unhealthy"

    return {
        "status": status,
        "version": APP_VERSION,
        "checkpointer": _get_checkpointer_status(),
        "experience_map_db": _get_experience_map_db_status(),
        "main_server": _get_main_server_status(),
        "api_key": api_key_status,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    애플리케이션 생명주기 관리

    - 시작 시: 리소스 초기화 (경험 맵 DB 풀, Checkpointer, InsightStore, httpx 클라이언트)
    - 종료 시: 리소스 정리
    """

    import logging

    from common.clients.correction_client import (
        init_correction_client,
        reset_correction_client,
    )
    from common.clients.portfolio_client import (
        init_portfolio_client,
        reset_portfolio_client,
    )
    from common.db.connection import close_pool, create_pool
    from common.http_client import close_http_client, get_http_client
    from features.experience_map.repository import (
        init_repository as init_experience_map_repository,
    )
    from features.experience_map.repository import set_repository as set_experience_map_repository
    from features.interview.agents.insight_store import MainServerInsightStore
    from features.interview.agents.nodes.retriever import init_insight_store

    logger = logging.getLogger(__name__)

    # ===== 경험 맵 DB 커넥션 풀과 Repository 초기화 =====
    # checkpoint DB와 분리된 풀이다. 미설정이어도 기동은 막지 않고 /health로 드러낸다.
    try:
        pool = await create_pool()
        init_experience_map_repository(pool)
        logger.info("경험 맵 DB 커넥션 풀 초기화 완료")
    except ValueError:
        logger.warning("DATABASE_URL이 설정되지 않음 - 경험 맵 DB 비활성화")
    except Exception:
        logger.exception("경험 맵 DB 커넥션 풀 초기화 실패")

    # ===== InsightStore 초기화 (메인 서버 API) =====
    init_insight_store(MainServerInsightStore())
    logger.info("InsightStore(MainServer) 초기화 완료")

    try:
        get_http_client()
        logger.info("메인 서버 HTTP 클라이언트 초기화 완료")
    except RuntimeError:
        logger.warning("메인 서버 HTTP 클라이언트 초기화 건너뜀 (환경변수 미설정)")
    except Exception:
        logger.exception("메인 서버 HTTP 클라이언트 초기화 실패")

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
    try:
        async with setup_checkpointer():
            yield
    finally:
        # ===== 종료 시: 리소스 정리 (예외 발생 시에도 실행) =====
        try:
            await close_http_client()
            logger.info("HTTP 클라이언트 정리 완료")
        except Exception:
            logger.exception("HTTP 클라이언트 정리 실패")

        if correction_client:
            try:
                await correction_client.close()
            except Exception:
                logger.exception("첨삭 클라이언트 정리 실패")
        if portfolio_client:
            try:
                await portfolio_client.close()
            except Exception:
                logger.exception("포트폴리오 클라이언트 정리 실패")

        try:
            # 풀이 닫힌 뒤 Repository 가 남아 있으면 죽은 커넥션을 쓰게 된다.
            set_experience_map_repository(None)
            await close_pool()
        except Exception:
            logger.exception("경험 맵 DB 커넥션 풀 정리 실패")


def create_app() -> FastAPI:
    """FastAPI 애플리케이션 생성"""

    app = FastAPI(
        title="Folioo AI",
        description="포트폴리오 정리를 도와주는 AI 인터뷰 에이전트 API",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(ApiKeyAuthMiddleware)
    # 경험정리 프론트 직결 경로는 티켓으로 인증한다. ApiKeyAuthMiddleware보다 바깥에 두어
    # 티켓 검증 실패가 X-API-Key 검사보다 먼저 응답되게 한다.
    app.add_middleware(ExperienceMapTicketMiddleware, secret_provider=_get_ticket_secret)
    # CORS 미들웨어를 나중에 등록해 preflight(OPTIONS)를 우선 처리한다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_load_allowed_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(
        "/health",
        response_model=dict[str, str],
        status_code=200,
        summary="헬스체크",
        description="서버 상태, 버전, checkpointer/메인 서버 연결 상태를 반환합니다.",
    )
    def health() -> dict[str, str]:
        return get_health()

    # 라우터 등록
    app.include_router(api_router)

    def custom_openapi() -> dict[str, Any]:
        """Swagger UI에서 `X-API-Key` 입력을 위한 OpenAPI 스키마 생성."""
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        app.openapi_schema = _attach_api_key_security(schema)
        return app.openapi_schema

    app.openapi = custom_openapi

    return app


# 애플리케이션 인스턴스
app = create_app()
