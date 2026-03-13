"""Retriever 노드 테스트"""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from features.interview.agents.nodes import retriever


def test_merge_and_deduplicate_prefers_search_source_with_higher_score():
    """동일 ID가 겹치면 더 높은 유사도와 search source를 유지한다."""
    merged = retriever._merge_and_deduplicate(
        [
            {
                "id": "insight-1",
                "title": "검색 결과",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "검색 내용",
                "similarity_score": 0.91,
                "source": "search",
            }
        ],
        [
            {
                "id": "insight-1",
                "title": "멘션 결과",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "멘션 내용",
                "similarity_score": None,
                "source": "mention",
            }
        ],
    )

    assert len(merged) == 1
    assert merged[0]["title"] == "검색 결과"
    assert merged[0]["source"] == "search"


def test_filter_search_results_applies_threshold_and_top_k():
    """threshold 이상 결과만 남기고 최대 3개로 제한한다."""
    filtered = retriever._filter_search_results(
        [
            {
                "id": "1",
                "title": "a",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "a",
                "similarity_score": 0.61,
                "source": "search",
            },
            {
                "id": "2",
                "title": "b",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "b",
                "similarity_score": 0.59,
                "source": "search",
            },
            {
                "id": "3",
                "title": "c",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "c",
                "similarity_score": 0.95,
                "source": "search",
            },
            {
                "id": "4",
                "title": "d",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "d",
                "similarity_score": 0.88,
                "source": "search",
            },
            {
                "id": "5",
                "title": "e",
                "activity_name": "활동",
                "category": "문제해결",
                "content": "e",
                "similarity_score": 0.75,
                "source": "search",
            },
        ],
        threshold=0.6,
        top_k=3,
    )

    assert [item["id"] for item in filtered] == ["3", "4", "5"]


@pytest.mark.asyncio
async def test_run_limits_search_results_and_merges_mentions(monkeypatch):
    """Retriever가 검색 결과를 3건으로 제한하고 멘션 결과를 병합한다."""
    mock_store = AsyncMock()
    mock_store.search_similar.return_value = [
        {
            "id": "search-1",
            "title": "s1",
            "activity_name": "활동1",
            "category": "문제해결",
            "content": "내용1",
            "similarity_score": 0.93,
        },
        {
            "id": "search-2",
            "title": "s2",
            "activity_name": "활동2",
            "category": "문제해결",
            "content": "내용2",
            "similarity_score": 0.88,
        },
        {
            "id": "mention-1",
            "title": "duplicate",
            "activity_name": "활동3",
            "category": "문제해결",
            "content": "내용3",
            "similarity_score": 0.81,
        },
        {
            "id": "search-4",
            "title": "below-threshold",
            "activity_name": "활동4",
            "category": "문제해결",
            "content": "내용4",
            "similarity_score": 0.4,
        },
    ]
    mock_store.get_by_id.return_value = {
        "id": "mention-1",
        "title": "mention",
        "activity_name": "활동3",
        "category": "문제해결",
        "content": "멘션 내용",
        "similarity_score": None,
    }

    monkeypatch.setattr(retriever, "_DEFAULT_TOP_K", 5)
    monkeypatch.setattr(retriever, "_DEFAULT_THRESHOLD", 0.6)
    monkeypatch.setattr(retriever, "get_insight_store", lambda: mock_store)

    result = await retriever.run(
        {
            "user_id": "user-1",
            "messages": [HumanMessage(content="문제 해결 경험")],
            "mentioned_insight_ids": ["mention-1"],
        }
    )

    retrieved = result["retrieved_insights"]
    assert [item["id"] for item in retrieved] == ["search-1", "search-2", "mention-1"]
    assert retrieved[0]["source"] == "search"
    assert retrieved[-1]["source"] == "search"
    assert result["next_node"] == "analyst"
    mock_store.search_similar.assert_awaited_once_with(
        query="문제 해결 경험",
        user_id="user-1",
        top_k=3,
        threshold=0.6,
    )
