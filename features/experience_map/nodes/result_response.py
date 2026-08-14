"""커밋 결과를 사용자용 완료 문구로 바꾸는 결정적 템플릿."""

from dataclasses import dataclass

from features.experience_map.schemas import CommitResult
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


def build_result_response(state: ExperienceMapState, result: CommitResult) -> str:
    """LLM 없이 커밋 결과에서 완료 문구를 만든다."""
    context = build_result_context(state, result)
    if len(context.categories) == 1:
        message = _single_category_message(context.experience_name, context.categories[0])
    else:
        message = _multiple_category_message(context.experience_name, context.categories)
    if context.dropped_count:
        message = f"{message}\n\n{context.dropped_count}개는 글자 수 제한(500자)을 넘어 넣지 못했어요. 나눠서 입력해 주세요."
    return message


def build_result_context(state: ExperienceMapState, result: CommitResult) -> ResultResponseContext:
    """커밋된 item의 path·action으로 결과 문구 변수를 계산한다."""
    action_by_item = {
        item.get("item_id"): item.get("action") for item in state.get("commit_items", [])
    }
    grouped: dict[str, CategorySummary] = {}
    experience_name = "경험"
    for applied in result.applied:
        activity, category = _path_parts(applied.path)
        experience_name = activity
        current = grouped.get(category, CategorySummary(label=category))
        if action_by_item.get(applied.item_id) == "update":
            grouped[category] = CategorySummary(
                category, current.added_count, current.updated_count + 1
            )
        else:
            grouped[category] = CategorySummary(
                category, current.added_count + 1, current.updated_count
            )
    return ResultResponseContext(
        experience_name=experience_name,
        categories=tuple(grouped.values()),
        dropped_count=len(result.dropped),
    )


def _path_parts(path: str) -> tuple[str, str]:
    """메인 서버가 돌려준 path에서 활동명과 최종 카테고리를 읽는다."""
    parts = [part.strip() for part in path.split(">") if part.strip()]
    if not parts:
        return "경험", "정리 항목"
    if len(parts) == 1:
        return parts[0], "정리 항목"
    return parts[0], parts[-1]


def _single_category_message(experience_name: str, category: CategorySummary) -> str:
    """단일 카테고리 또는 기존 블록 수정 전용 문구."""
    path = f"{experience_name} > {category.label}"
    if category.updated_count and not category.added_count:
        return f"{path} 내용을 보완했어요."
    return f"{path}에 {category.added_count}개를 정리했어요."


def _multiple_category_message(
    experience_name: str, categories: tuple[CategorySummary, ...]
) -> str:
    """여러 카테고리의 추가·수정 내역을 목록으로 만든다."""
    lines = [f"{experience_name}에 정리했어요."]
    for category in categories:
        if category.added_count:
            lines.append(f"- {category.label} — {category.added_count}개 추가")
        if category.updated_count:
            lines.append(f"- {category.label} — {category.updated_count}개 수정")
    return "\n".join(lines)


__all__ = [
    "CategorySummary",
    "ResultResponseContext",
    "build_result_context",
    "build_result_response",
]
