"""경험 맵 조회 결과를 LLM 안전 컨텍스트로 바꾼다.

DB 조회는 메인 서버 DDL이 확정된 뒤 Repository에 붙인다. 이 모듈은 그 조회 결과의
순수 변환만 담당한다. 실제 block ID는 절대 렌더링하거나 LLM에 전달하지 않는다.
"""

from dataclasses import dataclass, field

from features.experience_map.state import AliasBlockMetadata


@dataclass(frozen=True)
class MapBlockRow:
    """경험 맵 조회 SQL 한 행의 DDL 비의존 표현."""

    block_id: str
    parent_id: str | None
    level: int
    kind: str
    position: int
    content: str | None
    placeholder: str | None
    is_text_editable: bool
    is_deletable: bool


@dataclass
class MapBlock:
    """정렬된 경험 맵 트리의 한 블록."""

    row: MapBlockRow
    children: list["MapBlock"] = field(default_factory=list)


@dataclass(frozen=True)
class ActivityContext:
    """한 활동에만 한정한 LLM 컨텍스트와 alias 화이트리스트."""

    activity_alias: str
    activity_id: str
    tree_text: str
    alias_to_block_id: dict[str, str]
    alias_metadata: dict[str, AliasBlockMetadata]

    def resolve_alias(self, alias: str) -> str | None:
        """현재 활동 컨텍스트에 있는 alias만 실제 ID로 역변환한다."""
        return self.alias_to_block_id.get(alias)


class ExperienceMapSnapshot:
    """전체 outline과 활동별 상세 컨텍스트를 가진 읽기 전용 스냅샷."""

    def __init__(self, roots: list[MapBlock], map_version: int) -> None:
        self._roots = roots
        self.map_version = map_version
        self._activities = [
            block for root in roots for block in root.children if block.row.level == 2
        ]
        self._activity_by_alias = {
            _activity_alias(index): activity
            for index, activity in enumerate(self._activities, start=1)
        }
        self._block_id_to_activity_alias = {
            node.row.block_id: alias
            for alias, activity in self._activity_by_alias.items()
            for node in _walk(activity)
        }

    def outline(self) -> list[dict]:
        """그룹·활동만 담은 전체 outline을 반환한다."""
        return [
            {
                "level": root.row.level,
                "title": _label(root),
                "children": [
                    {
                        "alias": alias,
                        "level": activity.row.level,
                        "title": _label(activity),
                    }
                    for alias, activity in self._activity_by_alias.items()
                    if activity in root.children
                ],
            }
            for root in self._roots
        ]

    def get_activity_context(self, activity_alias: str) -> ActivityContext | None:
        """선택한 활동의 전체 트리와 범위 제한된 alias map을 반환한다."""
        activity = self._activity_by_alias.get(activity_alias)
        if activity is None:
            return None

        alias_to_block_id = {activity_alias: activity.row.block_id}
        alias_metadata: dict[str, AliasBlockMetadata] = {}
        next_block_alias = 1

        def render(node: MapBlock, depth: int, alias: str, parent_alias: str | None) -> list[str]:
            nonlocal next_block_alias
            alias_metadata[alias] = {
                "block_id": node.row.block_id,
                "parent_alias": parent_alias,
                "level": node.row.level,
                "kind": node.row.kind,
                "is_text_editable": node.row.is_text_editable,
            }
            lines = [f"{'  ' * depth}[{alias}] {_label(node)}"]
            for child in node.children:
                child_alias = _block_alias(next_block_alias)
                next_block_alias += 1
                alias_to_block_id[child_alias] = child.row.block_id
                lines.extend(render(child, depth + 1, child_alias, alias))
            return lines

        return ActivityContext(
            activity_alias=activity_alias,
            activity_id=activity.row.block_id,
            tree_text="\n".join(render(activity, 0, activity_alias, None)),
            alias_to_block_id=alias_to_block_id,
            alias_metadata=alias_metadata,
        )

    def block_id_to_activity_alias(self) -> dict[str, str]:
        """각 활동 하위 block의 소유 활동 별칭을 복사본으로 반환한다.

        gap의 ``anchor_block_id``를 선택 활동으로 안전하게 되돌릴 때만 쓴다.
        이 매핑은 LLM 프롬프트에 전달하지 않는다.
        """
        return dict(self._block_id_to_activity_alias)

    def block_contents(self) -> dict[str, str]:
        """내용이 있는 block의 실제 ID→원문 매핑을 복사해 반환한다."""
        return {
            node.row.block_id: node.row.content
            for root in self._roots
            for node in _walk(root)
            if node.row.content is not None
        }


def build_map_snapshot(rows: list[MapBlockRow], map_version: int) -> ExperienceMapSnapshot:
    """flat block 목록을 position 순서 트리로 만들고 활동 alias를 배정한다.

    잘못된 parent 참조나 level 관계는 실제 맵을 잘못된 활동으로 렌더링하게 하므로
    조용히 보정하지 않고 거부한다.
    """
    by_id: dict[str, MapBlock] = {}
    for row in rows:
        if row.block_id in by_id:
            raise ValueError(f"중복 block_id: {row.block_id}")
        if not 1 <= row.level <= 5:
            raise ValueError(f"지원하지 않는 block level: {row.level}")
        by_id[row.block_id] = MapBlock(row=row)

    roots: list[MapBlock] = []
    for node in by_id.values():
        parent_id = node.row.parent_id
        if parent_id is None:
            if node.row.level != 1:
                raise ValueError("level 1이 아닌 블록은 parent_id가 필요합니다.")
            roots.append(node)
            continue

        parent = by_id.get(parent_id)
        if parent is None:
            raise ValueError(f"부모 block을 찾을 수 없습니다: {parent_id}")
        if node.row.level != parent.row.level + 1:
            raise ValueError("부모와 자식 block level이 연속되지 않습니다.")
        parent.children.append(node)

    for node in by_id.values():
        node.children.sort(key=_sort_key)
    roots.sort(key=_sort_key)
    return ExperienceMapSnapshot(roots, map_version)


def _sort_key(node: MapBlock) -> tuple[int, int, int | str]:
    """position이 같을 때도 DB ID 기준으로 안정적인 순서를 만든다."""
    block_id = node.row.block_id
    if block_id.isdecimal():
        return node.row.position, 0, int(block_id)
    return node.row.position, 1, block_id


def _activity_alias(index: int) -> str:
    return f"exp_{index}"


def _block_alias(index: int) -> str:
    return f"b_{index}"


def _label(node: MapBlock) -> str:
    """사용자 작성 내용과 빈 블록 가이드를 절대 같은 값으로 취급하지 않는다."""
    content = (node.row.content or "").strip()
    if content:
        return content
    placeholder = (node.row.placeholder or "").strip()
    if placeholder:
        return f"(빈 블록 — 가이드: {placeholder})"
    return "(빈 블록)"


def _walk(root: MapBlock):
    """루트와 모든 하위 block을 깊이 우선으로 순회한다."""
    yield root
    for child in root.children:
        yield from _walk(child)


__all__ = [
    "ActivityContext",
    "ExperienceMapSnapshot",
    "MapBlock",
    "MapBlockRow",
    "build_map_snapshot",
]
