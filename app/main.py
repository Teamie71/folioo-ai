"""FastAPI 애플리케이션 설정"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    애플리케이션 생명주기 관리

    - 시작 시: 리소스 초기화
    - 종료 시: 리소스 정리
    """
    # 시작 시 실행
    yield
    # 종료 시 실행


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
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 라우터 등록
    app.include_router(api_router)

    return app


# 애플리케이션 인스턴스
app = create_app()
