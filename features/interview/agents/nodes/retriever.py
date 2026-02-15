"""Retriever 노드 - 인사이트 로그 벡터 검색"""

import logging
import os

from ..insight_store import InsightStore
from ..state import InsightLog, InterviewState

logger = logging.getLogger(__name__)

# 환경 변수에서 검색 설정 로드
_DEFAULT_TOP_K = int(os.getenv("INSIGHT_SEARCH_TOP_K", "5"))
_DEFAULT_THRESHOLD = float(os.getenv("INSIGHT_SEARCH_THRESHOLD", "0.7"))

# 모듈 레벨 InsightStore 싱글톤
_store: InsightStore | None = None


def init_insight_store(store: InsightStore) -> None:
    """InsightStore 싱글톤 초기화 (lifespan 또는 테스트에서 호출)"""

    global _store
    _store = store


def get_insight_store() -> InsightStore:
    """초기화된 InsightStore 반환"""

    if _store is None:
        raise RuntimeError(
            "InsightStore가 초기화되지 않았습니다. init_insight_store()를 먼저 호출해주세요."
        )
    return _store


def _merge_and_deduplicate(*insight_lists: list[InsightLog]) -> list[InsightLog]:
    """
    여러 인사이트 목록을 병합하고 ID 기준으로 중복 제거

    동일 ID가 여러 소스에 있는 경우:
    - similarity_score가 있는 쪽을 우선
    - 둘 다 있으면 높은 점수를 우선
    (즉, 유사도 검색 결과와 언급된 로그가 겹칠 때 유사도 점수를 유지하기 위해)

    Args:
        *insight_lists: 병합할 인사이트 목록들

    Returns:
        중복 제거된 인사이트 목록
    """
    seen: dict[str, InsightLog] = {}

    for insights in insight_lists:
        for insight in insights:
            existing = seen.get(insight["id"])
            if existing is None:
                seen[insight["id"]] = insight
            else:
                existing_score = existing.get("similarity_score") or 0.0
                new_score = insight.get("similarity_score") or 0.0
                if new_score > existing_score:
                    seen[insight["id"]] = insight

    return list(seen.values())


async def run(state: InterviewState) -> InterviewState:
    """
    인사이트 로그 벡터 검색

    1단계: 유사도 검색
    2단계: @ 멘션 조회
    3단계: 병합 및 중복 제거

    Returns:
        dict: { "retrieved_insights": [...], "next_node": "analyst" }
    """

    try:
        store = get_insight_store()
    except RuntimeError:
        logger.warning("InsightStore가 초기화되지 않음. 인사이트 검색을 건너뜁니다.")
        return {
            **state,
            "retrieved_insights": [],
            "next_node": "analyst",
        }

    # 1. 유사도 검색
    similar_insights: list[InsightLog] = []
    last_message = state["messages"][-1].content

    try:
        similar_insights = await store.search_similar(
            query=last_message,
            user_id=state["user_id"],
            top_k=_DEFAULT_TOP_K,
            threshold=_DEFAULT_THRESHOLD,
        )
        logger.info(
            "🔎 유사 인사이트 %d건 검색됨 (user_id=%s, top_k=%d, threshold=%.2f)",
            len(similar_insights),
            state["user_id"],
            _DEFAULT_TOP_K,
            _DEFAULT_THRESHOLD,
        )
        # 각 인사이트의 유사도 수치 로그
        for insight in similar_insights:
            logger.info(
                "  📌 score=%.4f | id=%s | title='%s'",
                insight.get("similarity_score", 0.0),
                insight["id"],
                insight["title"][:40],
            )
    except Exception:
        logger.exception("인사이트 유사도 검색 중 오류 발생")

    # 2. @ 멘션 인사이트 강제 포함
    mentioned_insights: list[InsightLog] = []
    mentioned_ids = state.get("mentioned_insight_ids", [])

    for insight_id in mentioned_ids:
        try:
            insight = await store.get_by_id(insight_id)
            if insight is not None:
                mentioned_insights.append(insight)
                logger.info(f"멘션 인사이트 조회 성공: {insight_id}")
            else:
                logger.warning(f"멘션 인사이트를 찾을 수 없음: {insight_id}")
        except Exception:
            logger.exception(f"멘션 인사이트 조회 실패: {insight_id}")

    # 3. 병합 및 중복 제거 (해당 턴의 인사이트만 / 누적하지 않음)
    all_insights = _merge_and_deduplicate(similar_insights, mentioned_insights)

    logger.info(
        "✅ 인사이트 병합 완료: 유사=%d, 멘션=%d → 최종=%d",
        len(similar_insights),
        len(mentioned_insights),
        len(all_insights),
    )

    # 검색 후 Analyst 노드로 전환
    return {
        **state,
        "retrieved_insights": all_insights,
        "next_node": "analyst",
    }
