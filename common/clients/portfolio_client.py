"""포트폴리오 서비스용 메인 서버 API 클라이언트"""

import logging

from .base_client import BaseClient

logger = logging.getLogger(__name__)

_client: "PortfolioClient | None" = None


class PortfolioClient(BaseClient):
    """
    메인 서버의 포트폴리오(Portfolio) API를 호출하는 클라이언트

    메인 서버 응답은 camelCase이므로, 호출자가 필요한 필드를 직접 추출한다.
    """

    _PREFIX = "/internal/portfolios"

    async def get_portfolio(self, portfolio_id: int) -> dict:
        """
        포트폴리오 데이터 조회

        Args:
            portfolio_id: 포트폴리오 ID (정수)

        Returns:
            메인 서버 응답 dict (camelCase 키)
            예: {id, description, responsibilities, problemSolving, learnings, ...}
        """
        response = await self.get(f"{self._PREFIX}/{portfolio_id}")
        return response["result"]


def get_portfolio_client() -> "PortfolioClient":
    """PortfolioClient 싱글톤 반환"""
    if _client is None:
        raise RuntimeError(
            "PortfolioClient가 초기화되지 않았습니다. init_portfolio_client()를 먼저 호출하세요."
        )
    return _client


def init_portfolio_client(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> "PortfolioClient":
    """PortfolioClient 싱글톤 초기화"""
    global _client
    _client = PortfolioClient(base_url=base_url, api_key=api_key, timeout=timeout)
    return _client


def reset_portfolio_client() -> None:
    """PortfolioClient 싱글톤 리셋 (테스트용)"""
    global _client
    _client = None


__all__ = [
    "PortfolioClient",
    "get_portfolio_client",
    "init_portfolio_client",
    "reset_portfolio_client",
]
