"""커밋·gap 병렬 coordinator와 결정적 결과 문구 테스트."""

import asyncio

import pytest

from features.experience_map.coordinator import coordinate
from features.experience_map.errors import CommitConflictError, LlmError
from features.experience_map.nodes.result_response import build_result_response
from features.experience_map.schemas import AppliedItem, CommitResult, DroppedItem

REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def state() -> dict:
    """coordinator에 전달할 validate 완료 state."""
    return {
        "user_id": "123",
        "session_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
        "request_id": REQUEST_ID,
        "commit_items": [
            {"item_id": "add_1", "action": "add"},
            {"item_id": "update_1", "action": "update"},
        ],
    }


def result(*, dropped: int = 0, applied: list[AppliedItem] | None = None) -> CommitResult:
    """테스트용 메인 서버 커밋 결과."""
    return CommitResult(
        request_id=REQUEST_ID,
        previous_version=7,
        map_version=8,
        revert_to_version=7,
        can_revert=True,
        applied=applied
        or [AppliedItem(item_id="add_1", block_id="401", path="커머스 리뉴얼 > 담당업무")],
        dropped=[
            DroppedItem(item_id=f"drop_{index}", reason="validation_retry_exceeded")
            for index in range(dropped)
        ],
    )


def committed_state(commit_result: CommitResult | None = None) -> dict:
    """commit 노드 성공 결과 state."""
    return state() | {"commit_result": (commit_result or result()).model_dump(mode="json")}


def suggestion_state() -> dict:
    """gap 분석·제안 변환 완료 state."""
    return state() | {
        "active_gap": {
            "gap_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
            "gap_type": "extend_block",
            "anchor_block_id": "401",
            "message": "개선안을 선택한 기준은 무엇이었나요?",
            "created_request_id": REQUEST_ID,
        },
        "suggestion": {
            "gap": {
                "gap_id": "d9428888-122b-11e1-b85c-61cd3cbb3210",
                "gap_type": "extend_block",
                "anchor_block_id": "401",
                "path": "커머스 리뉴얼 > 담당업무",
                "message": "개선안을 선택한 기준은 무엇이었나요?",
            },
            "message": "개선안을 선택한 기준은 무엇이었나요?",
        },
    }


async def collect(**kwargs) -> list:
    """coordinator SSE 이벤트를 리스트로 수집한다."""
    return [event async for event in coordinate(state(), **kwargs)]


def test_result_response_uses_fixed_intro_and_dropped_template():
    """에이전트 문서 3-8: 첫 문장은 항상 고정, 글자 수 초과 안내는 별도 문단."""
    message = build_result_response(state(), result(dropped=2))

    assert message.startswith("내용을 분석하여 경험을 정리했어요.\n- 담당업무 아래 1개의 블록 생성")
    assert "2개는 글자 수 제한(500자)을 넘어" in message


def test_result_response_lists_each_category_as_its_own_bullet():
    commit_result = result(
        applied=[
            AppliedItem(item_id="add_1", block_id="401", path="커머스 리뉴얼 > 담당업무"),
            AppliedItem(item_id="update_1", block_id="402", path="커머스 리뉴얼 > 주요성과"),
        ]
    )

    message = build_result_response(state(), commit_result)

    assert message == (
        "내용을 분석하여 경험을 정리했어요.\n"
        "- 담당업무 아래 1개의 블록 생성\n"
        "- 주요성과 아래 1개의 블록 수정"
    )


def test_result_response_uses_update_only_template():
    commit_result = result(
        applied=[AppliedItem(item_id="update_1", block_id="402", path="커머스 리뉴얼 > 주요성과")]
    )

    assert build_result_response(state(), commit_result) == (
        "내용을 분석하여 경험을 정리했어요.\n- 주요성과 아래 1개의 블록 수정"
    )


def test_result_response_marks_newly_created_category():
    """에이전트 문서 3-8: 3단계 카테고리를 새로 만든 경우에만 '{카테고리} 생성' 불렛이 붙는다.

    새 컨테이너는 내용이 없어 path에 자기 라벨이 실리지 않으므로(2-4-3), commit_items의
    section_kind로 새로 만든 카테고리인지 판단해야 한다.
    """
    commit_state = state() | {
        "commit_items": [
            {"item_id": "category_1", "action": "add", "section_kind": "TASK"},
            {"item_id": "anchor_1", "action": "add", "parent_item_id": "category_1"},
            {"item_id": "add_1", "action": "add", "parent_item_id": "anchor_1"},
        ]
    }
    commit_result = result(
        applied=[
            # 컨테이너 자신의 path는 활동명뿐이다 — 내용이 없어 자기 라벨이 안 실린다.
            AppliedItem(item_id="category_1", block_id="400", path="커머스 리뉴얼"),
            AppliedItem(item_id="anchor_1", block_id="401", path="커머스 리뉴얼"),
            AppliedItem(item_id="add_1", block_id="402", path="커머스 리뉴얼"),
        ]
    )

    message = build_result_response(commit_state, commit_result)

    assert message == (
        "내용을 분석하여 경험을 정리했어요.\n- 담당업무 아래 2개의 블록 생성\n- 담당업무 생성"
    )


