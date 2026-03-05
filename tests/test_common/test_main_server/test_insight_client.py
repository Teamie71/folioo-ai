"""인사이트 클라이언트 필드 변환 테스트"""

from unittest.mock import AsyncMock, patch

import pytest

from common.main_server.insight_client import InsightClient, _to_insight_log


class TestToInsightLog:
    """서버 응답 -> InsightLog 변환 테스트"""

    def test_basic_conversion(self):
        """기본 필드 변환 테스트"""
        raw = {
            "id": 42,
            "title": "팀 프로젝트 경험",
            "description": "백엔드 API 개발 담당",
            "activityNames": ["해커톤", "사이드 프로젝트"],
            "category": "문제해결",
            "similarityScore": 0.85,
        }
        result = _to_insight_log(raw)

        assert result["id"] == "42"
        assert result["title"] == "팀 프로젝트 경험"
        assert result["content"] == "백엔드 API 개발 담당"
        assert result["activity_name"] == "해커톤, 사이드 프로젝트"
        assert result["category"] == "문제해결"
        assert result["similarity_score"] == 0.85

    def test_empty_activity_names(self):
        """activityNames가 빈 배열인 경우"""
        raw = {
            "id": 1,
            "title": "테스트",
            "description": "내용",
            "activityNames": [],
            "category": "기타",
            "similarityScore": None,
        }
        result = _to_insight_log(raw)

        assert result["activity_name"] == ""
        assert result["similarity_score"] is None

    def test_missing_optional_fields(self):
        """선택 필드 누락 시 기본값"""
        raw = {"id": 1}
        result = _to_insight_log(raw)

        assert result["id"] == "1"
        assert result["title"] == ""
        assert result["content"] == ""
        assert result["activity_name"] == ""
        assert result["category"] == "기타"

    def test_single_activity_name(self):
        """activityNames가 1개인 경우"""
        raw = {
            "id": 5,
            "title": "제목",
            "description": "설명",
            "activityNames": ["인턴십"],
            "category": "학습",
            "similarityScore": 0.92,
        }
        result = _to_insight_log(raw)

        assert result["activity_name"] == "인턴십"


class TestInsightClientSearchSimilar:
    """InsightClient.search_similar 테스트"""

    @pytest.mark.asyncio
    async def test_search_returns_list(self):
        """정상 검색 결과 리스트 반환"""
        mock_result = [
            {
                "id": 1,
                "title": "인사이트1",
                "description": "내용1",
                "activityNames": ["활동A"],
                "category": "문제해결",
                "similarityScore": 0.9,
            },
            {
                "id": 2,
                "title": "인사이트2",
                "description": "내용2",
                "activityNames": ["활동B"],
                "category": "학습",
                "similarityScore": 0.8,
            },
        ]

        client = InsightClient()
        with patch(
            "common.main_server.insight_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_req:
            results = await client.search_similar(
                keyword="백엔드", user_id=1, top_k=5, threshold=0.7
            )

            assert len(results) == 2
            assert results[0]["content"] == "내용1"
            assert results[1]["activity_name"] == "활동B"

            mock_req.assert_called_once_with(
                "GET",
                "/internal/insights/search",
                params={"userId": 1, "keyword": "백엔드", "topK": 5, "threshold": 0.7},
            )

    @pytest.mark.asyncio
    async def test_search_empty_result(self):
        """결과 없을 시 빈 리스트"""
        client = InsightClient()
        with patch(
            "common.main_server.insight_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=[],
        ):
            results = await client.search_similar(keyword="없는키워드", user_id=1)
            assert results == []

    @pytest.mark.asyncio
    async def test_search_non_list_result(self):
        """result가 리스트가 아닌 경우 빈 리스트 반환"""
        client = InsightClient()
        with patch(
            "common.main_server.insight_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ):
            results = await client.search_similar(keyword="test", user_id=1)
            assert results == []


class TestInsightClientGetById:
    """InsightClient.get_by_id 테스트"""

    @pytest.mark.asyncio
    async def test_get_existing_insight(self):
        """존재하는 인사이트 조회"""
        mock_result = {
            "id": 42,
            "title": "테스트 인사이트",
            "description": "상세 내용",
            "activityNames": ["활동"],
            "category": "대인관계",
            "similarityScore": None,
        }

        client = InsightClient()
        with patch(
            "common.main_server.insight_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await client.get_by_id(42)

            assert result is not None
            assert result["id"] == "42"
            assert result["content"] == "상세 내용"

    @pytest.mark.asyncio
    async def test_get_not_found(self):
        """존재하지 않는 인사이트 조회 시 None"""
        from common.http_client import MainServerError

        client = InsightClient()
        with patch(
            "common.main_server.insight_client.request_with_retry",
            new_callable=AsyncMock,
            side_effect=MainServerError(status_code=404, message="Not Found"),
        ):
            result = await client.get_by_id(999)
            assert result is None

    @pytest.mark.asyncio
    async def test_get_null_result(self):
        """result가 None인 경우"""
        client = InsightClient()
        with patch(
            "common.main_server.insight_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await client.get_by_id(1)
            assert result is None
