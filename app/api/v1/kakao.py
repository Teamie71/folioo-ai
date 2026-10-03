"""카카오톡 대화 턴 API (메인 서버 → AI 서버, `X-API-Key` 인증)

경로는 메인 서버와 맞춘 루트 경로다. 세션 생성의 호환 경로(`/sessions`)와 같은 방식으로
`/api/v1` 접두사 없이 등록한다.
"""

import logging
import uuid

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from features.experience_map.errors import ExperienceMapError, RequestNotFoundError
from features.experience_map.kakao import get_kakao_service
from features.experience_map.kakao.schemas import (
    KakaoTurnAcceptedResponse,
    KakaoTurnRequest,
    KakaoTurnStatusResponse,
)

logger = logging.getLogger(__name__)

kakao_router = APIRouter(tags=["kakao"])


def _error_response(exc: ExperienceMapError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_response().model_dump())


@kakao_router.post(
    "/sessions/{session_id}/kakao/turns",
    response_model=KakaoTurnAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="카카오 턴 접수",
    description=(
        "검증 뒤 작업을 영속적으로 접수하고 즉시 202를 반환합니다. 같은 본문의 재접수도 "
        "202이고, 같은 request_id에 다른 본문이면 409입니다."
    ),
)
async def accept_kakao_turn(session_id: str, payload: KakaoTurnRequest):
    try:
        uuid.UUID(session_id)
    except ValueError:
        return _error_response(RequestNotFoundError("세션을 찾을 수 없습니다."))

    try:
        row = await get_kakao_service().accept(session_id, payload)
    except ExperienceMapError as exc:
        return _error_response(exc)
    return KakaoTurnAcceptedResponse(request_id=row.request_id, state=row.state)  # type: ignore[arg-type]


@kakao_router.get(
    "/kakao/turns/{request_id}",
    response_model=KakaoTurnStatusResponse,
    summary="카카오 턴 상태 조회",
    description=(
        "접수·실행 여부와 정리할 결과를 확인합니다. 메인 서버 CommonResponse 래퍼 없이 "
        "최상위 JSON을 반환하며, 기록이 없으면 404입니다."
    ),
)
async def get_kakao_turn(request_id: str, user_id: str):
    try:
        uuid.UUID(request_id)
    except ValueError:
        return _error_response(RequestNotFoundError())

    result = await get_kakao_service().get_status(request_id, user_id)
    if result is None:
        return _error_response(RequestNotFoundError())
    return result
