"""첨삭 서비스용 메인 서버 API 클라이언트"""

import logging

from .base_client import BaseClient

logger = logging.getLogger(__name__)

_client: "CorrectionClient | None" = None


class CorrectionClient(BaseClient):
    """
    메인 서버의 첨삭(Correction) API를 호출하는 클라이언트

    메인 서버 응답은 camelCase이므로, 이 클라이언트에서 snake_case로 변환하지 않고
    호출자(CorrectionService)가 필요한 필드를 직접 추출한다.
    """

    _PREFIX = "/corrections"

    async def get_correction(self, correction_id: int) -> dict:
        """
        첨삭 데이터 조회

        Args:
            correction_id: 첨삭 ID (정수)

        Returns:
            메인 서버 응답 result dict (camelCase 키)
            예: {id, companyName, positionName, jobDescription, companyInsight,
                 highlightPoint, portfolioIds, status, ...}
        """
        response = await self.get(f"{self._PREFIX}/{correction_id}")
        return response["result"]

    async def update_status(self, correction_id: int, status: str) -> dict:
        """
        첨삭 상태 변경 (UPPER_CASE로 전송)

        Args:
            correction_id: 첨삭 ID
            status: 변경할 상태 (e.g. "DOING_RAG", "GENERATING", "FAILED")
        """
        return await self.patch(
            f"{self._PREFIX}/{correction_id}/status",
            json={"status": status},
        )

    async def save_rag_data(
        self,
        correction_id: int,
        search_query: str,
        search_results: list | dict,
    ) -> dict:
        """
        RAG 검색 결과 저장

        Args:
            correction_id: 첨삭 ID
            search_query: 검색 쿼리
            search_results: 검색 결과
        """
        return await self.post(
            f"{self._PREFIX}/{correction_id}/rag-data",
            json={
                "searchQuery": search_query,
                "searchResults": search_results,
            },
        )

    async def get_rag_data(self, correction_id: int) -> dict | None:
        """
        RAG 데이터 조회 (단일 객체 응답)

        Args:
            correction_id: 첨삭 ID

        Returns:
            RAG 데이터 dict 또는 None
        """
        response = await self.get(f"{self._PREFIX}/{correction_id}/rag-data")
        return response["result"] if response else None

    async def update_company_insight(self, correction_id: int, company_insight: str) -> dict:
        """
        기업 분석 저장 + 상태 원자적 전이 (-> COMPANY_INSIGHT)

        메인 서버가 company_insight 저장과 함께 상태를 COMPANY_INSIGHT로 전이한다.
        별도 update_status 호출이 불필요하다.

        Args:
            correction_id: 첨삭 ID
            company_insight: 기업 분석 텍스트
        """
        return await self.patch(
            f"{self._PREFIX}/{correction_id}/company-insight",
            json={"companyInsight": company_insight[:1500]},
        )

    async def update_result(
        self, correction_id: int, result: list[dict], overall_review: str
    ) -> dict:
        """
        첨삭 결과 저장 + 상태 원자적 전이 (-> DONE)

        메인 서버가 result 저장과 함께 상태를 DONE으로 전이한다.
        별도 update_status 호출이 불필요하다.

        Args:
            correction_id: 첨삭 ID
            result: 첨삭 결과 배열 (메인 서버 포맷)
            overall_review: 전체 첨삭 요약
        """
        return await self.patch(
            f"{self._PREFIX}/{correction_id}/result",
            json={"result": result, "overallReview": overall_review},
        )

    async def update_emphasis_points(self, correction_id: int, emphasis_points: str) -> dict:
        """
        강조 포인트 수정

        Args:
            correction_id: 첨삭 ID
            emphasis_points: 강조 포인트 텍스트
        """
        return await self.patch(
            f"{self._PREFIX}/{correction_id}/emphasis-points",
            json={"highlightPoint": emphasis_points},
        )

    async def delete_correction(self, correction_id: int) -> None:
        """
        첨삭 삭제

        Args:
            correction_id: 첨삭 ID
        """
        await self.delete(f"{self._PREFIX}/{correction_id}")


def get_correction_client() -> "CorrectionClient":
    """CorrectionClient 싱글톤 반환"""
    if _client is None:
        raise RuntimeError(
            "CorrectionClient가 초기화되지 않았습니다. init_correction_client()를 먼저 호출하세요."
        )
    return _client


def init_correction_client(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> "CorrectionClient":
    """CorrectionClient 싱글톤 초기화"""
    global _client
    _client = CorrectionClient(base_url=base_url, api_key=api_key, timeout=timeout)
    return _client


def reset_correction_client() -> None:
    """CorrectionClient 싱글톤 리셋 (테스트용)"""
    global _client
    _client = None


__all__ = [
    "CorrectionClient",
    "get_correction_client",
    "init_correction_client",
    "reset_correction_client",
]
