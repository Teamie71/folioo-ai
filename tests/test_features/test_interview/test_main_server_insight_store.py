"""MainServerInsightStore 단위 테스트"""

from unittest.mock import AsyncMock

import pytest

from features.interview.agents.insight_store import InsightStore, MainServerInsightStore


class TestMainServerInsightStoreProtocol:
    """프로토콜 준수 테스트"""

    def test_implements_insight_store_protocol(self):
        """MainServerInsightStore가 InsightStore 프로토콜을 구현하는지 확인"""
        store = MainServerInsightStore()
        assert isinstance(store, InsightStore)


class TestSearchSimilar:
    """search_similar 메서드 테스트"""

    @pytest.mark.asyncio
    async def test_delegates_to_insight_client(self):
        """InsightClient.search_similar에 올바른 파라미터를 전달"""
        expected = [
            {
                "id": "1",
                "title": "인사이트",
                "content": "내용",
                "activity_name": "활동",
                "category": "문제해결",
                "similarity_score": 0.9,
            }
        ]

        mock_client = AsyncMock()
        mock_client.search_similar.return_value = expected

        store = MainServerInsightStore(client=mock_client)
        result = await store.search_similar(
            query="백엔드 개발", user_id="42", top_k=5, threshold=0.7
        )

        assert result == expected
        mock_client.search_similar.assert_called_once_with(
            keyword="백엔드 개발", user_id=42, top_k=5, threshold=0.7
        )

    @pytest.mark.asyncio
    async def test_converts_user_id_str_to_int(self):
        """user_id 문자열을 int로 변환"""
        mock_client = AsyncMock()
        mock_client.search_similar.return_value = []

        store = MainServerInsightStore(client=mock_client)
        await store.search_similar(query="test", user_id="123")

        mock_client.search_similar.assert_called_once()
        call_kwargs = mock_client.search_similar.call_args
        assert call_kwargs.kwargs["user_id"] == 123

    @pytest.mark.asyncio
    async def test_invalid_user_id_returns_empty_list(self):
        """int로 변환 불가능한 user_id는 빈 리스트 반환"""
        mock_client = AsyncMock()
        store = MainServerInsightStore(client=mock_client)

        result = await store.search_similar(query="test", user_id="not-a-number")

        assert result == []
        mock_client.search_similar.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_default_parameters(self):
        """기본 top_k, threshold 파라미터 전달"""
        mock_client = AsyncMock()
        mock_client.search_similar.return_value = []

        store = MainServerInsightStore(client=mock_client)
        await store.search_similar(query="test", user_id="1")

        call_kwargs = mock_client.search_similar.call_args
        assert call_kwargs.kwargs["top_k"] == 5
        assert call_kwargs.kwargs["threshold"] == 0.7


class TestGetById:
    """get_by_id 메서드 테스트"""

    @pytest.mark.asyncio
    async def test_delegates_to_insight_client(self):
        """InsightClient.get_by_id에 올바른 파라미터를 전달"""
        expected = {
            "id": "42",
            "title": "인사이트",
            "content": "내용",
            "activity_name": "활동",
            "category": "대인관계",
            "similarity_score": None,
        }

        mock_client = AsyncMock()
        mock_client.get_by_id.return_value = expected

        store = MainServerInsightStore(client=mock_client)
        result = await store.get_by_id("42")

        assert result == expected
        mock_client.get_by_id.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_converts_insight_id_str_to_int(self):
        """insight_id 문자열을 int로 변환"""
        mock_client = AsyncMock()
        mock_client.get_by_id.return_value = None

        store = MainServerInsightStore(client=mock_client)
        await store.get_by_id("99")

        mock_client.get_by_id.assert_called_once_with(99)

    @pytest.mark.asyncio
    async def test_invalid_insight_id_returns_none(self):
        """int로 변환 불가능한 insight_id는 None 반환"""
        mock_client = AsyncMock()
        store = MainServerInsightStore(client=mock_client)

        result = await store.get_by_id("abc")

        assert result is None
        mock_client.get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_for_not_found(self):
        """존재하지 않는 인사이트는 None 반환"""
        mock_client = AsyncMock()
        mock_client.get_by_id.return_value = None

        store = MainServerInsightStore(client=mock_client)
        result = await store.get_by_id("999")

        assert result is None
