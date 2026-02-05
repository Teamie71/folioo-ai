"""인터뷰 API 라우터"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from requests.api import get

from app.schemas.interview import (
    ChatRequest,
    ChatResponse,
    CollectedFieldSchema,
    CreateSessionRequest,
    CreateSessionResponse,
    MessageSchema,
    SessionStateResponse,
    StageProgressSchema,
)
from features.interview import get_interview_service

router = APIRouter(prefix="/interview", tags=["interview"])


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="인터뷰 세션 생성",
    description="새로운 인터뷰 세션을 생성하고 첫 AI 질문을 반환합니다.",
)
async def create_session(request: CreateSessionRequest) -> CreateSessionResponse:
    """
    새 인터뷰 세션 생성

    - 세션 ID는 서버에서 자동 생성 (UUID)
    - 첫 AI 질문이 함께 반환됨
    """

    service = get_interview_service()
    session_id = str(uuid4())

    try:
        result = service.create_session(
            user_id=request.user_id,
            session_id=session_id,
            experience_name=request.experience_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return CreateSessionResponse(
        session_id=result["session_id"],
        first_question=result["first_question"],
        current_stage=result["current_stage"],
        stage_progress=StageProgressSchema(**result["stage_progress"]),
    )


@router.post(
    "/sessions/{session_id}/chat",
    response_model=ChatResponse,
    summary="메시지 전송",
    description="사용자 메시지를 전송하고 AI 응답을 받습니다.",
)
async def chat(session_id: str, request: ChatRequest) -> ChatResponse:
    """
    사용자 메시지 처리 및 AI 응답 생성

    - 세션이 존재하지 않으면 404 에러
    - AI 응답과 함께 현재 진행 상황 반환
    """
    service = get_interview_service()

    try:
        result = service.process_message(
            session_id=session_id,
            message=request.message,
            file_ids=request.file_ids,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    return ChatResponse(
        ai_response=result["ai_response"],
        current_stage=result["current_stage"],
        stage_progress=StageProgressSchema(**result["stage_progress"]),
        overall_completion=result["overall_completion"],
        all_complete=result["all_complete"],
    )


@router.get(
    "/sessions/{session_id}/state",
    summary="세션 상태 조회",
    description="현재 세션의 전체 상태를 조회합니다.",
)
async def get_session_state(session_id: str) -> SessionStateResponse:
    """
    세션 상태 조회

    - 세션이 존재하지 않으면 404 에러
    - 수집된 데이터, 대화 기록 등 전체 상태 반환
    """

    service = get_interview_service()
    state = service.get_session_state(session_id)

    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"세션을 찾을 수 없습니다: {session_id}",
        )

    # 메시지 변환
    messages = [
        MessageSchema(
            type=msg.type,
            content=msg.content,
            id=getattr(msg, "id", None),
        )
        for msg in state["messages"]
    ]

    # collected_data 변환
    collected_data = {}
    for stage_key, fields in state["collected_data"].items():
        collected_data[stage_key] = {
            field_name: CollectedFieldSchema(**field_data)
            for field_name, field_data in fields.items()
        }

    return SessionStateResponse(
        session_id=state["session_id"],
        user_id=state["user_id"],
        experience_name=state["experience_name"],
        current_stage=state["current_stage"],
        stage_progress=StageProgressSchema(**state["stage_progress"]),
        overall_completion=state["overall_completion_percentage"],
        all_complete=state["all_stages_complete"],
        message_count=len(state["messages"]),
        is_extended_mode=state["is_extended_mode"],
        collected_data=collected_data,
        messages=messages,
    )
