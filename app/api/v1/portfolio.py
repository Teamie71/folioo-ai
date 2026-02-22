"""포트폴리오 API 라우터"""

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

    existing = await service._repository.get_by_session_id(request.session_id)
    if existing and existing["status"] in {
        PortfolioStatus.GENERATING.value,
        PortfolioStatus.COMPLETED.value,
    }:
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
    service = get_portfolio_service()
    result = await service.get_result(portfolio_id)
    if result is None or result.output is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"포트폴리오를 찾을 수 없습니다: {portfolio_id}",
        )

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
    service = get_portfolio_service()
    try:
        await service.get_status(portfolio_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    await service.update_contribution_rate(portfolio_id, request.contribution_rate)
    return {"message": "ok"}


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
