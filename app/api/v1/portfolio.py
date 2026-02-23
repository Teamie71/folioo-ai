"""포트폴리오 API 라우터"""

import uuid as _uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.schemas.interview import ErrorResponse
from app.schemas.portfolio import (
    GeneratePortfolioRequest,
    GeneratePortfolioResponse,
    PortfolioResultResponse,
    PortfolioStatusResponse,
    UpdateContributionRateRequest,
)
from features.portfolio.schemas import PortfolioStatus
from features.portfolio.service import get_portfolio_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _validate_portfolio_id(portfolio_id: str) -> None:
    """portfolio_id가 유효한 UUID 형식인지 검증한다."""
    try:
        _uuid.UUID(portfolio_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"포트폴리오를 찾을 수 없습니다: {portfolio_id}",
        ) from e


def _to_portfolio_result_response(result) -> PortfolioResultResponse:
    """서비스 결과를 API 응답 스키마로 변환"""
    return PortfolioResultResponse(
        portfolio_id=result.portfolio_id,
        session_id=result.session_id,
        user_id=result.user_id,
        experience_name=result.experience_name,
        status=result.status,
        contribution_rate=result.contribution_rate,
        detail_info=result.output.detail_info,
        assigned_task=result.output.assigned_task,
        problem_solving=result.output.problem_solving,
        lessons_learned=result.output.lessons_learned,
    )


@router.post(
    "/generate",
    response_model=GeneratePortfolioResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="포트폴리오 생성 시작",
    responses={
        400: {"model": ErrorResponse, "description": "인터뷰가 완료되지 않은 경우"},
        409: {"model": ErrorResponse, "description": "이미 생성 중/완료된 포트폴리오가 있는 경우"},
    },
)
async def generate_portfolio(
    request: GeneratePortfolioRequest,
    background_tasks: BackgroundTasks,
) -> GeneratePortfolioResponse:
    """포트폴리오 생성을 시작합니다."""
    service = get_portfolio_service()

    if await service.has_generating_or_completed(request.session_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 생성 중이거나 완료된 포트폴리오가 존재합니다: {request.session_id}",
        )

    try:
        portfolio_id = await service.start_generation(
            session_id=request.session_id,
            user_id=request.user_id,
            background_tasks=background_tasks,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return GeneratePortfolioResponse(
        portfolio_id=portfolio_id,
        status=PortfolioStatus.GENERATING,
    )


@router.get(
    "/{portfolio_id}/status",
    response_model=PortfolioStatusResponse,
    summary="포트폴리오 생성 상태 조회",
    responses={404: {"model": ErrorResponse, "description": "포트폴리오가 없는 경우"}},
)
async def get_portfolio_status(portfolio_id: str) -> PortfolioStatusResponse:
    """포트폴리오 생성 상태를 조회합니다."""
    _validate_portfolio_id(portfolio_id)
    service = get_portfolio_service()
    try:
        return await service.get_status(portfolio_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.get(
    "/{portfolio_id}",
    response_model=PortfolioResultResponse,
    summary="포트폴리오 결과 조회",
    responses={404: {"model": ErrorResponse, "description": "포트폴리오가 없는 경우"}},
)
async def get_portfolio_result(portfolio_id: str) -> PortfolioResultResponse:
    """포트폴리오 결과를 조회합니다."""
    _validate_portfolio_id(portfolio_id)
    service = get_portfolio_service()
    result = await service.get_result(portfolio_id)
    if result is None or result.output is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"포트폴리오를 찾을 수 없습니다: {portfolio_id}",
        )

    return _to_portfolio_result_response(result)


@router.patch(
    "/{portfolio_id}/contribution-rate",
    status_code=status.HTTP_200_OK,
    summary="포트폴리오 기여도 수정",
    responses={404: {"model": ErrorResponse, "description": "포트폴리오가 없는 경우"}},
)
async def update_contribution_rate(
    portfolio_id: str,
    request: UpdateContributionRateRequest,
) -> dict[str, str]:
    """포트폴리오 기여도를 수정합니다."""
    _validate_portfolio_id(portfolio_id)
    service = get_portfolio_service()
    if not await service.exists(portfolio_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"포트폴리오를 찾을 수 없습니다: {portfolio_id}",
        )

    await service.update_contribution_rate(portfolio_id, request.contribution_rate)
    return {"message": "기여도가 수정되었습니다."}


@router.get(
    "/session/{session_id}",
    response_model=PortfolioResultResponse,
    summary="세션별 포트폴리오 결과 조회",
    responses={404: {"model": ErrorResponse, "description": "포트폴리오가 없는 경우"}},
)
async def get_portfolio_by_session(session_id: str) -> PortfolioResultResponse:
    """세션 ID로 포트폴리오 결과를 조회합니다."""
    service = get_portfolio_service()
    result = await service.get_by_session(session_id)
    if result is None or result.output is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"포트폴리오를 찾을 수 없습니다: {session_id}",
        )

    return _to_portfolio_result_response(result)
