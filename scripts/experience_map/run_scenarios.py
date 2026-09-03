#!/usr/bin/env python3
"""멀티턴 gap 답변·파일 업로드·validate 강제 재시도를 실제 LLM으로 검증한다.

`run_local_turn.py`와 같은 in-memory 백엔드를 쓰되, 한 프로세스 안에서 여러
턴을 이어서 실행하거나 특정 노드를 일부러 실패시켜 재시도 경로를 켠다.

사용법:
    uv run python scripts/experience_map/run_scenarios.py gap
    uv run python scripts/experience_map/run_scenarios.py file
    uv run python scripts/experience_map/run_scenarios.py retry
    uv run python scripts/experience_map/run_scenarios.py all
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.experience_map import graph as graph_module  # noqa: E402
from features.experience_map.nodes import validate as validate_node  # noqa: E402
from features.experience_map.service import compute_request_hash  # noqa: E402
from features.experience_map.state import ExperienceMapState, start_turn  # noqa: E402
from features.experience_map.templates import set_template_catalog_client  # noqa: E402
from features.experience_map.test_runtime import (  # noqa: E402
    InMemoryObjectStore,
    InMemoryTestMapStore,
    TestUiGraphRunner,
    _initial_rows,
    create_test_template_catalog_client,
)
from features.experience_map.upload_store import UploadStore, set_upload_store  # noqa: E402

USER_ID = "9000001"
ACTIVITY_BLOCK_ID = "200"  # _initial_rows()의 "교내 커머스 리뉴얼" level 2 block_id

set_template_catalog_client(create_test_template_catalog_client())


class _BytesReader:
    """`AsyncFileLike`를 만족하는 최소 래퍼."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._sent = False

    async def read(self, size: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._data


async def _build_state(
    store: InMemoryTestMapStore,
    *,
    user_message: str | None,
    context_experience_id: str | None,
    active_gap: dict | None = None,
    file_references: list[dict] | None = None,
) -> ExperienceMapState:
    request_id = str(uuid.uuid4())
    state = start_turn(
        {"user_id": USER_ID, "session_id": "local-session", "active_gap": active_gap},
        request_id=request_id,
        request_hash=compute_request_hash(user_message, context_experience_id, None, []),
        user_message=user_message,
        context_experience_id=context_experience_id,
    )
    state["file_references"] = file_references or []
    snapshot = await store.snapshot(USER_ID)
    state["map_version"] = snapshot.map_version
    state["outline"] = snapshot.outline()
    state["block_id_to_experience_alias"] = snapshot.block_id_to_activity_alias()
    state["block_id_to_content"] = snapshot.block_contents()
    state["activity_contexts"] = {
        alias: {
            "tree_text": context.tree_text,
            "alias_to_block_id": context.alias_to_block_id,
            "alias_metadata": context.alias_metadata,
        }
        for alias in state["block_id_to_experience_alias"].values()
        if (context := snapshot.get_activity_context(alias)) is not None
    }
    if context_experience_id:
        activity_alias = state["block_id_to_experience_alias"].get(context_experience_id)
        if activity_alias:
            context = state["activity_contexts"][activity_alias]
            state["target_experience_alias"] = activity_alias
            state["activity_tree_text"] = context["tree_text"]
            state["alias_to_block_id"] = context["alias_to_block_id"]
            state["alias_metadata"] = context["alias_metadata"]
    return state


def _print_map(display: dict) -> None:
    print(f"\n=== 경험 맵 (map_version={display['map_version']}) ===")
    for activity in display["activities"]:
        print(f"\n[{activity['id']}] {activity['title']}")
        print(activity["tree"])


async def _run_turn(runner: TestUiGraphRunner, state: ExperienceMapState) -> list[dict]:
    events = []
    async for event in runner.run(state):
        payload = event.model_dump(mode="json")
        events.append(payload)
        print(f"\n[{payload.get('type')}]")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return events


async def scenario_gap() -> None:
    """멀티턴: 1턴에서 gap 제안을 받고, 2턴에서 실제로 답한다."""
    print("\n" + "=" * 70 + "\n시나리오: 멀티턴 gap 답변\n" + "=" * 70)
    store = InMemoryTestMapStore(initial_rows_factory=_initial_rows)
    await store.reset(USER_ID)
    runner = TestUiGraphRunner(store)

    print("\n--- 턴 1: 문제해결 경험 입력 ---")
    state1 = await _build_state(
        store,
        user_message=(
            "행사 신청 페이지에서 결제 오류가 반복돼 GA4로 이탈 구간을 분석했다. "
            "결제 승인 API 타임아웃이 원인이었고, 재시도 로직을 추가해 해결했다."
        ),
        context_experience_id=ACTIVITY_BLOCK_ID,
    )
    events1 = await _run_turn(runner, state1)
    gap_events = [e for e in events1 if e.get("type") == "suggestion_ready"]
    if not gap_events:
        print("\n(턴 1에서 gap 제안이 안 나왔습니다 — 이번 실행은 여기서 끝냅니다.)")
        _print_map(await store.display_map(USER_ID))
        return

    gap = gap_events[0]["gap"]
    print(f"\n--- 받은 gap: {gap['message']} ---")

    print("\n--- 턴 2: gap에 실제로 답변 ---")
    state2 = await _build_state(
        store,
        user_message="타임아웃 임계값을 3초로 늘리고, 실패 시 최대 2회까지 지수 백오프로 재시도하도록 구현했다.",
        context_experience_id=ACTIVITY_BLOCK_ID,
        active_gap={
            "gap_id": gap["gap_id"],
            "gap_type": gap["gap_type"],
            "anchor_block_id": gap["anchor_block_id"],
            "message": gap["message"],
            "created_request_id": state1["request_id"],
        },
    )
    await _run_turn(runner, state2)
    _print_map(await store.display_map(USER_ID))


async def scenario_file() -> None:
    """진짜 GCS 없이 InMemoryObjectStore로 파일 업로드 경로를 실행한다."""
    print("\n" + "=" * 70 + "\n시나리오: 파일 업로드 (.txt)\n" + "=" * 70)
    store = InMemoryTestMapStore(initial_rows_factory=_initial_rows)
    await store.reset(USER_ID)
    upload_store = UploadStore(InMemoryObjectStore(), file_ttl_seconds=3600)
    set_upload_store(upload_store)
    runner = TestUiGraphRunner(store)

    content = (
        "담당 업무\n"
        "사내 커머스 리뉴얼 프로젝트에서 프론트엔드 개발을 담당했다.\n"
        "문제 해결\n"
        "장바구니 페이지 로딩 속도가 5초를 넘어 이탈률이 높았다. "
        "번들 크기를 분석해 불필요한 라이브러리를 제거하고 코드 스플리팅을 적용해 "
        "로딩 속도를 1.2초로 줄였다.\n"
    ).encode("utf-8")

    request_id = str(uuid.uuid4())
    stored = await upload_store.store_files(
        USER_ID, request_id, [("경력기술서.txt", "text/plain", _BytesReader(content))]
    )
    print(f"\n--- 업로드한 파일: {stored[0].filename} ({stored[0].file_size} bytes) ---")

    state = await _build_state(
        store,
        user_message=None,
        context_experience_id=ACTIVITY_BLOCK_ID,
        file_references=[f.as_reference() for f in stored],
    )
    state["request_id"] = request_id
    state["request_hash"] = hashlib.sha256(
        json.dumps({"file": stored[0].sha256}, sort_keys=True).encode()
    ).hexdigest()

    await _run_turn(runner, state)
    _print_map(await store.display_map(USER_ID))


async def scenario_retry() -> None:
    """validate를 처음 한 번 강제로 실패시켜 structure/refine 재시도 경로를 켠다.

    검증 로직 자체는 실제 코드를 그대로 쓰되, 첫 validate 호출 결과에
    인위적으로 refine 대상 오류 하나를 끼워 넣는다 — 그 뒤 실제 LLM으로
    도는 refine 재시도(내가 방금 좁혀 넣은 부분 재사용 최적화 포함)가 실제로
    작동하는지 확인한다.
    """
    print("\n" + "=" * 70 + "\n시나리오: validate 강제 실패 → structure/refine 재시도\n" + "=" * 70)
    store = InMemoryTestMapStore(initial_rows_factory=_initial_rows)
    await store.reset(USER_ID)
    runner = TestUiGraphRunner(store)

    call_count = {"n": 0}
    real_validate = validate_node.validate_operations  # graph.py가 바인딩한 것과 같은 함수 객체

    def _fake_first_call_forces_refine_repair(state: ExperienceMapState) -> ExperienceMapState:
        call_count["n"] += 1
        if call_count["n"] == 1:
            structured = state.get("structured_items") or []
            if not structured:
                return real_validate(state)
            target_id = structured[0]["item_id"]
            print(
                f"\n>>> [강제 주입] 1번째 validate 호출을 인위적으로 실패시킵니다 "
                f"(item_id={target_id!r}, repair_target=refine)"
            )
            updated = dict(state)
            updated["current_node"] = "validate"
            updated["validation_errors"] = [
                {
                    "item_id": target_id,
                    "code": "content_too_long",
                    "message": "(테스트로 강제 주입한 오류) 내용이 최대 글자 수를 넘었습니다.",
                    "repair_target": "refine",
                }
            ]
            updated["repair_count"] = state.get("repair_count", 0) + 1
            return updated  # type: ignore[return-value]
        print(f"\n>>> [강제 주입] {call_count['n']}번째 validate 호출은 실제 로직 그대로 둡니다.")
        return real_validate(state)

    state = await _build_state(
        store,
        user_message=(
            "이벤트 페이지 접속량이 몰릴 때 500 에러가 자주 발생했다. "
            "APM으로 확인해보니 DB 커넥션 풀이 고갈되고 있었다. "
            "커넥션 풀 크기를 늘리고 슬로우 쿼리에 인덱스를 추가해 에러율을 0.5%로 낮췄다."
        ),
        context_experience_id=ACTIVITY_BLOCK_ID,
    )

    # graph.py는 `from ...validate import validate_operations`로 이름을 미리
    # 바인딩해 뒀으므로, validate_node(원본 모듈)가 아니라 graph_module 쪽
    # 이름을 바꿔 끼워야 그래프 실행에 실제로 반영된다.
    with patch.object(
        graph_module, "validate_operations", _fake_first_call_forces_refine_repair
    ):
        await _run_turn(runner, state)

    print(f"\n--- validate 호출 횟수: {call_count['n']} (1번째는 강제 실패, 이후 정상) ---")
    _print_map(await store.display_map(USER_ID))


SCENARIOS = {"gap": scenario_gap, "file": scenario_file, "retry": scenario_retry}


async def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {*SCENARIOS, "all"}:
        print(f"사용법: {sys.argv[0]} {{{'|'.join([*SCENARIOS, 'all'])}}}")
        return 1
    names = list(SCENARIOS) if sys.argv[1] == "all" else [sys.argv[1]]
    for name in names:
        await SCENARIOS[name]()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
