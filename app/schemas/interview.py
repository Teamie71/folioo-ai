"""인터뷰 API 스키마 정의"""

from datetime import datetime

from pydantic import BaseModel, Field


# ===== 공통 타입 =====
class StageProgressSchema(BaseModel):
    """단계 진행 상황 스키마"""

    fixed_q_used: int = Field(..., description="사용자가 답변 완료한 고정 질문 수")
    fixed_q_total: int = Field(..., description="전체 고정 질문 수")
    generated_q_used: int = Field(..., description="사용자가 답변 완료한 생성 질문 수")
    generated_q_max: int = Field(..., description="최대 생성 질문 수")
    force_all_generated_q: bool = Field(..., description="생성 질문 강제 소진 여부")
    is_complete: bool = Field(..., description="현재 단계 완료 여부")


class CollectedFieldSchema(BaseModel):
    """수집된 필드 정보 스키마"""

    field_name: str = Field(..., description="필드 이름")
    description: str = Field(..., description="필드 설명")
    value: str | list | None = Field(None, description="수집된 값")
    completeness: float = Field(..., ge=0.0, le=1.0, description="완성도 (0.0 ~ 1.0)")


class MessageSchema(BaseModel):
    """대화 메시지 스키마"""

    type: str = Field(..., description="메시지 타입 (human, ai, system)")
    content: str = Field(..., description="메시지 내용")
    id: str | None = Field(None, description="메시지 ID")


# ===== 세션 생성 =====
class CreateSessionRequest(BaseModel):
    """세션 생성 요청"""

    user_id: str = Field(..., min_length=1, description="사용자 ID")
    experience_name: str = Field(..., min_length=1, description="정리할 경험/프로젝트 이름")


class CreateSessionResponse(BaseModel):
    """세션 생성 응답"""

    session_id: str = Field(..., description="생성된 세션 ID")
    first_question: str = Field(..., description="AI의 첫 질문")
    current_stage: int = Field(..., ge=1, le=4, description="현재 단계")
    stage_progress: StageProgressSchema = Field(..., description="단계 진행 상황")


# ===== 채팅 =====
class ChatRequest(BaseModel):
    """채팅 요청"""

    message: str = Field(..., min_length=1, description="사용자 메시지")
    file_ids: list[str] | None = Field(None, description="업로드된 파일 ID 목록")


class ChatResponse(BaseModel):
    """채팅 응답"""

    ai_response: str = Field(..., description="AI 응답 메시지")
    current_stage: int = Field(..., ge=1, le=4, description="현재 단계")
    stage_progress: StageProgressSchema = Field(..., description="단계 진행 상황")
    overall_completion: float = Field(..., ge=0.0, le=100.0, description="전체 완료율 (%)")
    all_complete: bool = Field(..., description="모든 단계 완료 여부")


# ===== 상태 조회 =====
class SessionStateResponse(BaseModel):
    """세션 상태 조회 응답"""

    session_id: str = Field(..., description="세션 ID")
    user_id: str = Field(..., description="사용자 ID")
    experience_name: str = Field(..., description="경험/프로젝트 이름")
    current_stage: int = Field(..., ge=1, le=4, description="현재 단계")
    stage_progress: StageProgressSchema = Field(..., description="단계 진행 상황")
    overall_completion: float = Field(..., ge=0.0, le=100.0, description="전체 완료율 (%)")
    all_complete: bool = Field(..., description="모든 단계 완료 여부")
    message_count: int = Field(..., ge=0, description="총 메시지 수")
    is_extended_mode: bool = Field(..., description="추가 대화 모드 여부")
    collected_data: dict[str, dict[str, CollectedFieldSchema]] = Field(
        ..., description="수집된 포트폴리오 데이터"
    )
    messages: list[MessageSchema] = Field(..., description="전체 대화 기록")


# ===== 에러 응답 =====
class ErrorResponse(BaseModel):
    """에러 응답"""

    detail: str = Field(..., description="에러 상세 메시지")


# ===== SSE 스트리밍 이벤트 =====
class SSETextDelta(BaseModel):
    """토큰 델타 내용"""

    type: str = Field(default="text_delta", description="델타 타입")
    text: str = Field(..., description="스트리밍된 텍스트 조각")


class SSEContentBlockDelta(BaseModel):
    """LLM 토큰 스트리밍 이벤트"""

    type: str = Field(default="content_block_delta", description="이벤트 타입")
    delta: SSETextDelta = Field(..., description="텍스트 델타")


class SSEMessagePayload(BaseModel):
    """최종 완료 메시지 내용"""

    ai_response: str = Field(..., description="전체 AI 응답")
    current_stage: int = Field(..., ge=1, le=4, description="현재 단계")
    stage_progress: StageProgressSchema = Field(..., description="단계 진행 상황")
    overall_completion: float = Field(..., ge=0.0, le=100.0, description="전체 완료율 (%)")
    all_complete: bool = Field(..., description="모든 단계 완료 여부")


class SSEMessageComplete(BaseModel):
    """전체 처리 완료 이벤트"""

    type: str = Field(default="message_complete", description="이벤트 타입")
    message: SSEMessagePayload = Field(..., description="완료 메시지")


class SSEErrorDetail(BaseModel):
    """에러 상세 정보"""

    code: str = Field(..., description="에러 코드 (session_not_found, llm_error, internal_error)")
    message: str = Field(..., description="에러 메시지")


class SSEError(BaseModel):
    """에러 이벤트"""

    type: str = Field(default="error", description="이벤트 타입")
    error: SSEErrorDetail = Field(..., description="에러 상세")


class SSEPing(BaseModel):
    """하트비트 이벤트"""

    type: str = Field(default="ping", description="이벤트 타입")
    timestamp: datetime = Field(..., description="전송 시각 (ISO 8601)")
