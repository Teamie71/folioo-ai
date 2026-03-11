"""포트폴리오 클라이언트 필드 변환 테스트"""

from unittest.mock import AsyncMock, patch

import pytest

from common.http_client import MainServerError
from common.main_server.portfolio_client import FIELD_MAP_SERVER_TO_AI, PortfolioClient


class TestFieldMapping:
    """필드 매핑 상수 검증"""

    def test_server_to_ai_mapping(self):
        """서버 -> AI 서버 필드 매핑"""
        assert FIELD_MAP_SERVER_TO_AI == {
            "sessionId": "session_id",
            "userId": "user_id",
            "experienceName": "experience_name",
            "contributionRate": "contribution_rate",
            "errorMessage": "error_message",
            "responsibilities": "contributions",
            "problemSolving": "achievements",
            "learnings": "insights",
        }


class TestPortfolioClientGetPortfolio:
    """PortfolioClient.get_portfolio 테스트"""

    @pytest.mark.asyncio
    async def test_get_portfolio_field_mapping(self):
        """포트폴리오 조회 시 필드명 변환"""
        mock_result = {
            "id": 1,
            "sessionId": "session-1",
            "userId": 101,
            "experienceName": "프로젝트",
            "description": "프로젝트 설명",
            "responsibilities": "팀 리딩, API 개발",
            "problemSolving": "성능 최적화 50% 달성",
            "learnings": "아키텍처 설계의 중요성",
            "contributionRate": 70,
            "status": "completed",
        }

        client = PortfolioClient()
        with patch(
            "common.main_server.portfolio_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_req:
            result = await client.get_portfolio(1)

            assert result["contributions"] == "팀 리딩, API 개발"
            assert result["achievements"] == "성능 최적화 50% 달성"
            assert result["insights"] == "아키텍처 설계의 중요성"
            assert result["description"] == "프로젝트 설명"
            assert result["session_id"] == "session-1"
            assert result["user_id"] == 101
            assert result["experience_name"] == "프로젝트"
            assert result["contribution_rate"] == 70
            assert "responsibilities" not in result
            assert "problemSolving" not in result
            assert "learnings" not in result

            mock_req.assert_called_once_with("GET", "/internal/portfolios/1")

    @pytest.mark.asyncio
    async def test_get_portfolio_non_dict_result(self):
        """result가 dict가 아닌 경우 빈 딕셔너리 반환"""
        client = PortfolioClient()
        with patch(
            "common.main_server.portfolio_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await client.get_portfolio(1)
            assert result == {}

    @pytest.mark.asyncio
    async def test_get_portfolio_returns_empty_dict_for_404(self):
        """404 응답은 빈 딕셔너리로 처리한다."""
        client = PortfolioClient()
        with patch(
            "common.main_server.portfolio_client.request_with_retry",
            new_callable=AsyncMock,
            side_effect=MainServerError(status_code=404, message="Not Found"),
        ):
            result = await client.get_portfolio(1)
            assert result == {}


class TestPortfolioClientUpdateResult:
    """PortfolioClient.update_result 테스트"""

    @pytest.mark.asyncio
    async def test_update_completed(self):
        """성공 상태 업데이트 시 필드명 변환"""
        client = PortfolioClient()
        with patch(
            "common.main_server.portfolio_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_req:
            await client.update_result(
                portfolio_id=1,
                status="completed",
                description="설명",
                contributions="기여 내용",
                achievements="성과",
                insights="인사이트",
            )

            mock_req.assert_called_once_with(
                "PATCH",
                "/internal/portfolios/1",
                json={
                    "status": "completed",
                    "description": "설명",
                    "responsibilities": "기여 내용",
                    "problemSolving": "성과",
                    "learnings": "인사이트",
                },
            )

    @pytest.mark.asyncio
    async def test_update_failed(self):
        """실패 상태 업데이트"""
        client = PortfolioClient()
        with patch(
            "common.main_server.portfolio_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_req:
            await client.update_result(
                portfolio_id=2,
                status="failed",
                error_message="LLM 호출 실패",
            )

            mock_req.assert_called_once_with(
                "PATCH",
                "/internal/portfolios/2",
                json={
                    "status": "failed",
                    "errorMessage": "LLM 호출 실패",
                },
            )

    @pytest.mark.asyncio
    async def test_update_invalid_status_raises(self):
        """허용되지 않는 status 전달 시 ValueError 발생"""
        client = PortfolioClient()
        with pytest.raises(ValueError, match="허용되지 않는 status"):
            await client.update_result(portfolio_id=1, status="generating")
