"""첨삭 API 라우터"""

import logging
import uuid as _uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status

from app.schemas.correction import (
    CompanyInsightResponse,
    CorrectionResultResponse,
    CorrectionStatusResponse,
    CreateCorrectionRequest,
    CreateCorrectionResponse,
    UpdateCompanyInsightRequest,
    UpdateEmphasisPointsRequest,
)
from app.schemas.interview import ErrorResponse
from features.correction.schemas import CorrectionStatus
from features.correction.service import get_correction_service

router = APIRouter(prefix="/corrections", tags=["correction"])
logger = logging.getLogger(__name__)


def _validate_correction_id(correction_id: str) -> None:
    """correction_id가 유효한 UUID 형식인지 검증한다."""
    try:
        _uuid.UUID(correction_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
        ) from e


def _raise_internal_server_error() -> None:
    """내부 서버 에러를 표준 메시지로 반환한다."""
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="내부 서버 오류가 발생했습니다.",
    )


def _is_company_insight_or_later(status_value: str) -> bool:
    """company_insight 이후 단계 여부를 반환한다."""
    allowed_statuses = {
        CorrectionStatus.COMPANY_INSIGHT.value,
        CorrectionStatus.GENERATING.value,
        CorrectionStatus.DONE.value,
    }
    return status_value in allowed_statuses


@router.post(
    "",
    response_model=CreateCorrectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="첨삭 세션 생성",
    responses={500: {"model": ErrorResponse, "description": "내부 서버 에러"}},
)
async def create_correction(request: CreateCorrectionRequest) -> CreateCorrectionResponse:
    """첨삭 세션을 생성한다."""
    service = get_correction_service()
    try:
        correction = await service.create_correction(
            portfolio_id=request.portfolio_id,
            user_id=request.user_id,
            company_name=request.company_name,
            job_title=request.job_title,
            job_description=request.job_description,
        )
        return CreateCorrectionResponse(
            correction_id=str(correction["id"]),
            status=CorrectionStatus(correction["status"]),
        )
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.get(
    "/{correction_id}",
    response_model=CorrectionResultResponse,
    summary="첨삭 결과 조회",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def get_correction_result(correction_id: str) -> CorrectionResultResponse:
    """첨삭 결과를 조회한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        return CorrectionResultResponse(
            correction_id=str(correction["id"]),
            status=CorrectionStatus(correction["status"]),
            result=correction.get("result"),
        )
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.get(
    "/{correction_id}/status",
    response_model=CorrectionStatusResponse,
    summary="첨삭 상태 조회",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def get_correction_status(correction_id: str) -> CorrectionStatusResponse:
    """첨삭 상태를 조회한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        status_value = await service.get_status(correction_id)
        return CorrectionStatusResponse(status=status_value, progress_message=None)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.post(
    "/{correction_id}/rag",
    status_code=status.HTTP_202_ACCEPTED,
    summary="RAG 실행 시작",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def start_rag(correction_id: str, background_tasks: BackgroundTasks) -> dict[str, str]:
    """RAG 실행을 시작한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        if correction["status"] != CorrectionStatus.NOT_STARTED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 상태에서는 RAG를 시작할 수 없습니다.",
            )
        await service.start_rag(correction_id, background_tasks)
        return {"message": "RAG 실행을 시작했습니다."}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.get(
    "/{correction_id}/company-insight",
    response_model=CompanyInsightResponse,
    summary="기업 분석 결과 조회",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def get_company_insight(correction_id: str) -> CompanyInsightResponse:
    """기업 분석 결과를 조회한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        if not _is_company_insight_or_later(correction["status"]):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="기업 분석 결과를 조회할 수 있는 상태가 아닙니다.",
            )
        if correction.get("company_insight") is None:
            logger.error(
                "company_insight is None in company-insight response "
                "(correction_id: %s, correction: %s)",
                correction_id,
                correction,
            )
            raise ValueError("company_insight 데이터가 비어 있습니다.")
        return CompanyInsightResponse(company_insight=correction.get("company_insight"))
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.patch(
    "/{correction_id}/company-insight",
    status_code=status.HTTP_200_OK,
    summary="기업 분석 수정",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def update_company_insight(
    correction_id: str,
    request: UpdateCompanyInsightRequest,
) -> dict[str, str]:
    """기업 분석 내용을 수정한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        if correction["status"] != CorrectionStatus.COMPANY_INSIGHT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="기업 분석 수정은 company_insight 상태에서만 가능합니다.",
            )
        await service.update_company_insight(correction_id, request.company_insight)
        return {"message": "기업 분석이 수정되었습니다."}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.patch(
    "/{correction_id}/emphasis-points",
    status_code=status.HTTP_200_OK,
    summary="강조 포인트 수정",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def update_emphasis_points(
    correction_id: str,
    request: UpdateEmphasisPointsRequest,
) -> dict[str, str]:
    """강조 포인트를 수정한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        if correction["status"] != CorrectionStatus.COMPANY_INSIGHT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="강조 포인트 수정은 company_insight 상태에서만 가능합니다.",
            )
        await service.update_emphasis_points(correction_id, request.emphasis_points)
        return {"message": "강조 포인트가 수정되었습니다."}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.post(
    "/{correction_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="첨삭 생성 시작",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def start_generation(
    correction_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """첨삭 생성을 시작한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        if correction["status"] != CorrectionStatus.COMPANY_INSIGHT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 상태에서는 첨삭 생성을 시작할 수 없습니다.",
            )
        await service.start_generation(correction_id, background_tasks)
        return {"message": "첨삭 생성을 시작했습니다."}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.post(
    "/{correction_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    summary="첨삭 재시도 시작",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def retry_correction(
    correction_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """실패한 첨삭 생성을 재시도한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        correction = await service.get_correction(correction_id)
        if correction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
            )
        if correction["status"] != CorrectionStatus.FAILED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 상태에서는 재시도할 수 없습니다.",
            )
        await service.retry(correction_id, background_tasks)
        return {"message": "재시도를 시작했습니다."}
    except ValueError as e:
        if "실패 상태가 아닌 첨삭은 재시도할 수 없습니다" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 상태에서는 재시도할 수 없습니다.",
            ) from e
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.delete(
    "/{correction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="첨삭 삭제",
    responses={
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def delete_correction(correction_id: str) -> Response:
    """첨삭을 삭제한다."""
    _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        await service.delete_correction(correction_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()
