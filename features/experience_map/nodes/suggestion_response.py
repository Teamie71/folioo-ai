"""gap 후보를 다음 턴 state와 사용자 제안 payload로 변환한다."""

from uuid import uuid4

from features.experience_map.nodes.gap_analysis import NO_GAP_MESSAGE
from features.experience_map.schemas import ActiveGap, GapCandidate
from features.experience_map.state import ExperienceMapState


def build_suggestion(state: ExperienceMapState) -> ExperienceMapState:
    """별칭 gap 후보를 실제 ID active_gap과 화면용 suggestion으로 바꾼다.

    gap 분석이 정상적으로 gap 없음으로 끝난 경우에도 suggestion은 남긴다. 반면 gap
    분석 실패는 예외로 coordinator에 전달되므로 이 함수를 호출하지 않아 이벤트 자체를
    생략할 수 있다.
    """
    updated = dict(state)
    updated["current_node"] = "suggestion_response"
    raw_candidate = state.get("gap_candidate")
    if raw_candidate is None:
        updated["active_gap"] = None
        updated["suggestion"] = {"gap": None, "message": NO_GAP_MESSAGE}
        return updated  # type: ignore[return-value]

    candidate = GapCandidate.model_validate(raw_candidate)
    anchor_id, anchor_path = _resolve_anchor(state, candidate.anchor_ref)
    request_id = state.get("request_id")
    if not anchor_id or not isinstance(request_id, str) or not request_id:
        raise ValueError("gap을 저장할 기준 블록 또는 request_id를 찾을 수 없습니다.")
    message = state.get("gap_message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("gap 제안 문구가 없습니다.")

    gap = ActiveGap(
        gap_id=str(uuid4()),
        gap_type=candidate.gap_type,
        anchor_block_id=anchor_id,
        message=message.strip(),
        created_request_id=request_id,
    )
    updated["active_gap"] = gap.model_dump()
    updated["suggestion"] = {
        "gap": {
            **gap.model_dump(),
            "path": anchor_path,
        },
        "message": gap.message,
    }
    return updated  # type: ignore[return-value]


def _resolve_anchor(state: ExperienceMapState, anchor_ref: str) -> tuple[str | None, str]:
    """기존 alias 또는 방금 커밋한 item_id를 실제 block ID와 경로로 바꾼다."""
    existing_id = state.get("alias_to_block_id", {}).get(anchor_ref)
    if existing_id:
        return existing_id, _path_for_anchor(state.get("activity_tree_text"), anchor_ref)

    commit_result = state.get("commit_result") or {}
    applied = commit_result.get("applied", []) if isinstance(commit_result, dict) else []
    for item in applied:
        if not isinstance(item, dict) or item.get("item_id") != anchor_ref:
            continue
        block_id = item.get("block_id")
        path = item.get("path")
        if isinstance(block_id, str) and block_id:
            return block_id, str(path or anchor_ref)
    return None, anchor_ref


def _path_for_anchor(activity_tree: str | None, anchor_alias: str) -> str:
    """활동 트리의 들여쓰기에서 화면에 보일 기준 block 경로를 만든다."""
    if not activity_tree:
        return anchor_alias
    stack: list[tuple[int, str]] = []
    for line in activity_tree.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("[") or "] " not in stripped:
            continue
        alias, label = stripped[1:].split("] ", maxsplit=1)
        depth = (len(line) - len(stripped)) // 2
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, label))
        if alias == anchor_alias:
            return " > ".join(part for _, part in stack)
    return anchor_alias


__all__ = ["build_suggestion"]
