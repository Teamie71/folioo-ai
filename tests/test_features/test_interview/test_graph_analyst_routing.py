"""Analyst 조건부 라우팅 그래프 테스트"""

import pytest

from features.interview.agents.graph import build_graph
from features.interview.agents.nodes import analyst, question_generator, router
from features.interview.agents.state import get_initial_interview_state


@pytest.mark.asyncio
async def test_graph_routes_to_end_when_analyst_sets_end(monkeypatch):
    """Analyst가 end를 반환하면 question_generator를 거치지 않고 종료한다."""

    def _router_run(state):
        return {**state, "next_node": "retriever"}

    async def _retriever_run(state):
        return {**state, "next_node": "analyst"}

    def _analyst_run(state):
        return {**state, "next_node": "end", "all_stages_complete": True}

    def _question_generator_run(state):
        raise AssertionError("analyst가 end를 반환한 경우 question_generator가 호출되면 안 됩니다.")

    monkeypatch.setattr(router, "run", _router_run)
    from features.interview.agents.nodes import retriever

    monkeypatch.setattr(retriever, "run", _retriever_run)
    monkeypatch.setattr(analyst, "run", _analyst_run)
    monkeypatch.setattr(question_generator, "run", _question_generator_run)

    graph = build_graph()
    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )

    result = await graph.ainvoke(state)

    assert result["all_stages_complete"] is True
