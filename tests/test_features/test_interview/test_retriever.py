"""Retriever 노드 테스트"""

import importlib
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from features.interview.agents.nodes import retriever


def test_env_defaults_fallback_on_invalid_values(monkeypatch):
    """잘못된 환경 변수 값이면 기본값으로 fallback한다."""
    monkeypatch.setenv("INSIGHT_SEARCH_TOP_K", "invalid")
    monkeypatch.setenv("INSIGHT_SEARCH_THRESHOLD", "bad")

    reloaded = importlib.reload(retriever)

    assert reloaded._DEFAULT_TOP_K == 3
    assert reloaded._DEFAULT_THRESHOLD == 0.6

    monkeypatch.delenv("INSIGHT_SEARCH_TOP_K", raising=False)
    monkeypatch.delenv("INSIGHT_SEARCH_THRESHOLD", raising=False)
    importlib.reload(retriever)


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
            "turn_number": 1,
            "user_id": "user-1",
            "messages": [HumanMessage(content="문제 해결 경험")],
            "mentioned_insight": "mention-1",
            "insight_turn_history": [],
        }
    )

    retrieved = result["retrieved_insights"]
    assert [item["id"] for item in retrieved] == ["search-1", "search-2", "mention-1"]
    assert retrieved[0]["source"] == "search"
    assert retrieved[-1]["source"] == "search"
    assert result["insight_turn_history"] == [
        {
            "turn_number": 1,
            "user_message": "문제 해결 경험",
            "mentioned_insight": "mention-1",
            "insights": retrieved,
        }
    ]
    assert result["next_node"] == "analyst"
    mock_store.search_similar.assert_awaited_once_with(
        query="문제 해결 경험",
        user_id="user-1",
        top_k=3,
        threshold=0.6,
    )


@pytest.mark.asyncio
async def test_run_appends_empty_history_when_store_is_unavailable(monkeypatch):
    """스토어가 없어도 현재 턴 복원 이력은 남긴다."""
    monkeypatch.setattr(
        retriever,
        "get_insight_store",
        lambda: (_ for _ in ()).throw(RuntimeError("store missing")),
    )

    result = await retriever.run(
        {
            "turn_number": 2,
            "user_id": "user-1",
            "messages": [HumanMessage(content="추가 설명입니다.")],
            "mentioned_insight": "mention-2",
            "insight_turn_history": [],
        }
    )

    assert result["retrieved_insights"] == []
    assert result["insight_turn_history"] == [
        {
            "turn_number": 2,
            "user_message": "추가 설명입니다.",
            "mentioned_insight": "mention-2",
            "insights": [],
        }
    ]


@pytest.mark.asyncio
async def test_run_skips_similarity_search_for_blank_message(monkeypatch):
    """blank 최신 사용자 메시지에 mention이 없으면 유사도 검색과 히스토리 append를 생략한다."""
    mock_store = AsyncMock()
    monkeypatch.setattr(retriever, "get_insight_store", lambda: mock_store)

    result = await retriever.run(
        {
            "turn_number": 3,
            "user_id": "user-1",
            "messages": [HumanMessage(content="   ")],
            "mentioned_insight": None,
            "insight_turn_history": [],
        }
    )

    assert result["retrieved_insights"] == []
    assert result["insight_turn_history"] == []
    assert result["next_node"] == "analyst"
    mock_store.search_similar.assert_not_awaited()
    mock_store.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_fetches_mentioned_insight_for_blank_message(monkeypatch):
    """blank 메시지여도 mentioned_insight가 있으면 explicit mention fetch를 수행한다."""
    mock_store = AsyncMock()
    mock_store.get_by_id.return_value = {
        "id": "mention-3",
        "title": "멘션 인사이트",
        "activity_name": "활동3",
        "category": "문제해결",
        "content": "멘션 내용",
        "similarity_score": None,
    }
    monkeypatch.setattr(retriever, "get_insight_store", lambda: mock_store)

    result = await retriever.run(
        {
            "turn_number": 4,
            "user_id": "user-1",
            "messages": [HumanMessage(content="   ")],
            "mentioned_insight": "mention-3",
            "insight_turn_history": [],
        }
    )

    assert result["retrieved_insights"] == [
        {
            "id": "mention-3",
            "title": "멘션 인사이트",
            "activity_name": "활동3",
            "category": "문제해결",
            "content": "멘션 내용",
            "similarity_score": None,
            "source": "mention",
        }
    ]
    assert result["insight_turn_history"] == [
        {
            "turn_number": 4,
            "user_message": "",
            "mentioned_insight": "mention-3",
            "insights": result["retrieved_insights"],
        }
    ]
    assert result["next_node"] == "analyst"
    mock_store.search_similar.assert_not_awaited()
    mock_store.get_by_id.assert_awaited_once_with("mention-3")


@pytest.mark.asyncio
async def test_run_does_not_append_history_for_blank_message_when_store_missing(monkeypatch):
    """스토어가 없어도 blank 메시지 턴은 bogus insight history를 남기지 않는다."""
    monkeypatch.setattr(
        retriever,
        "get_insight_store",
        lambda: (_ for _ in ()).throw(RuntimeError("store missing")),
    )

    result = await retriever.run(
        {
            "turn_number": 4,
            "user_id": "user-1",
            "messages": [HumanMessage(content="")],
            "mentioned_insight": None,
            "insight_turn_history": [],
        }
    )

    assert result["retrieved_insights"] == []
    assert result["insight_turn_history"] == []
    assert result["next_node"] == "analyst"


@pytest.mark.asyncio
async def test_run_appends_empty_history_for_blank_message_with_mention_when_store_missing(monkeypatch):
    """스토어가 없어도 blank+mention 턴은 빈 user_message로 복원 이력을 남긴다."""
    monkeypatch.setattr(
        retriever,
        "get_insight_store",
        lambda: (_ for _ in ()).throw(RuntimeError("store missing")),
    )

    result = await retriever.run(
        {
            "turn_number": 5,
            "user_id": "user-1",
            "messages": [HumanMessage(content="")],
            "mentioned_insight": "mention-5",
            "insight_turn_history": [],
        }
    )

    assert result["retrieved_insights"] == []
    assert result["insight_turn_history"] == [
        {
            "turn_number": 5,
            "user_message": "",
            "mentioned_insight": "mention-5",
            "insights": [],
        }
    ]
    assert result["next_node"] == "analyst"


def test_upsert_insight_turn_history_replaces_same_turn_record():
    """같은 turn_number 기록은 append 대신 교체한다."""
    history = [
        {
            "turn_number": 1,
            "user_message": "이전 답변",
            "mentioned_insight": None,
            "insights": [],
        },
        {
            "turn_number": 2,
            "user_message": "기존 답변",
            "mentioned_insight": "old-id",
            "insights": [
                {
                    "id": "old-id",
                    "title": "기존 인사이트",
                    "activity_name": "활동",
                    "category": "문제해결",
                    "content": "기존 내용",
                    "similarity_score": 0.8,
                    "source": "search",
                }
            ],
        },
    ]
    new_record = {
        "turn_number": 2,
        "user_message": "재처리 답변",
        "mentioned_insight": "new-id",
        "insights": [
            {
                "id": "new-id",
                "title": "새 인사이트",
                "activity_name": "새 활동",
                "category": "문제해결",
                "content": "새 내용",
                "similarity_score": 0.9,
                "source": "search",
            }
        ],
    }

    result = retriever._upsert_insight_turn_history(history, new_record)

    assert result == [history[0], new_record]
