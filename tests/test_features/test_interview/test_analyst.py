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


class _DummyGlobalConfig:
    def __init__(self, *, enable_dynamic_followup: bool = True):
        self.enable_dynamic_followup = enable_dynamic_followup
        self.context_window_size = 5


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
    assert result["stage_progress"]["is_complete"] is False
    assert result["collected_data"]["stage_1"]["project_background"]["value"] == "프로젝트 배경"


def test_run_moves_to_next_stage_when_questions_exhausted(monkeypatch):
    """고정/생성 질문이 모두 소진되면 다음 단계로 전환한다."""
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
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = state["stage_progress"]["generated_q_max"]

    result = analyst.run(state)
    stage_2_config = load_stage_config(2)

    assert result["current_stage"] == 2
    assert result["next_node"] == "question_generator"
    assert result["stage_progress"]["fixed_q_used"] == 0
    assert result["stage_progress"]["fixed_q_total"] == len(stage_2_config.fixed_questions)
    assert result["stage_progress"]["generated_q_used"] == 0
    assert result["stage_progress"]["generated_q_max"] == stage_2_config.max_generated_questions
    assert (
        result["stage_progress"]["force_all_generated_q"]
        == stage_2_config.force_all_generated_questions
    )
    assert result["stage_progress"]["is_complete"] is False


def test_run_moves_to_next_stage_when_dynamic_followup_disabled(monkeypatch):
    """고정 질문 소진 후 동적 질문이 비활성화면 단계를 완료 처리한다."""
    response = AnalystResponse(fields=[])
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))
    monkeypatch.setattr(
        analyst,
        "get_global_config",
        lambda: _DummyGlobalConfig(enable_dynamic_followup=False),
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = 0

    result = analyst.run(state)

    assert result["current_stage"] == 2
    assert result["next_node"] == "question_generator"


def test_run_moves_to_next_stage_when_required_fields_complete(monkeypatch):
    """고정 질문 소진 후 모든 필드가 충분하면 단계를 완료 처리한다."""
    response = AnalystResponse(fields=[])
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    stage_1_config = load_stage_config(1)
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = 0
    state["stage_progress"]["force_all_generated_q"] = False
    state["collected_data"]["stage_1"] = {
        field_name: {
            "field_name": field_name,
            "description": field_info.get("description", ""),
            "value": "충분한 답변",
            "completeness": 0.9,
        }
        for field_name, field_info in stage_1_config.required_fields.items()
    }

    result = analyst.run(state)

    assert result["current_stage"] == 2
    assert result["next_node"] == "question_generator"


def test_run_marks_all_complete_at_stage_4(monkeypatch):
    """4단계 완료 시 all_stages_complete와 overall_completion_percentage를 설정한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="final_deliverable",
                value="서비스 런칭 완료",
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
    stage_4_config = load_stage_config(4)
    state["stage_progress"]["fixed_q_total"] = len(stage_4_config.fixed_questions)
    state["stage_progress"]["generated_q_max"] = stage_4_config.max_generated_questions
    state["stage_progress"]["force_all_generated_q"] = stage_4_config.force_all_generated_questions
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = state["stage_progress"]["generated_q_max"]

    result = analyst.run(state)

    assert result["current_stage"] == 4
    assert result["stage_progress"]["is_complete"] is True
    assert result["all_stages_complete"] is True
    assert result["overall_completion_percentage"] == 88.5
    assert result["next_node"] == "end"
    assert result["collected_data"]["stage_4"]["final_deliverable"]["value"] == "서비스 런칭 완료"


def test_run_extended_mode_routes_to_question_generator(monkeypatch):
    """연장 모드에서 턴이 남아있으면 question_generator로 라우팅한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="연장 모드에서 보완한 프로젝트 배경",
                completeness=0.9,
                reasoning="추가 설명으로 완성도 상승",
            )
        ]
    )
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 1
    state["extension_turns_max"] = 3
    state["collected_data"]["stage_1"]["project_background"] = {
        "field_name": "project_background",
        "description": "이 활동을 시작하게 된 이유",
        "value": "기존 값",
        "completeness": 0.4,
    }

    result = analyst.run(state)

    assert result["next_node"] == "question_generator"
    assert result["all_stages_complete"] is False
    assert result["is_extended_mode"] is True
    assert result["collected_data"]["stage_1"]["project_background"]["value"] == (
        "연장 모드에서 보완한 프로젝트 배경"
    )


def test_run_extended_mode_finishes_when_turns_exhausted(monkeypatch):
    """연장 모드에서 사용 턴이 최대치에 도달하면 종료 상태로 복귀한다."""
    response = AnalystResponse(fields=[])
    monkeypatch.setattr(analyst, "get_llm", lambda temperature=0.3: _mock_analyst_llm(response))

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 3
    state["extension_turns_max"] = 3

    result = analyst.run(state)

    assert result["next_node"] == "end"
    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False
