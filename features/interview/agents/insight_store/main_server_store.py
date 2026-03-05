"""메인 서버 API 기반 InsightStore 구현"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from common.main_server import InsightClient
    from features.interview.agents.state import InsightLog

logger = logging.getLogger(__name__)


def _to_numeric_identifier(value: str) -> int | None:
    """문자열 식별자를 숫자 ID로 변환 (예: "user-1" -> 1)"""
    try:
        parsed = int(value)
        if parsed >= 0:
            return parsed
        return None
    except (ValueError, TypeError):
        pass

    if not isinstance(value, str):
        return None

    matched_numbers = re.findall(r"\d+", value)
    if len(matched_numbers) != 1:
        return None

    try:
        return int(matched_numbers[0])
    except (ValueError, TypeError):
        return None


class MainServerInsightStore:
    """
    메인 서버 API를 통한 InsightStore 구현

    InsightClient를 래핑하여 InsightStore 프로토콜을 준수합니다.
    프로토콜의 user_id(str) -> InsightClient의 user_id(int) 변환을 처리합니다.
    """

    def __init__(self, client: InsightClient | None = None) -> None:
        if client is not None:
            self._client = client
        else:
            from common.main_server import InsightClient as _InsightClient

            self._client = _InsightClient()

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
        numeric_user_id = _to_numeric_identifier(user_id)
        if numeric_user_id is None:
            logger.warning("user_id를 int로 변환할 수 없습니다: %s", "<redacted>")
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
        numeric_id = _to_numeric_identifier(insight_id)
        if numeric_id is None:
            logger.warning("insight_id를 int로 변환할 수 없습니다: %s", "<redacted>")
            return None

        return await self._client.get_by_id(numeric_id)
