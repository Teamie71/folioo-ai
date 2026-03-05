"""인사이트 메인 서버 API 클라이언트"""

import logging
from typing import Any

from common.http_client import MainServerError, request_with_retry
from features.interview.agents.state import InsightLog

logger = logging.getLogger(__name__)


def _to_insight_log(raw: dict[str, Any]) -> InsightLog:
    """메인 서버 응답 객체를 InsightLog로 변환"""
    activity_names = raw.get("activityNames") or []
    activity_name = ", ".join(str(a) for a in activity_names)

    return {
        "id": str(raw["id"]),
        "title": str(raw.get("title", "")),
        "activity_name": activity_name,
        "category": raw.get("category", "기타"),
        "content": str(raw.get("description", "")),
        "similarity_score": raw.get("similarityScore"),
    }


class InsightClient:
    """인사이트 로그 메인 서버 API 클라이언트"""

    async def search_similar(
        self,
        keyword: str,
        user_id: int,
        top_k: int = 10,
        threshold: float = 0.0,
    ) -> list[InsightLog]:
        """
        키워드 기반 유사 인사이트 검색

        Args:
            keyword: 검색 키워드
            user_id: 사용자 ID
            top_k: 반환할 최대 개수
            threshold: 유사도 임계값 (0.0 ~ 1.0)

        Returns:
            InsightLog 리스트
        """
        result = await request_with_retry(
            "GET",
            "/internal/insights/search",
            params={
                "userId": user_id,
                "keyword": keyword,
                "topK": top_k,
                "threshold": threshold,
            },
        )
        if not isinstance(result, list):
            return []
        return [_to_insight_log(item) for item in result]

    async def get_by_id(self, insight_id: int) -> InsightLog | None:
        """
        ID로 인사이트 조회

        Args:
            insight_id: 인사이트 ID

        Returns:
            InsightLog 또는 없을 경우 None
        """
        try:
            result = await request_with_retry(
                "GET",
                f"/internal/insights/{insight_id}",
            )
        except MainServerError as e:
            if e.status_code == 404:
                return None
            raise
        if result is None:
            return None
        return _to_insight_log(result)
