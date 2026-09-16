"""커밋 결과를 사용자용 완료 문구로 바꾸는 결정적 템플릿 (에이전트 문서 3-8)."""

from dataclasses import dataclass, field

from features.experience_map.schemas import SECTION_LABELS, CommitResult
from features.experience_map.state import ExperienceMapState


@dataclass(frozen=True)
class CategorySummary:
    """카테고리별 추가·수정 개수."""

    label: str
    added_count: int = 0
    updated_count: int = 0


@dataclass(frozen=True)
class ResultResponseContext:
    """결과 템플릿이 쓰는 변수를 분리한 표현 모델."""

    experience_name: str
    categories: tuple[CategorySummary, ...]
    dropped_count: int
    new_categories: frozenset[str] = field(default_factory=frozenset)


def build_result_response(state: ExperienceMapState, result: CommitResult) -> str:
    """LLM 없이 커밋 결과에서 문서 3-8 템플릿 그대로 완료 문구를 만든다."""
    context = build_result_context(state, result)
    lines = ["내용을 분석하여 경험을 정리했어요."]
    for category in context.categories:
        if category.updated_count:
            lines.append(f"- {category.label} 아래 {category.updated_count}개의 블록 수정")
        if category.added_count:
            lines.append(f"- {category.label} 아래 {category.added_count}개의 블록 생성")
        if category.label in context.new_categories:
            lines.append(f"- {category.label} 생성")
    message = "\n".join(lines)
    if context.dropped_count:
        message = f"{message}\n\n{context.dropped_count}개는 글자 수 제한(500자)을 넘어 넣지 못했어요. 나눠서 입력해 주세요."
    if state.get("file_content_truncated"):
        # 페이지 수(MAX_PDF_PAGES)나 전체 글자 수 상한으로 파일 내용 일부를
        # 조용히 버렸을 수 있다 — 로그에만 남기지 않고 사용자에게도 알린다.
        message = (
            f"{message}\n\n첨부 파일 내용이 많아 일부만 반영됐어요. "
            "나머지가 중요하다면 나눠서 다시 올려 주세요."
        )
    return message


def build_result_context(state: ExperienceMapState, result: CommitResult) -> ResultResponseContext:
    """커밋된 item의 path·action으로 결과 문구 변수를 계산한다.

    새로 만든 3단계 카테고리 컨테이너는 내용이 없어(명세 2-4-3) `path`에 자기
    라벨이 실리지 않는다 — 컨테이너 자신도 `applied`에는 있지만 path는 그
    부모(활동)까지만 담는다. 그래서 카테고리가 이번에 새로 만들어졌는지는
    `path` 대신 `commit_items`의 `section_kind`로 판단하고, 그 라벨은
    `SECTION_LABELS`에서 가져온다.
    """
    commit_items = state.get("commit_items", [])
    items_by_id = {item.get("item_id"): item for item in commit_items}
    action_by_item = {item_id: item.get("action") for item_id, item in items_by_id.items()}
    new_container_labels = {
        item_id: SECTION_LABELS[item["section_kind"]]
        for item_id, item in items_by_id.items()
        if item.get("section_kind")
    }

    def new_category_label(item_id: str | None) -> str | None:
        """item_id의 부모 체인을 따라가 이번에 새로 만든 카테고리 컨테이너를 찾는다."""
        seen: set[str] = set()
        current_id = item_id
        while current_id is not None and current_id in items_by_id and current_id not in seen:
            seen.add(current_id)
            if current_id in new_container_labels:
                return new_container_labels[current_id]
            current_id = items_by_id[current_id].get("parent_item_id")
        return None

    grouped: dict[str, CategorySummary] = {}
    new_categories: set[str] = set()
    experience_name = "경험"
    for applied in result.applied:
        activity, path_category = _path_parts(applied.path)
        experience_name = activity

        if applied.item_id in new_container_labels:
            # 컨테이너 자신은 블록 수·생성 수에 세지 않는다 — "카테고리 생성" 표시만 한다.
            label = new_container_labels[applied.item_id]
            grouped.setdefault(label, CategorySummary(label=label))
            new_categories.add(label)
            continue

        label = new_category_label(applied.item_id) or path_category
        if label in new_container_labels.values():
            new_categories.add(label)

        current = grouped.get(label, CategorySummary(label=label))
        if action_by_item.get(applied.item_id) == "update":
            grouped[label] = CategorySummary(label, current.added_count, current.updated_count + 1)
        else:
            grouped[label] = CategorySummary(label, current.added_count + 1, current.updated_count)

    return ResultResponseContext(
        experience_name=experience_name,
        categories=tuple(grouped.values()),
        dropped_count=len(result.dropped),
        new_categories=frozenset(new_categories),
    )


def _path_parts(path: str) -> tuple[str, str]:
    """메인 서버가 돌려준 path에서 활동명과 3단계 카테고리를 읽는다.

    서버 path는 현재 블록을 제외한 부모 체인이므로 level 5 블록이면
    `활동 > 카테고리 > 앵커`가 된다. 마지막 조각을 쓰면 앵커 문구로 잘못
    그룹화되므로 항상 활동 바로 아래 조각을 카테고리로 사용한다.
    """
    parts = [part.strip() for part in path.split(">") if part.strip()]
    if not parts:
        return "경험", "정리 항목"
    if len(parts) == 1:
        return parts[0], "정리 항목"
    return parts[0], parts[1]


__all__ = [
    "CategorySummary",
    "ResultResponseContext",
    "build_result_context",
    "build_result_response",
]
