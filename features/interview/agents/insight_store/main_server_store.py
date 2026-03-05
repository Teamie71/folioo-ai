"""메인 서버 API 기반 InsightStore 구현"""

import logging

from common.main_server import InsightClient
from features.interview.agents.state import InsightLog

logger = logging.getLogger(__name__)


class MainServerInsightStore:
    """
    메인 서버 API를 통한 InsightStore 구현

    InsightClient를 래핑하여 InsightStore 프로토콜을 준수합니다.
    프로토콜의 user_id(str) -> InsightClient의 user_id(int) 변환을 처리합니다.
    """

    def __init__(self, client: InsightClient | None = None) -> None:
        self._client = client or InsightClient()

    async def search_similar(
        self,
        query: str,
        user_id: str,
        top_k: int = 5,
        threshold: float = 0.7,
    ) -> list[InsightLog]:
        """
        텍스트 기반 인사이트 로그 검색

        Args:
            query: 검색 텍스트
            user_id: 사용자 ID (문자열, 내부에서 int로 변환)
            top_k: 최대 반환 결과 수
            threshold: 유사도 임계값

        Returns:
            유사 인사이트 목록 (유사도 내림차순)
        """
        try:
            numeric_user_id = int(user_id)
        except (ValueError, TypeError):
            logger.warning("user_id를 int로 변환할 수 없습니다: %s", user_id)
            return []

        return await self._client.search_similar(
            keyword=query,
            user_id=numeric_user_id,
            top_k=top_k,
            threshold=threshold,
        )

    async def get_by_id(self, insight_id: str) -> InsightLog | None:
        """
        인사이트 단건 조회

        Args:
            insight_id: 인사이트 로그 ID (문자열, 내부에서 int로 변환)

        Returns:
            인사이트 로그 데이터, 없으면 None
        """
        try:
            numeric_id = int(insight_id)
        except (ValueError, TypeError):
            logger.warning("insight_id를 int로 변환할 수 없습니다: %s", insight_id)
            return None

        return await self._client.get_by_id(numeric_id)
