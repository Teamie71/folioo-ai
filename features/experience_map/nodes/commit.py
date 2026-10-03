"""메인 서버 커밋 위임과 map version 충돌 복구 판단."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from pydantic import ValidationError as PydanticValidationError

from features.experience_map.errors import CommitConflictError, MapVersionConflictError
from features.experience_map.main_client import ExperienceMapMainClient
from features.experience_map.schemas import (
    CommitAddItem,
    CommitItem,
    CommitResult,
    CommitUpdateItem,
    DroppedItem,
    StructuredItem,
)
from features.experience_map.state import ExperienceMapState

MapRefresher = Callable[[ExperienceMapState], Awaitable[ExperienceMapState]]


async def commit_changes(
    state: ExperienceMapState,
    *,
    client: ExperienceMapMainClient | None = None,
    refresh_map: MapRefresher | None = None,
) -> ExperienceMapState:
    """별칭 operation을 실제 ID로 바꿔 메인 서버에 한 번 커밋한다.

    커밋 코루틴은 SSE 연결 취소의 영향을 받지 않도록 shield로 감싼다. 네트워크로
    응답만 유실된 경우에는 같은 request ID의 결과를 조회해 중복 POST 없이 복구한다.
    map version 충돌은 최신 맵을 주입받아 재구성 진입점만 결정한다. 실제 맵 조회와
    그래프 재실행은 coordinator가 소유하므로 이 노드는 DB에 직접 접근하지 않는다.

    Args:
        state: validate를 통과한 경험정리 state
        client: 테스트 또는 coordinator가 주입하는 메인 서버 클라이언트
        refresh_map: 최신 맵을 읽어 state 컨텍스트를 다시 만드는 콜백

    Returns:
        ExperienceMapState: 성공 결과 또는 충돌 복구를 위한 다음 진입점이 담긴 state

    Raises:
        CommitConflictError: 두 번째 version 충돌 또는 최신 맵 재조회 불가 시
        ValueError: 커밋에 필요한 state가 올바르지 않은 경우
    """
    commit_client = client or ExperienceMapMainClient()
    items = _to_commit_items(state)
    try:
        # 연결이 끊겨도 메인 서버의 단일 커밋 요청은 취소하지 않는다.
        result = await asyncio.shield(
            commit_client.commit(
                user_id=_required_string(state, "user_id"),
                request_id=_required_string(state, "request_id"),
                base_map_version=_required_version(state),
                items=items,
            )
        )
    except MapVersionConflictError as exc:
        return await _recover_map_conflict(state, exc, refresh_map)
    except (httpx.TimeoutException, httpx.NetworkError):
        recovery = await commit_client.get_commit(_required_string(state, "request_id"))
        if not recovery.committed or recovery.result is None:
            raise
        result = recovery.result

    return _with_commit_result(state, result)


def next_node(state: ExperienceMapState) -> str:
    """커밋 뒤 coordinator 또는 충돌 재구성 노드를 선택한다."""
    if state.get("commit_recovery_node"):
        return state["commit_recovery_node"]
    return "coordinator" if state.get("commit_result") else "fallback"


def _to_commit_items(state: ExperienceMapState) -> list[CommitItem]:
    """validate가 확정한 별칭 operation을 메인 API payload로 변환한다."""
    aliases = state.get("alias_to_block_id", {})
    converted: list[CommitItem] = []
    for raw in state.get("commit_items", []):
        try:
            item = StructuredItem.model_validate(raw)
            if item.action == "add":
                converted.append(
                    CommitAddItem(
                        item_id=item.item_id,
                        parent_id=_resolve_alias(aliases, item.parent_ref, required=True),
                        parent_item_id=item.parent_item_id,
                        section_kind=item.section_kind,
                        slot_id=item.slot_id,
                        content=item.text,
                        after_id=_resolve_alias(aliases, item.after_ref, required=True),
                    )
                )
            else:
                converted.append(
                    CommitUpdateItem(
                        item_id=item.item_id,
                        target_id=_resolve_alias(aliases, item.target_ref, required=True),
                        content=item.text,
                    )
                )
        except (PydanticValidationError, ValueError) as exc:
            raise ValueError(f"커밋 operation을 실제 ID로 변환할 수 없습니다: {raw!r}") from exc
    if not converted:
        raise ValueError("커밋할 operation이 없습니다.")
    return converted


async def _recover_map_conflict(
    state: ExperienceMapState,
    error: MapVersionConflictError,
    refresh_map: MapRefresher | None,
) -> ExperienceMapState:
    """첫 충돌의 최신 맵을 반영하고 validate/structure 재진입점을 정한다."""
    if state.get("commit_conflict_count", 0) >= 1:
        raise CommitConflictError(failed_node="commit") from error
    if refresh_map is None:
        raise CommitConflictError(
            "최신 경험 맵을 다시 읽을 수 없습니다.", failed_node="commit"
        ) from error

    refreshed = await refresh_map(dict(state))
    old_aliases = state.get("alias_to_block_id", {})
    new_aliases = refreshed.get("alias_to_block_id", {})
    remapped, structure_maintained = _remap_operations(state, old_aliases, new_aliases)

    updated = dict(refreshed)
    updated["current_node"] = "commit"
    updated["commit_conflict_count"] = state.get("commit_conflict_count", 0) + 1
    updated["commit_result"] = None
    if error.current_map_version is not None and updated.get("map_version") is None:
        updated["map_version"] = error.current_map_version

    if structure_maintained:
        updated["structured_items"] = remapped
        updated["gap_update_item"] = _remap_one_operation(
            state.get("gap_update_item"), old_aliases, new_aliases
        )[0]
        updated["target_experience_alias"] = _remap_alias(
            state.get("target_experience_alias"), old_aliases, new_aliases
        )
        updated["commit_recovery_node"] = "validate"
    else:
        updated["commit_recovery_node"] = "structure"
    return updated  # type: ignore[return-value]


def _remap_operations(
    state: ExperienceMapState, old_aliases: dict[str, str], new_aliases: dict[str, str]
) -> tuple[list[dict], bool]:
    """기존 별칭이 가리키던 실제 block ID 기준으로 최신 별칭을 다시 붙인다."""
    remapped: list[dict] = []
    maintained = True
    for raw in state.get("structured_items", []):
        item, valid = _remap_one_operation(raw, old_aliases, new_aliases)
        if item is not None:
            remapped.append(item)
        maintained = maintained and valid

    _, gap_valid = _remap_one_operation(state.get("gap_update_item"), old_aliases, new_aliases)
    return remapped, maintained and gap_valid and _target_activity_is_present(
        state, old_aliases, new_aliases
    )


def _remap_one_operation(
    raw: dict | None, old_aliases: dict[str, str], new_aliases: dict[str, str]
) -> tuple[dict | None, bool]:
    """operation 하나의 기존 블록 별칭을 최신 alias로 치환한다."""
    if raw is None:
        return None, True
    remapped = dict(raw)
    alias_by_id = {block_id: alias for alias, block_id in new_aliases.items()}
    valid = True
    for field in ("parent_ref", "target_ref", "after_ref"):
        alias = remapped.get(field)
        if alias is None:
            continue
        block_id = old_aliases.get(alias)
        new_alias = alias_by_id.get(block_id) if block_id else None
        if new_alias is None:
            valid = False
            continue
        remapped[field] = new_alias
    return remapped, valid


def _target_activity_is_present(
    state: ExperienceMapState, old_aliases: dict[str, str], new_aliases: dict[str, str]
) -> bool:
    """선택 활동 자체가 최신 맵에도 남아 있는지 확인하고 별칭을 갱신한다."""
    target_alias = state.get("target_experience_alias")
    if target_alias is None:
        return True
    target_id = old_aliases.get(target_alias)
    return target_id in new_aliases.values()


def _remap_alias(
    alias: str | None, old_aliases: dict[str, str], new_aliases: dict[str, str]
) -> str | None:
    """한 기존 별칭을 동일한 실제 ID의 최신 별칭으로 바꾼다."""
    if alias is None:
        return None
    target_id = old_aliases.get(alias)
    return next((new for new, block_id in new_aliases.items() if block_id == target_id), None)


def _resolve_alias(
    aliases: dict[str, str], alias: str | None, *, required: bool = False
) -> str | None:
    """LLM 별칭을 실제 ID로 안전하게 바꾼다."""
    if alias is None:
        return None
    block_id = aliases.get(alias)
    if block_id is None and required:
        raise ValueError(f"알 수 없는 block 별칭입니다: {alias}")
    return block_id


def _required_string(state: ExperienceMapState, field: str) -> str:
    """필수 문자열 state 필드를 읽는다."""
    value = state.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"커밋에 필요한 {field} 값이 없습니다.")
    return value


def _required_version(state: ExperienceMapState) -> int:
    """커밋 기준 map version을 읽는다."""
    version = state.get("map_version")
    if not isinstance(version, int):
        raise ValueError("커밋에 필요한 map_version 값이 없습니다.")
    return version


def _with_commit_result(state: ExperienceMapState, result: CommitResult) -> ExperienceMapState:
    """로컬에서 제외한 item까지 포함해 직렬화 가능한 커밋 결과를 만든다."""
    dropped = [DroppedItem.model_validate(item) for item in state.get("dropped_items", [])]
    completed = result.model_copy(update={"dropped": dropped})
    updated = dict(state)
    updated["current_node"] = "commit"
    updated["commit_recovery_node"] = None
    updated["commit_result"] = completed.model_dump(mode="json")
    return updated  # type: ignore[return-value]


__all__ = ["MapRefresher", "commit_changes", "next_node"]
