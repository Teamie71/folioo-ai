"""커밋 위임과 map version 충돌 복구 테스트."""

import httpx
import pytest

from features.experience_map.errors import CommitConflictError, MapVersionConflictError
from features.experience_map.main_client import CommitRecoveryResult
from features.experience_map.nodes.commit import commit_changes, next_node
from features.experience_map.schemas import AppliedItem, CommitResult

REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def commit_result() -> CommitResult:
    """테스트용 메인 서버 커밋 결과."""
    return CommitResult(
        request_id=REQUEST_ID,
        previous_version=7,
        map_version=8,
        revert_to_version=7,
        can_revert=True,
        applied=[AppliedItem(item_id="it_1", block_id="401", path="경험 > 상세정보")],
    )


def valid_state() -> dict:
    """기존 블록 추가와 수정을 함께 가진 validate 완료 state."""
    return {
        "user_id": "123",
        "request_id": REQUEST_ID,
        "map_version": 7,
        "target_experience_alias": "exp_1",
        "alias_to_block_id": {"exp_1": "100", "b_1": "200", "b_2": "300"},
        "structured_items": [
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "section_kind": "DETAIL",
                "text": "사용자 인터뷰 결과를 반영했습니다.",
                "after_ref": "b_2",
            },
            {
                "item_id": "it_2",
                "action": "update",
                "target_ref": "b_2",
                "text": "기존 문장을 더 명확하게 다듬었습니다.",
            },
        ],
        "commit_items": [
            {
                "item_id": "it_1",
                "action": "add",
                "parent_ref": "b_1",
                "section_kind": "DETAIL",
                "text": "사용자 인터뷰 결과를 반영했습니다.",
                "after_ref": "b_2",
            },
            {
                "item_id": "it_2",
                "action": "update",
                "target_ref": "b_2",
                "text": "기존 문장을 더 명확하게 다듬었습니다.",
            },
        ],
        "refined_items": [],
        "dropped_items": [{"item_id": "it_dropped", "reason": "validation_retry_exceeded"}],
    }


class ClientStub:
    """커밋 결과와 호출 payload를 제어하는 테스트 대역."""

    def __init__(self, outcome: CommitResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []
        self.recovery = CommitRecoveryResult(committed=False)

    async def commit(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def get_commit(self, request_id: str) -> CommitRecoveryResult:
        assert request_id == REQUEST_ID
        return self.recovery


@pytest.mark.asyncio
async def test_commit_converts_aliases_to_actual_ids_and_keeps_dropped_items():
    """메인 서버에는 실제 ID만 보내고 AI가 제외한 item도 결과에 남긴다."""
    client = ClientStub(commit_result())

    result = await commit_changes(valid_state(), client=client)

    assert [item.model_dump(exclude_none=True) for item in client.calls[0]["items"]] == [
        {
            "item_id": "it_1",
            "action": "add",
            "parent_id": "200",
            "section_kind": "DETAIL",
            "content": "사용자 인터뷰 결과를 반영했습니다.",
            "after_id": "300",
        },
        {
            "item_id": "it_2",
            "action": "update",
            "target_id": "300",
            "content": "기존 문장을 더 명확하게 다듬었습니다.",
        },
    ]
    assert result["commit_result"]["dropped"] == [
        {"item_id": "it_dropped", "reason": "validation_retry_exceeded"}
    ]
    assert next_node(result) == "coordinator"


@pytest.mark.asyncio
async def test_network_response_loss_recovers_existing_commit_without_second_post():
    """응답 유실은 GET으로 확인하고 POST를 중복 전송하지 않는다."""
    client = ClientStub(httpx.ReadTimeout("응답 유실"))
    client.recovery = CommitRecoveryResult(committed=True, result=commit_result())

    result = await commit_changes(valid_state(), client=client)

    assert len(client.calls) == 1
    assert result["commit_result"]["map_version"] == 8


@pytest.mark.asyncio
async def test_commit_rejects_unknown_optional_alias_instead_of_dropping_it():
    """after_ref처럼 선택 필드여도 알 수 없는 별칭을 null로 바꾸지 않는다."""
    state = valid_state()
    state["commit_items"][0]["after_ref"] = "b_missing"
    client = ClientStub(commit_result())

    with pytest.raises(ValueError, match="실제 ID로 변환"):
        await commit_changes(state, client=client)

    assert client.calls == []


@pytest.mark.asyncio
async def test_first_conflict_remaps_aliases_and_restarts_from_validate():
    """참조 block이 모두 남아 있으면 최신 alias로 바꾼 뒤 validate부터 재실행한다."""
    client = ClientStub(MapVersionConflictError(current_map_version=9))
    refreshed_called = False

    async def refresh_map(state: dict) -> dict:
        nonlocal refreshed_called
        refreshed_called = True
        return {
            **state,
            "map_version": 9,
            "target_experience_alias": "exp_9",
            "alias_to_block_id": {"exp_9": "100", "b_9": "200", "b_10": "300"},
        }

    result = await commit_changes(valid_state(), client=client, refresh_map=refresh_map)

    assert refreshed_called is True
    assert result["commit_conflict_count"] == 1
    assert result["target_experience_alias"] == "exp_9"
    assert result["structured_items"][0]["parent_ref"] == "b_9"
    assert result["structured_items"][0]["after_ref"] == "b_10"
    assert result["structured_items"][1]["target_ref"] == "b_10"
    assert next_node(result) == "validate"


@pytest.mark.asyncio
async def test_first_conflict_restarts_from_structure_when_referenced_block_is_removed():
    """참조 block 하나라도 없어지면 이전 구조화를 재사용하지 않는다."""
    client = ClientStub(MapVersionConflictError(current_map_version=9))

    async def refresh_map(state: dict) -> dict:
        return {**state, "map_version": 9, "alias_to_block_id": {"exp_1": "100", "b_1": "200"}}

    result = await commit_changes(valid_state(), client=client, refresh_map=refresh_map)

    assert next_node(result) == "structure"


@pytest.mark.asyncio
async def test_second_conflict_is_final_commit_conflict():
    """한 번 재구성한 뒤 받은 두 번째 409는 사용자 재시도 가능한 오류가 된다."""
    state = valid_state() | {"commit_conflict_count": 1}
    client = ClientStub(MapVersionConflictError(current_map_version=9))

    with pytest.raises(CommitConflictError) as exc_info:
        await commit_changes(state, client=client)

    assert exc_info.value.code == "commit_conflict"
