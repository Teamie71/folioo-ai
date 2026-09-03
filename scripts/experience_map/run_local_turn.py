#!/usr/bin/env python3
"""API·메인 서버·Postgres 없이 경험정리 에이전트를 실제 LLM으로 직접 실행한다.

`app/experience_map_test_ui.py`와 같은 in-memory 백엔드
(`InMemoryTestMapStore` + `TestUiGraphRunner`)를 쓰되, FastAPI·티켓 발급
과정을 완전히 건너뛰고 Python에서 그래프를 바로 호출한다. LangGraph
checkpointer도 `InMemorySaver`라 `DATABASE_URL`이 없어도 된다. 실제로
필요한 건 `.env`의 LLM 키(`OPENROUTER_API_KEY` 등)뿐이다.

사용법:
    uv run python scripts/experience_map/run_local_turn.py "결제 오류 원인을 분석하고 재시도 로직을 추가해 장애를 줄였다."
    uv run python scripts/experience_map/run_local_turn.py --activity 200 "GA4로 이탈 구간을 찾아 폼을 3단계로 줄였다."
    uv run python scripts/experience_map/run_local_turn.py --show-map   # 맵만 출력하고 끝
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.experience_map.service import compute_request_hash  # noqa: E402
from features.experience_map.state import ExperienceMapState, start_turn  # noqa: E402
from features.experience_map.templates import set_template_catalog_client  # noqa: E402
from features.experience_map.test_runtime import (  # noqa: E402
    InMemoryTestMapStore,
    TestUiGraphRunner,
    _initial_rows,
    create_test_template_catalog_client,
)

# structure 노드는 전역 카탈로그 클라이언트(get_template_catalog_client())를 쓰는데,
# 기본값은 메인 서버(MAIN_BACKEND_URL)를 실제로 호출한다. 로컬에는 메인 서버가
# 없으므로 앱이 EXPERIENCE_MAP_TEST_UI_ENABLED=true일 때 하는 것과 똑같이
# 테스트 전용 카탈로그로 바꿔 끼운다 (app/main.py 참고).
set_template_catalog_client(create_test_template_catalog_client())

USER_ID = "9000001"


async def _build_state(
    store: InMemoryTestMapStore,
    *,
    user_message: str | None,
    context_experience_id: str | None,
) -> ExperienceMapState:
    """service.py `_build_state`를 in-memory 맵 기준으로 재현한다."""
    request_id = str(uuid.uuid4())
    state = start_turn(
        {"user_id": USER_ID, "session_id": "local-session", "active_gap": None},
        request_id=request_id,
        request_hash=compute_request_hash(user_message, context_experience_id, None, []),
        user_message=user_message,
        context_experience_id=context_experience_id,
    )
    state["file_references"] = []
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


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("message", nargs="?", help="경험 사실 텍스트")
    parser.add_argument(
        "--activity",
        default="200",
        help="연결할 level 2 활동 block_id (기본 표본 맵의 '교내 커머스 리뉴얼' = 200)",
    )
    parser.add_argument("--show-map", action="store_true", help="맵만 출력하고 종료")
    args = parser.parse_args()

    store = InMemoryTestMapStore(initial_rows_factory=_initial_rows)
    await store.reset(USER_ID)

    if args.show_map or not args.message:
        _print_map(await store.display_map(USER_ID))
        if not args.message:
            print("\n(메시지 인자를 안 주면 맵만 보여주고 끝냅니다. 사용법은 --help 참고)")
        return 0

    print(f"입력: {args.message!r} (연결 활동 block_id={args.activity})")
    state = await _build_state(
        store, user_message=args.message, context_experience_id=args.activity
    )

    runner = TestUiGraphRunner(store)
    async for event in runner.run(state):
        payload = event.model_dump(mode="json")
        print(f"\n[{payload.get('type')}]")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    _print_map(await store.display_map(USER_ID))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
