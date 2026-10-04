"""카카오톡 턴 API 스키마"""

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_UTTERANCE_CHARS = 500

TurnState = Literal["ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED", "EXPIRED", "COMMIT_UNKNOWN"]
DeliveryStatus = Literal["SUCCESS", "FAIL", "UNKNOWN", "NOT_ATTEMPTED"]
Outcome = Literal["SUCCEEDED", "FAILED", "COMMIT_UNKNOWN"]


class KakaoTurnRequest(BaseModel):
    """메인 서버가 보내는 카톡 턴 접수 요청"""

    user_id: str = Field(..., min_length=1)
    block_id: str = Field(..., min_length=1)
    request_id: str = Field(..., description="이번 턴 식별자 (UUID)")
    utterance: str = Field(..., description="사용자 발화 (500자 이내)")
    callback_url: str = Field(..., min_length=1, description="카카오 1회용 콜백 URL")
    expires_at: datetime = Field(..., description="실행·콜백 준비 기한 (UTC)")

    @field_validator("request_id")
    @classmethod
    def _request_id_is_uuid(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("request_id 가 UUID 형식이 아닙니다.") from exc

    @field_validator("utterance")
    @classmethod
    def _utterance_length(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("utterance 가 비어 있습니다.")
        if len(stripped) > MAX_UTTERANCE_CHARS:
            raise ValueError(f"utterance 는 {MAX_UTTERANCE_CHARS}자 이내여야 합니다.")
        return stripped

    @field_validator("expires_at")
    @classmethod
    def _expires_at_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at 에 시간대(UTC)가 필요합니다.")
        return value.astimezone(UTC)


class KakaoTurnAcceptedResponse(BaseModel):
    """접수 응답. 202 본문"""

    request_id: str
    state: TurnState


class KakaoTurnStatusResponse(BaseModel):
    """상태 조회 응답. 메인 서버 CommonResponse 래퍼 없이 최상위 JSON 으로 반환한다."""

    request_id: str
    state: TurnState
    delivery_status: DeliveryStatus
