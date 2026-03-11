"""포트폴리오 메인 서버 API 클라이언트"""

from typing import Any

from common.http_client import MainServerError, request_with_retry

_ALLOWED_UPDATE_STATUSES = ("completed", "failed")

FIELD_MAP_SERVER_TO_AI = {
    "sessionId": "session_id",
    "userId": "user_id",
    "experienceName": "experience_name",
    "contributionRate": "contribution_rate",
    "errorMessage": "error_message",
    "responsibilities": "contributions",
    "problemSolving": "achievements",
    "learnings": "insights",
}


class PortfolioClient:
    """포트폴리오 메인 서버 API 클라이언트"""

    async def get_portfolio(self, portfolio_id: int) -> dict[str, Any]:
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
        try:
            result = await request_with_retry(
                "GET",
                f"/internal/portfolios/{portfolio_id}",
            )
        except MainServerError as e:
            if e.status_code == 404:
                return {}
            raise

        if not isinstance(result, dict):
            return {}

        mapped: dict[str, Any] = {}
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
        if status not in _ALLOWED_UPDATE_STATUSES:
            raise ValueError(
                f"허용되지 않는 status입니다: {status!r} (허용: {_ALLOWED_UPDATE_STATUSES})"
            )

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
