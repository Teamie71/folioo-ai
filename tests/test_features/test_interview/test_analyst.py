"""Analyst 노드 테스트"""

from langchain_core.runnables import RunnableLambda

from features.interview.agents.nodes import analyst
from features.interview.agents.prompts.analyst import AnalystFieldResult, AnalystResponse
from features.interview.agents.state import get_initial_interview_state
from features.interview.config.loader import load_stage_config


class _DummyLLM:
    def __init__(self, response: AnalystResponse):
        self._response = response

    def with_structured_output(self, _schema):
        return RunnableLambda(lambda _: self._response)


def _mock_analyst_llm(response: AnalystResponse):
    return _DummyLLM(response)


def test_run_keeps_stage_when_not_complete(monkeypatch):
    """단계가 완료되지 않으면 current_stage를 유지한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="프로젝트 배경",
                completeness=0.8,
                reasoning="충분히 설명됨",
            )
        ]
    )
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )

    result = analyst.run(state)

    assert result["current_stage"] == 1
    assert result["next_node"] == "question_generator"
    assert result["stage_progress"] == state["stage_progress"]
    assert result["collected_data"]["stage_1"]["project_background"]["value"] == "프로젝트 배경"


def test_run_moves_to_next_stage_when_complete(monkeypatch):
    """현재 단계가 완료되면 다음 단계로 전환한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="프로젝트 배경",
                completeness=0.8,
                reasoning="충분히 설명됨",
            )
        ]
    )
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["stage_progress"]["is_complete"] = True

    result = analyst.run(state)
    stage_2_config = load_stage_config(2)

    assert result["current_stage"] == 2
    assert result["next_node"] == "question_generator"
    assert result["stage_progress"]["fixed_q_used"] == 0
    assert result["stage_progress"]["fixed_q_total"] == len(stage_2_config.fixed_questions)
    assert result["stage_progress"]["generated_q_used"] == 0
    assert result["stage_progress"]["generated_q_max"] == stage_2_config.max_generated_questions
    assert result["stage_progress"]["force_all_generated_q"] == stage_2_config.force_all_generated_questions
    assert result["stage_progress"]["is_complete"] is False
    assert result["collected_data"]["stage_1"]["project_background"]["value"] == "프로젝트 배경"


def test_run_marks_all_complete_at_stage_4(monkeypatch):
    """4단계 완료 시 all_stages_complete와 overall_completion_percentage를 설정한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="portfolio_outcome",
                value="성과 설명",
                completeness=0.9,
                reasoning="충분히 설명됨",
            )
        ]
    )
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))
    monkeypatch.setattr(
        analyst,
        "_calculate_overall_completion_percentage",
        lambda experience_name, collected_data: (88.5, None),
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_stage"] = 4
    state["stage_progress"]["is_complete"] = True
    stage_4_config = load_stage_config(4)
    state["stage_progress"]["fixed_q_total"] = len(stage_4_config.fixed_questions)
    state["stage_progress"]["generated_q_max"] = stage_4_config.max_generated_questions
    state["stage_progress"]["force_all_generated_q"] = stage_4_config.force_all_generated_questions

    result = analyst.run(state)

    assert result["current_stage"] == 4
    assert result["all_stages_complete"] is True
    assert result["overall_completion_percentage"] == 88.5
    assert result["next_node"] == "end"