@pytest.mark.asyncio
async def test_commit_result_is_emitted_before_slow_gap_suggestion():
    """느린 gap 분석은 commit_result와 결과 메시지를 지연시키지 않는다."""
    release_gap = asyncio.Event()

    async def run_commit(input_state):
        return committed_state()

    async def run_gap(input_state):
        await release_gap.wait()
        return suggestion_state()

    events = coordinate(state(), commit_runner=run_commit, gap_runner=run_gap)
    assert (await anext(events)).type == "node_status"  # commit: running
    assert (await anext(events)).type == "node_status"  # commit: completed
    assert (await anext(events)).type == "commit_result"
    assert (await anext(events)).type == "message_complete"
    release_gap.set()
    assert (await anext(events)).type == "suggestion_ready"
    assert (await anext(events)).type == "message_complete"


@pytest.mark.asyncio
async def test_gap_failure_uses_fixed_suggestion_and_clears_previous_gap():
    saved: list[tuple[str, dict | None]] = []

    async def run_commit(input_state):
        return committed_state()

    async def run_gap(input_state):
        raise LlmError(failed_node="gap_analysis")

    async def save_gap(user_id: str, gap: dict | None):
        saved.append((user_id, gap))

    events = await collect(
        commit_runner=run_commit,
        gap_runner=run_gap,
        save_active_gap=save_gap,
    )

    assert [event.type for event in events] == [
        "node_status",
        "node_status",
        "commit_result",
        "message_complete",
        "suggestion_ready",
        "message_complete",
    ]
    assert events[-1].message.ai_response == "더 정리하고 싶으신 내용이 있나요?"
    assert saved == [("123", None)]


@pytest.mark.asyncio
async def test_commit_failure_cancels_gap_and_sends_no_suggestion():
    cancelled = asyncio.Event()

    async def run_commit(input_state):
        raise CommitConflictError(failed_node="commit")

    async def run_gap(input_state):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(CommitConflictError):
        await collect(commit_runner=run_commit, gap_runner=run_gap)

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_successful_gap_is_persisted_after_commit():
    saved: list[tuple[str, dict | None]] = []

    async def run_commit(input_state):
        return committed_state()

    async def run_gap(input_state):
        return suggestion_state()

    async def save_gap(user_id: str, gap: dict | None):
        saved.append((user_id, gap))

    events = await collect(commit_runner=run_commit, gap_runner=run_gap, save_active_gap=save_gap)

    assert [event.type for event in events] == [
        "node_status",
        "node_status",
        "commit_result",
        "message_complete",
        "suggestion_ready",
        "message_complete",
    ]
    assert saved == [("123", suggestion_state()["active_gap"])]


@pytest.mark.asyncio
async def test_new_commit_item_anchor_is_resolved_after_parallel_gap_analysis():
    """gap 분석과 commit은 병렬이어도 새 블록 ID 변환은 commit 결과를 기다린다."""
    saved: list[dict | None] = []

    async def run_commit(input_state):
        return committed_state()

    async def run_gap(input_state):
        return input_state | {
            "gap_candidate": {
                "gap_type": "extend_block",
                "anchor_ref": "add_1",
                "reason": "판단 기준 부족",
            },
            "gap_message": "개선안을 선택한 기준은 무엇이었나요?",
        }

    async def save_gap(user_id: str, gap: dict | None):
        saved.append(gap)

    events = await collect(
        commit_runner=run_commit,
        gap_runner=run_gap,
        save_active_gap=save_gap,
    )

    suggestion = events[-2].gap
    assert suggestion is not None
    assert suggestion.anchor_block_id == "401"
    assert suggestion.path == "커머스 리뉴얼 > 담당업무"
    assert saved[0]["anchor_block_id"] == "401"


@pytest.mark.asyncio
async def test_first_map_conflict_reprocesses_and_restarts_gap_from_recovered_state():
    """첫 충돌은 최신 state를 재처리하고 최종 items 기준으로 gap 분석을 다시 시작한다."""
    commit_calls: list[dict] = []
    gap_started: list[str] = []
    first_gap_cancelled = asyncio.Event()

    async def run_commit(input_state):
        commit_calls.append(input_state)
        if len(commit_calls) == 1:
            return input_state | {"commit_recovery_node": "validate"}
        return committed_state()

    async def run_gap(input_state):
        marker = str(input_state.get("recovered", "initial"))
        gap_started.append(marker)
        if marker == "initial":
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                first_gap_cancelled.set()
                raise
        return suggestion_state()

    async def recover(input_state, entry_node):
        assert entry_node == "validate"
        return input_state | {
            "recovered": "latest-map",
            "commit_recovery_node": None,
            "commit_items": [{"item_id": "add_1", "action": "add"}],
        }

    events = await collect(
        commit_runner=run_commit,
        gap_runner=run_gap,
        recover_commit=recover,
    )

    assert first_gap_cancelled.is_set()
    assert gap_started == ["initial", "latest-map"]
    assert len(commit_calls) == 2
    assert [event.type for event in events] == [
        "node_status",
        "node_status",
        "commit_result",
        "message_complete",
        "suggestion_ready",
        "message_complete",
    ]
