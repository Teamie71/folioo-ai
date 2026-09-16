"""첨삭 API 라우터"""

import json
import logging
from contextlib import suppress

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile, status
from sse_starlette.sse import EventSourceResponse

from app.schemas.correction import (
    CompanyInsightResponse,
    CorrectionResultResponse,
    CorrectionStatusResponse,
    UpdateCompanyInsightRequest,
    UpdateEmphasisPointsRequest,
)
from app.schemas.interview import ErrorResponse
from app.schemas.pdf_extraction import PdfExtractionAcceptedResponse
from common.clients.base_client import MainServerError
from common.clients.correction_client import get_correction_client
from common.sse import interleave_ping_events
from features.correction.service import get_correction_service
from features.portfolio.pdf_extraction.service import get_pdf_extraction_service

router = APIRouter(prefix="/corrections", tags=["correction"])
logger = logging.getLogger(__name__)

_MAX_PDF_FILE_SIZE_BYTES = 10 * 1024 * 1024
_PDF_UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024


def _validate_correction_id(correction_id: str) -> int:
    """correction_id가 유효한 정수인지 검증하고 int로 반환한다."""
    try:
        return int(correction_id)
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"첨삭을 찾을 수 없습니다: {correction_id}",
        ) from e


async def _read_upload_within_limit(file: UploadFile) -> bytes:
    """업로드 파일을 크기 상한 안에서 읽어 바이트로 반환한다."""
    file_bytes_buffer = bytearray()

    while chunk := await file.read(_PDF_UPLOAD_CHUNK_SIZE_BYTES):
        file_bytes_buffer.extend(chunk)
        if len(file_bytes_buffer) > _MAX_PDF_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PDF 파일 크기는 10MB를 초과할 수 없습니다.",
            )

    return bytes(file_bytes_buffer)


def _raise_internal_server_error() -> None:
    """내부 서버 에러를 표준 메시지로 반환한다."""
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="내부 서버 오류가 발생했습니다.",
    )


def _handle_main_server_error(exc: MainServerError) -> None:
    """메인 서버 에러를 적절한 HTTP 에러로 변환한다."""
    if exc.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc
    if exc.status_code == 422 and exc.error_code == "CORRECTION4221":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.detail,
    ) from exc


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
    cid = _validate_correction_id(correction_id)
    try:
        correction = await get_correction_client().get_correction(cid)
        return CorrectionResultResponse(
            correction_id=str(correction["id"]),
            status=correction.get("status", "").lower(),
            result=correction.get("result"),
        )
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    cid = _validate_correction_id(correction_id)
    try:
        correction = await get_correction_client().get_correction(cid)
        return CorrectionStatusResponse(
            status=correction.get("status", "").lower(),
            progress_message=None,
        )
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    """RAG 실행을 시작한다. 메인 서버가 상태 전이를 검증한다."""
    cid = _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        await service.start_rag(cid, background_tasks)
        return {"message": "RAG 실행을 시작했습니다."}
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    cid = _validate_correction_id(correction_id)
    try:
        correction = await get_correction_client().get_correction(cid)
        company_insight = correction.get("companyInsight")
        if company_insight is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="기업 분석 결과를 조회할 수 있는 상태가 아닙니다.",
            )
        if isinstance(company_insight, dict):
            company_insight = json.dumps(company_insight, ensure_ascii=False)
        return CompanyInsightResponse(company_insight=company_insight)
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    cid = _validate_correction_id(correction_id)
    try:
        await get_correction_client().update_company_insight(cid, request.company_insight)
        return {"message": "기업 분석이 수정되었습니다."}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    cid = _validate_correction_id(correction_id)
    try:
        await get_correction_client().update_emphasis_points(cid, request.emphasis_points)
        return {"message": "강조 포인트가 수정되었습니다."}
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    """첨삭 생성을 시작한다. 메인 서버가 상태 전이를 검증한다."""
    cid = _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        await service.start_generation(cid, background_tasks)
        return {"message": "첨삭 생성을 시작했습니다."}
    except MainServerError as exc:
        _handle_main_server_error(exc)
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
    cid = _validate_correction_id(correction_id)
    service = get_correction_service()
    try:
        await service.retry(cid, background_tasks)
        return {"message": "재시도를 시작했습니다."}
    except ValueError as e:
        if "실패 상태가 아닌 첨삭은 재시도할 수 없습니다" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 상태에서는 재시도할 수 없습니다.",
            ) from e
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except MainServerError as exc:
        _handle_main_server_error(exc)
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()


@router.post(
    "/{correction_id}/pdf-extraction",
    response_model=PdfExtractionAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="PDF 추출 시작",
    responses={
        400: {"model": ErrorResponse, "description": "PDF 업로드 검증 실패"},
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        409: {"model": ErrorResponse, "description": "상태 전이 규칙 위반"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def start_pdf_extraction(
    correction_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> PdfExtractionAcceptedResponse:
    """PDF 파일 업로드를 받아 추출 작업을 비동기로 시작한다."""
    cid = _validate_correction_id(correction_id)
    service = get_pdf_extraction_service()

    try:
        file_bytes = await _read_upload_within_limit(file)

        await service.start_extraction(
            correction_id=cid,
            file_bytes=file_bytes,
            filename=file.filename or "",
            content_type=file.content_type,
            background_tasks=background_tasks,
        )
        return PdfExtractionAcceptedResponse(
            correction_id=correction_id,
            status="accepted",
            message="PDF 추출 요청이 접수되었습니다.",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except MainServerError as exc:
        _handle_main_server_error(exc)
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()
    finally:
        with suppress(Exception):
            await file.close()


@router.post(
    "/{correction_id}/pdf-extraction/stream",
    summary="PDF 추출 활동 단위 스트리밍",
    description=(
        "PDF 업로드를 받아 활동이 완성될 때마다 SSE 로 흘려보낸다. "
        "저장은 스트림이 끝난 뒤 기존 PDF 추출 결과 콜백 1회로 처리한다."
    ),
    responses={
        200: {"description": "SSE 스트림", "content": {"text/event-stream": {}}},
        400: {"model": ErrorResponse, "description": "PDF 업로드 검증 실패"},
        404: {"model": ErrorResponse, "description": "첨삭이 없는 경우"},
        500: {"model": ErrorResponse, "description": "내부 서버 에러"},
    },
)
async def stream_pdf_extraction(
    correction_id: str,
    file: UploadFile = File(...),
) -> EventSourceResponse:
    """PDF 추출을 활동 단위로 SSE 스트리밍한다."""
    cid = _validate_correction_id(correction_id)
    service = get_pdf_extraction_service()

    try:
        file_bytes = await _read_upload_within_limit(file)
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()
    finally:
        with suppress(Exception):
            await file.close()

    stream = service.stream_extraction(
        correction_id=cid,
        file_bytes=file_bytes,
        filename=file.filename or "",
        content_type=file.content_type,
    )

    return EventSourceResponse(
        interleave_ping_events(stream),
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 프록시 버퍼링 비활성화
        },
    )


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
    cid = _validate_correction_id(correction_id)
    try:
        await get_correction_client().delete_correction(cid)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except MainServerError as exc:
        _handle_main_server_error(exc)
    except HTTPException:
        raise
    except Exception:
        _raise_internal_server_error()
