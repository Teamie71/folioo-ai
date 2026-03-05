"""포트폴리오 메인 서버 API 클라이언트"""

import logging

from common.http_client import request_with_retry

logger = logging.getLogger(__name__)

FIELD_MAP_SERVER_TO_AI = {
    "responsibilities": "contributions",
    "problemSolving": "achievements",
    "learnings": "insights",
}


class PortfolioClient:
    """포트폴리오 메인 서버 API 클라이언트"""

    async def get_portfolio(self, portfolio_id: int) -> dict:
        """
        포트폴리오 단건 조회

        Args:
            portfolio_id: 포트폴리오 ID

        Returns:
            필드 매핑 적용된 포트폴리오 딕셔너리
            (responsibilities->contributions, problemSolving->achievements, learnings->insights)

        Raises:
            MainServerError: API 호출 실패 시
        """
        result = await request_with_retry(
            "GET",
            f"/internal/portfolios/{portfolio_id}",
        )
        if not isinstance(result, dict):
            return {}

        mapped: dict = {}
        for key, value in result.items():
            mapped[FIELD_MAP_SERVER_TO_AI.get(key, key)] = value
        return mapped

    async def update_result(
        self,
        portfolio_id: int,
        status: str,
        *,
        description: str | None = None,
        contributions: str | None = None,
        achievements: str | None = None,
        insights: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        포트폴리오 처리 결과 업데이트

        Args:
            portfolio_id: 포트폴리오 ID
            status: 상태 ("completed" 또는 "failed")
            description: 포트폴리오 설명 (completed 시)
            contributions: 기여 내용 (completed 시, 서버 필드명: responsibilities)
            achievements: 성과 내용 (completed 시, 서버 필드명: problemSolving)
            insights: 인사이트 (completed 시, 서버 필드명: learnings)
            error_message: 에러 메시지 (failed 시)

        Raises:
            MainServerError: API 호출 실패 시
        """
        if status == "completed":
            payload = {
                "status": "completed",
                "description": description or "",
                "responsibilities": contributions or "",
                "problemSolving": achievements or "",
                "learnings": insights or "",
            }
        else:
            payload = {
                "status": "failed",
                "errorMessage": error_message or "",
            }

        await request_with_retry(
            "PATCH",
            f"/internal/portfolios/{portfolio_id}",
            json=payload,
        )
