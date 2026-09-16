"""커밋과 gap 분석을 graph 밖에서 병렬 실행하는 coordinator."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Literal

from app.schemas.experience_map import (
    CommitResultEvent,
    CompletedMessage,
    ExperienceMapEvent,
    MessageCompleteEvent,
    NodeStatusEvent,
    SuggestionGap,
    SuggestionReadyEvent,
)
from features.experience_map.graph_runner import NODE_STREAMING_PHRASES
from features.experience_map.nodes.commit import commit_changes
from features.experience_map.nodes.gap_analysis import analyze_gap
from features.experience_map.nodes.result_response import build_result_response
from features.experience_map.nodes.suggestion_response import build_suggestion
from features.experience_map.schemas import CommitResult
from features.experience_map.state import ExperienceMapState

logger = logging.getLogger(__name__)

StateRunner = Callable[[ExperienceMapState], Awaitable[ExperienceMapState]]
CommitRecoveryRunner = Callable[
    [ExperienceMapState, Literal["validate", "structure"]], Awaitable[ExperienceMapState]
]
ActiveGapSaver = Callable[[str, dict | None], Awaitable[None]]


async def coordinate(
    state: ExperienceMapState,
    *,
    commit_runner: StateRunner = commit_changes,
    gap_runner: StateRunner | None = None,
    recover_commit: CommitRecoveryRunner | None = None,
    save_active_gap: ActiveGapSaver | None = None,
) -> AsyncIterator[ExperienceMapEvent]:
    """커밋 결과를 우선 보내고, 늦은 gap 분석은 뒤이어 보낸다.

    commit 실패는 gap task를 취소하고 그대로 전파한다. gap 실패는 완료된 커밋을
    실패로 바꾸지 않지만, 문서(에이전트 문서 3절 공통 규칙·API 명세 6절)대로
    `suggestion_ready`와 suggestion `message_complete`를 아예 생략한다 —
    고정 문구로 대체해 보내지 않는다. 두 task에는 별도 state 복사본을 넘겨
    서로의 중간 필드를 덮어쓰지 못하게 한다.
    """
    run_gap = gap_runner or _run_gap
    yield NodeStatusEvent(
        node="commit", status="running", phrase=NODE_STREAMING_PHRASES.get("commit")
    )
    commit_input = dict(state)
    commit_task = asyncio.create_task(commit_runner(commit_input))
    gap_task = asyncio.create_task(run_gap(dict(commit_input)))

    try:
        committed_state = await commit_task
        while recovery_node := committed_state.get("commit_recovery_node"):
            if recover_commit is None:
                raise RuntimeError("map version 충돌 복구 실행기가 설정되지 않았습니다.")

            # 최초 commit items를 기준으로 돌던 gap 분석 결과는 더 이상 유효하지 않다.
            # 최신 맵에서 재구성·재검증한 state를 기준으로 다시 분석한다.
            gap_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await gap_task

            recovered_state = await recover_commit(committed_state, recovery_node)
            gap_task = asyncio.create_task(run_gap(dict(recovered_state)))
            committed_state = await commit_runner(dict(recovered_state))
    except BaseException:
        gap_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await gap_task
        raise

    yield NodeStatusEvent(node="commit", status="completed")
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
        gap_state = {**gap_state, "commit_result": committed_state.get("commit_result")}
        if not isinstance(gap_state.get("suggestion"), dict):
            gap_state = build_suggestion(gap_state)
    except Exception:
        # 문서대로 실패 시 suggestion 이벤트 자체를 생략한다(위 docstring).
        # 다만 이번 턴이 이전 active_gap에 답하는 것이었다면 그 gap은 이미
        # 소비됐으므로, 분석 성공 여부와 무관하게 지운다 — 안 지우면 다음
        # 턴에도 같은 gap이 활성 상태로 남아 새 입력을 그 답변으로 오인한다.
        logger.exception("coordinator: gap 분석 실패 - suggestion 이벤트 생략")
        await _save_active_gap_safely(save_active_gap, state, None)
        return

    suggestion = gap_state.get("suggestion")
    if not isinstance(suggestion, dict):
        raise ValueError("gap 분석 결과에 suggestion이 없습니다.")
    await _save_active_gap_safely(save_active_gap, state, gap_state.get("active_gap"))
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


async def _run_gap(state: ExperienceMapState) -> ExperienceMapState:
    """커밋과 병렬로 gap 후보를 분석한다. 실제 ID 변환은 커밋 성공 뒤 수행한다."""
    return await analyze_gap(state)


async def _save_active_gap_safely(
    save_active_gap: ActiveGapSaver | None,
    state: ExperienceMapState,
    gap: dict | None,
) -> None:
    """gap 저장 실패가 이미 성공한 커밋을 실패로 바꾸지 않게 격리한다."""
    if save_active_gap is None:
        return
    try:
        await save_active_gap(_required_string(state, "user_id"), gap)
    except Exception:
        logger.exception("coordinator: active gap 저장 실패 - 커밋 성공 상태 유지")


def _required_string(state: ExperienceMapState, field: str) -> str:
    """SSE 완료 메시지의 필수 식별자를 읽는다."""
    value = state.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"coordinator에 필요한 {field} 값이 없습니다.")
    return value


__all__ = ["ActiveGapSaver", "CommitRecoveryRunner", "StateRunner", "coordinate"]
