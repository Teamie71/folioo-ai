"""경험정리 수동 테스트 UI 전용 in-memory 맵과 커밋 실행기.

메인 서버의 ``block`` DDL·커밋 API가 없는 로컬 환경에서만 실제 LLM 노드의
블록 수정 흐름을 점검한다. ``EXPERIENCE_MAP_TEST_UI_ENABLED``일 때에만 앱
lifespan에서 주입하며, 운영 Repository·메인 서버 쓰기를 대체하지 않는다.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.schemas.experience_map import CompletedMessage, MessageCompleteEvent
from features.experience_map.coordinator import coordinate
from features.experience_map.graph import build_graph
from features.experience_map.graph_runner import CheckpointGraphRunner, GraphRunner
from features.experience_map.map_context import (
    ExperienceMapSnapshot,
    MapBlockRow,
    build_map_snapshot,
)
from features.experience_map.nodes.fallback import fallback_message
from features.experience_map.schemas import AppliedItem, CommitResult, StructuredItem
from features.experience_map.state import ExperienceMapState
from features.experience_map.templates import TemplateCatalogClient


def _initial_rows() -> list[MapBlockRow]:
    """블록 수정 테스트에 쓰는 결정적 샘플 맵을 만든다."""

    def row(
        block_id: str,
        parent_id: str | None,
        level: int,
        position: int,
        content: str | None,
    ) -> MapBlockRow:
        return MapBlockRow(
            block_id=block_id,
            parent_id=parent_id,
            level=level,
            kind="CONTENT",
            position=position,
            content=content,
            placeholder=None,
            is_text_editable=True,
            is_deletable=False,
        )

    return [
        row("100", None, 1, 1, "프로젝트 경험"),
        row("200", "100", 2, 1, "교내 커머스 리뉴얼"),
        row("300", "200", 3, 1, "문제 해결"),
        row("301", "300", 4, 1, "행사 신청 페이지의 이탈률이 높았다."),
        row("302", "300", 4, 2, "GA4 퍼널 분석 후 입력 단계를 5개에서 3개로 줄였다."),
        row("400", "200", 3, 2, "성과"),
        row("401", "400", 4, 1, "신청 전환율과 완료율을 개선했다."),
    ]


async def _test_template_catalog() -> dict[str, Any]:
    """메인 서버 없이 구조화 노드를 실행할 최소 템플릿 카탈로그를 반환한다."""
    return {
        "version": "test-v1",
        "sections": [
            {
                "section_id": "DETAIL",
                "label": "상세정보",
                "slots": [
                    {
                        "slot_id": "DETAIL.MOTIVATION",
                        "level": 4,
                        "placeholder": "시작 계기",
                        "example": "문제를 해결하고 싶었습니다.",
                    }
                ],
                "templates": [],
            },
            {
                "section_id": "PROBLEM_SOLVING",
                "label": "문제해결",
                "slots": [
                    {
                        "slot_id": "PROBLEM_SOLVING.SUMMARY",
                        "level": 4,
                        "is_anchor": True,
                        "placeholder": "문제 요약",
                        "example": "가입 이탈 문제 해결",
                    }
                ],
                "templates": [],
            },
        ],
    }


def create_test_template_catalog_client() -> TemplateCatalogClient:
    """테스트 UI 전용 카탈로그 클라이언트를 생성한다."""
    return TemplateCatalogClient(_test_template_catalog)


@dataclass
class _UserMap:
    version: int
    rows: list[MapBlockRow]


class InMemoryTestMapStore:
    """사용자별 샘플 맵을 메모리에 유지한다."""

    def __init__(self) -> None:
        self._maps: dict[str, _UserMap] = {}
        self._lock = asyncio.Lock()

    async def snapshot(self, user_id: str) -> ExperienceMapSnapshot:
        """사용자 맵을 만들거나 현재 스냅샷을 반환한다."""
        async with self._lock:
            current = self._maps.setdefault(user_id, _UserMap(version=1, rows=_initial_rows()))
            return build_map_snapshot(list(current.rows), current.version)

    async def display_map(self, user_id: str) -> dict[str, Any]:
        """테스트 UI가 블록을 선택할 수 있는 안전한 표시 모델을 반환한다."""
        snapshot = await self.snapshot(user_id)
        activities = []
        for group in snapshot.outline():
            for activity in group["children"]:
                context = snapshot.get_activity_context(activity["alias"])
                if context is None:
                    continue
                activities.append(
                    {
                        "id": context.activity_id,
                        "title": activity["title"],
                        "tree": context.tree_text,
                    }
                )
        return {"map_version": snapshot.map_version, "activities": activities}

    async def commit(self, state: ExperienceMapState) -> ExperienceMapState:
        """검증된 operation을 샘플 맵에 반영하고 커밋 결과를 만든다."""
        user_id = str(state["user_id"])
        async with self._lock:
            current = self._maps.setdefault(user_id, _UserMap(version=1, rows=_initial_rows()))
            by_id = {row.block_id: row for row in current.rows}
            aliases = state.get("alias_to_block_id", {})
            previous_version = current.version
            applied: list[AppliedItem] = []

            for raw in state.get("commit_items", []):
                item = StructuredItem.model_validate(raw)
                if item.action == "update":
                    target_id = aliases.get(item.target_ref or "")
                    if target_id is None or target_id not in by_id:
                        raise ValueError("테스트 맵에서 수정 대상 블록을 찾을 수 없습니다.")
                    old = by_id[target_id]
                    replacement = MapBlockRow(
                        block_id=old.block_id,
                        parent_id=old.parent_id,
                        level=old.level,
                        kind=old.kind,
                        position=old.position,
                        content=item.text,
                        placeholder=old.placeholder,
                        is_text_editable=old.is_text_editable,
                        is_deletable=old.is_deletable,
                    )
                    current.rows[current.rows.index(old)] = replacement
                    by_id[target_id] = replacement
                    applied.append(
                        AppliedItem(
                            item_id=item.item_id, block_id=target_id, path=_path(by_id, target_id)
                        )
                    )
                    continue

                parent_id = aliases.get(item.parent_ref or "")
                if parent_id is None or parent_id not in by_id:
                    raise ValueError("테스트 맵에서 추가 대상 부모 블록을 찾을 수 없습니다.")
                parent = by_id[parent_id]
                new_id = str(max((int(key) for key in by_id if key.isdecimal()), default=999) + 1)
                position = (
                    max(
                        (row.position for row in current.rows if row.parent_id == parent_id),
                        default=0,
                    )
                    + 1
                )
                added = MapBlockRow(
                    block_id=new_id,
                    parent_id=parent_id,
                    level=parent.level + 1,
                    kind="CONTENT",
                    position=position,
                    content=item.text,
                    placeholder=None,
                    is_text_editable=True,
                    is_deletable=True,
                )
                current.rows.append(added)
                by_id[new_id] = added
                applied.append(
                    AppliedItem(item_id=item.item_id, block_id=new_id, path=_path(by_id, new_id))
                )

            current.version += 1
            result = CommitResult(
                request_id=str(state["request_id"]),
                previous_version=previous_version,
                map_version=current.version,
                revert_to_version=previous_version,
                can_revert=False,
                applied=applied,
                dropped=[],
            )
        return {**state, "commit_result": result.model_dump(mode="json")}


def _path(rows: dict[str, MapBlockRow], block_id: str) -> str:
    """결과 응답용 블록 경로를 생성한다."""
    labels: list[str] = []
    current = rows[block_id]
    while True:
        if current.content:
            labels.append(current.content)
        if current.parent_id is None:
            break
        current = rows[current.parent_id]
    labels.reverse()
    return " > ".join(labels[-3:])


class TestUiGraphRunner(GraphRunner):
    """실제 LLM graph와 메모리 커밋을 조합한 테스트 전용 실행기."""

    def __init__(self, store: InMemoryTestMapStore) -> None:
        self._store = store
        self._runner = CheckpointGraphRunner(
            build_graph(checkpointer=InMemorySaver()), state_events=self._state_events
        )

    async def run(self, state: ExperienceMapState):
        async for event in self._runner.run(state):
            yield event

    async def resume(self, state: ExperienceMapState):
        async for event in self._runner.resume(state):
            yield event

    async def _state_events(self, state: ExperienceMapState):
        if state.get("fallback_reason"):
            yield MessageCompleteEvent(
                message=CompletedMessage(
                    request_id=str(state["request_id"]),
                    session_id=str(state["session_id"]),
                    response_kind="fallback",
                    ai_response=fallback_message(state.get("fallback_reason")),
                    committed=False,
                )
            )
            return
        if state.get("commit_items"):
            async for event in coordinate(state, commit_runner=self._store.commit):
                yield event


_store = InMemoryTestMapStore()


def get_test_map_store() -> InMemoryTestMapStore:
    """테스트 UI 프로세스의 맵 저장소를 반환한다."""
    return _store


__all__ = [
    "InMemoryTestMapStore",
    "TestUiGraphRunner",
    "create_test_template_catalog_client",
    "get_test_map_store",
]
