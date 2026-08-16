"""커밋과 gap 분석을 graph 밖에서 병렬 실행하는 coordinator."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress

from app.schemas.experience_map import (
    CommitResultEvent,
    CompletedMessage,
    ExperienceMapEvent,
    MessageCompleteEvent,
    SuggestionGap,
    SuggestionReadyEvent,
)
from features.experience_map.nodes.commit import commit_changes
from features.experience_map.nodes.gap_analysis import analyze_gap
from features.experience_map.nodes.result_response import build_result_response
from features.experience_map.nodes.suggestion_response import build_suggestion
from features.experience_map.schemas import CommitResult
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

StateRunner = Callable[[ExperienceMapState], Awaitable[ExperienceMapState]]
ActiveGapSaver = Callable[[str, dict | None], Awaitable[None]]


async def coordinate(
    state: ExperienceMapState,
    *,
    commit_runner: StateRunner = commit_changes,
    gap_runner: StateRunner | None = None,
    save_active_gap: ActiveGapSaver | None = None,
) -> AsyncIterator[ExperienceMapEvent]:
    """커밋 결과를 우선 보내고, 늦은 gap 분석은 뒤이어 보낸다.

    commit 실패는 gap task를 취소하고 그대로 전파한다. gap 실패는 완료된 커밋을
    실패로 바꾸지 않으며 suggestion 이벤트만 생략한다. 두 task에는 별도 state 복사본을
    넘겨 서로의 중간 필드를 덮어쓰지 못하게 한다.
    """
    run_gap = gap_runner or _run_gap
    commit_task = asyncio.create_task(commit_runner(dict(state)))
    gap_task = asyncio.create_task(run_gap(dict(state)))

    try:
        committed_state = await commit_task
    except BaseException:
        gap_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await gap_task
        raise

    result = CommitResult.model_validate(committed_state.get("commit_result"))
    yield CommitResultEvent(result=result)
    yield MessageCompleteEvent(
        message=CompletedMessage(
            request_id=_required_string(state, "request_id"),
            session_id=_required_string(state, "session_id"),
            response_kind="result",
            ai_response=build_result_response(committed_state, result),
            committed=True,
            map_version=result.map_version,
            can_revert=result.can_revert,
        )
    )

    try:
        gap_state = await gap_task
        suggestion = gap_state.get("suggestion")
        if not isinstance(suggestion, dict):
            raise ValueError("gap 분석 결과에 suggestion이 없습니다.")
        if save_active_gap is not None:
            await save_active_gap(_required_string(state, "user_id"), gap_state.get("active_gap"))
        raw_gap = suggestion.get("gap")
        yield SuggestionReadyEvent(
            gap=SuggestionGap.model_validate(raw_gap) if raw_gap is not None else None
        )
        yield MessageCompleteEvent(
            message=CompletedMessage(
                request_id=_required_string(state, "request_id"),
                session_id=_required_string(state, "session_id"),
                response_kind="suggestion",
                ai_response=str(suggestion.get("message") or ""),
                committed=False,
            )
        )
    except Exception:
        logger.exception("coordinator: gap 분석 실패 - suggestion 이벤트 생략")


async def _run_gap(state: ExperienceMapState) -> ExperienceMapState:
    """gap 분석과 alias→실제 ID 제안 변환을 한 task로 묶는다."""
    return build_suggestion(await analyze_gap(state))


def _required_string(state: ExperienceMapState, field: str) -> str:
    """SSE 완료 메시지의 필수 식별자를 읽는다."""
    value = state.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"coordinator에 필요한 {field} 값이 없습니다.")
    return value


__all__ = ["ActiveGapSaver", "StateRunner", "coordinate"]
