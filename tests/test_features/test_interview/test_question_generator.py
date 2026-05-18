"""QuestionGenerator 노드 테스트"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from features.interview.agents.nodes import question_generator
from features.interview.agents.nodes.utils import _flatten_additional_question_targets
from features.interview.agents.state import get_initial_interview_state
from features.interview.config.loader import get_all_stages, get_global_config, load_stage_config


def _mock_llm_return(content: str):
    return RunnableLambda(lambda _: AIMessage(content=content))


def _mock_llm_raise():
    def _raise(_):
        raise RuntimeError("LLM 호출 실패")

    return RunnableLambda(_raise)


def _mark_pre_evaluation_done(state: dict) -> dict:
    targets = _flatten_additional_question_targets(get_global_config(), get_all_stages())
    return {
        **state,
        "additional_question_pre_evaluated": True,
        "additional_question_target_statuses": {
            target["target"]: {"asked_count": 0, "is_satisfied": False}
            for target in targets
        },
    }


@pytest.fixture
def first_turn_state():
    """첫 턴 테스트용 state fixture"""
    return get_initial_interview_state(
        user_id="test_user", session_id="test_session", experience_name="AI 에이전트 개발 프로젝트"
    )


def test_first_turn_question_generation(first_turn_state, monkeypatch):
    """
    첫 턴 질문 생성 테스트
    - turn_number가 0일 때 첫 질문을 생성하는지 확인
    - AIMessage가 추가되는지 확인
    - stage_progress의 fixed_q_used가 1로 증가하는지 확인
    - next_node가 'end'인지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_return("첫 질문입니다."),
    )

    # 실행
    result = question_generator.run(first_turn_state)

    # 검증 1: AIMessage가 추가되었는지 확인
    assert "messages" in result
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], AIMessage)

    # 검증 2: 질문 내용이 생성되었는지 확인
    question_content = result["messages"][0].content
    assert question_content == "첫 질문입니다."

    # 검증 3: stage_progress가 업데이트되었는지 확인
    assert "stage_progress" in result
    assert result["stage_progress"]["fixed_q_used"] == 1

    # 검증 4: next_node가 'end'인지 확인
    assert result["next_node"] == "end"


def test_first_turn_uses_fixed_question_content(first_turn_state, monkeypatch):
    """
    첫 번째 고정 질문 내용이 사용되는지 확인
    - stage 1의 첫 번째 고정 질문이 플레이스홀더 치환되어 사용되는지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_raise(),
    )

    # 실행
    result = question_generator.run(first_turn_state)

    # 검증: 고정 질문 내용이 질문에 반영되었는지 확인
    question_content = result["messages"][0].content

    # 플레이스홀더가 치환되었는지 확인 (원본에는 [경험명]이 있지만 결과에는 실제 경험명이 들어가야 함)
    assert "[경험명]" not in question_content
    assert "AI 에이전트 개발 프로젝트" in question_content


def test_turn_number_controls_first_turn_detection(first_turn_state, monkeypatch):
    """messages 길이가 아니라 turn_number로 첫 턴을 판별한다."""
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_raise(),
    )

    state = {
        **first_turn_state,
        "turn_number": 1,
        "messages": [],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": 1,
        },
    }

    result = question_generator.run(state)

    expected_fixed_question = load_stage_config(1).fixed_questions[1]

    assert result["messages"][0].content == expected_fixed_question
    assert result["stage_progress"]["fixed_q_used"] == 2


def test_followup_fixed_question_generation(first_turn_state, monkeypatch):
    """
    후속 고정 질문 생성 테스트
    - 첫 턴 이후 고정 질문을 순차적으로 생성하는지 확인
    - 플레이스홀더가 치환되는지 확인
    """
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_raise(),
    )

    non_first_turn_state = {
        **first_turn_state,
        "turn_number": 1,
        "messages": [
            AIMessage(content="첫 질문입니다."),
            HumanMessage(content="사용자 답변입니다."),
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": 1,
        },
    }

    result = question_generator.run(non_first_turn_state)

    expected_fixed_question = load_stage_config(1).fixed_questions[1]

    assert result["stage_progress"]["fixed_q_used"] == 2
    assert result["next_node"] == "end"
    assert result["messages"][0].content == expected_fixed_question


def test_followup_fixed_question_includes_retrieved_insights_prompt_variable(
    first_turn_state,
    monkeypatch,
):
    """후속 고정 질문 생성 시 retrieved_insights를 프롬프트 변수로 전달한다."""
    captured: dict[str, object] = {}

    def _capture_invoke(chain, prompt_variables, max_retries_per_question):
        captured.update(prompt_variables)
        return "후속 질문"

    monkeypatch.setattr(question_generator, "_invoke_with_retry", _capture_invoke)
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_return("사용되지 않는 질문"),
    )

    state = {
        **first_turn_state,
        "turn_number": 1,
        "messages": [
            AIMessage(content="첫 질문입니다."),
            HumanMessage(content="사용자 답변입니다."),
        ],
        "file_contexts": ["[파일: report.pdf]\n프로젝트 요구사항 요약"],
        "retrieved_insights": [
            {
                "id": "insight-1",
                "title": "문제 해결 경험",
                "activity_name": "프로젝트 A",
                "category": "문제해결",
                "content": "복잡한 병목을 개선한 경험",
                "similarity_score": 0.91,
                "source": "search",
            }
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": 1,
        },
    }

    result = question_generator.run(state)

    assert result["messages"][0].content == "후속 질문"
    assert captured["retrieved_insights"] == (
        "- [문제해결] 문제 해결 경험\n"
        "  - 활동명: 프로젝트 A\n"
        "  - 출처: search\n"
        "  - 유사도: 0.91\n"
        "  - 내용: 복잡한 병목을 개선한 경험"
    )
    assert captured["file_contexts"] == "[파일: report.pdf]\n프로젝트 요구사항 요약"


def test_regular_mode_skips_dynamic_question_after_fixed_exhaustion(
    first_turn_state,
    monkeypatch,
):
    """정규 모드는 legacy generated 설정이 남아 있어도 생성 질문을 만들지 않는다."""

    def _raise_if_called(*args, **kwargs):
        raise AssertionError("정규 모드에서 생성 질문이 호출되면 안 됩니다.")

    monkeypatch.setattr(question_generator, "_generate_dynamic_question", _raise_if_called)

    state = {
        **first_turn_state,
        "turn_number": 1,
        "messages": [
            AIMessage(content="질문1"),
            HumanMessage(content="답변1"),
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": first_turn_state["stage_progress"]["fixed_q_total"],
            "generated_q_used": 0,
            "generated_q_max": 3,
        },
    }

    result = question_generator.run(state)

    assert result["messages"][0].content == "혹시 더 추가하고 싶은 내용이 있으신가요?"
    assert result["stage_progress"]["generated_q_used"] == 0
    assert result["stage_progress"]["generated_q_max"] == 3


def test_first_turn_uses_retry_limit_from_global_config(first_turn_state, monkeypatch):
    """global_config.max_retries_per_question 값만큼 재시도한다."""
    calls = {"count": 0}

    def _invoke(_):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("일시 실패")
        return AIMessage(content="재시도 성공 질문")

    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: RunnableLambda(_invoke),
    )
    monkeypatch.setattr(
        question_generator,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_retries_per_question": 2,
                "enable_dynamic_followup": True,
                "context_window_size": 5,
            },
        )(),
    )

    result = question_generator.run(first_turn_state)

    assert calls["count"] == 3
    assert result["messages"][0].content == "재시도 성공 질문"


def test_fallback_question_when_called_after_fixed_exhaustion(first_turn_state):
    """고정 질문 소진 상태로 호출되어도 AIMessage fallback을 반환한다."""
    state = {
        **first_turn_state,
        "turn_number": 1,
        "messages": [
            AIMessage(content="질문1"),
            HumanMessage(content="답변1"),
        ],
        "stage_progress": {
            **first_turn_state["stage_progress"],
            "fixed_q_used": first_turn_state["stage_progress"]["fixed_q_total"],
            "generated_q_used": 0,
            "generated_q_max": 3,
        },
    }

    result = question_generator.run(state)

    assert result["next_node"] == "end"
    assert isinstance(result["messages"][0], AIMessage)
    assert result["messages"][0].content == "혹시 더 추가하고 싶은 내용이 있으신가요?"
    assert result["stage_progress"]["generated_q_used"] == 0


def test_extended_mode_pre_evaluation_ends_when_all_targets_satisfied(
    first_turn_state,
    monkeypatch,
):
    """사전 판정에서 모든 target이 충분하면 질문 없이 안내 문구로 종료한다."""
    targets = _flatten_additional_question_targets(get_global_config(), get_all_stages())
    monkeypatch.setattr(
        question_generator,
        "_pre_evaluate_additional_question_targets",
        lambda state, targets: ({target["target"]: True for target in targets}, None),
    )

    state = {
        **first_turn_state,
        "is_extended_mode": True,
        "all_stages_complete": False,
        "extension_turns_used": 0,
        "extension_turns_max": 18,
    }

    result = question_generator.run(state)

    assert result["is_extended_mode"] is False
    assert result["all_stages_complete"] is True
    assert result["extension_turns_used"] == 0
    assert result["additional_question_pre_evaluated"] is True
    assert result["next_node"] == "end"
    assert "추가 질문 없이" in result["messages"][0].content
    assert all(
        result["additional_question_target_statuses"][target["target"]]["is_satisfied"]
        for target in targets
    )


def test_extended_mode_pre_evaluation_selects_first_unsatisfied_target(
    first_turn_state,
    monkeypatch,
):
    """사전 판정 후 부족한 첫 target을 선택하고 asked_count를 증가한다."""
    monkeypatch.setattr(
        question_generator,
        "_pre_evaluate_additional_question_targets",
        lambda state, targets: ({targets[0]["target"]: True}, None),
    )
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_return("두 번째 target 질문"),
    )

    state = {
        **first_turn_state,
        "is_extended_mode": True,
        "all_stages_complete": False,
        "extension_turns_used": 0,
        "extension_turns_max": 18,
    }

    result = question_generator.run(state)

    assert result["messages"][0].content == "두 번째 target 질문"
    assert result["extension_turns_used"] == 1
    assert result["current_additional_question_target_id"] == "stage_3_episode_2_strategy_rationale"
    assert result["additional_question_target_statuses"]["stage_3_episode_1_strategy_rationale"][
        "is_satisfied"
    ] is True
    assert result["additional_question_target_statuses"]["stage_3_episode_2_strategy_rationale"][
        "asked_count"
    ] == 1


def test_extended_mode_uses_second_pass_after_first_pass_exhausted(
    first_turn_state,
    monkeypatch,
):
    """1차 후보가 없으면 asked_count 1인 target을 2차 패스로 선택한다."""
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_return("2차 패스 질문"),
    )
    state = _mark_pre_evaluation_done(
        {
            **first_turn_state,
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_turns_used": 17,
            "extension_turns_max": 18,
        }
    )
    state["additional_question_target_statuses"] = {
        target_id: {"asked_count": 1, "is_satisfied": False}
        for target_id in state["additional_question_target_statuses"]
    }

    result = question_generator.run(state)

    assert result["messages"][0].content == "2차 패스 질문"
    assert result["current_additional_question_target_id"] == "stage_3_episode_1_strategy_rationale"
    assert result["additional_question_target_statuses"]["stage_3_episode_1_strategy_rationale"][
        "asked_count"
    ] == 2


def test_extended_mode_ends_when_no_askable_target_remains(first_turn_state):
    """모든 부족 target을 2회 질문했으면 추가 질문 없이 종료한다."""
    state = _mark_pre_evaluation_done(
        {
            **first_turn_state,
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_turns_used": 10,
            "extension_turns_max": 18,
        }
    )
    state["additional_question_target_statuses"] = {
        target_id: {"asked_count": 2, "is_satisfied": False}
        for target_id in state["additional_question_target_statuses"]
    }

    result = question_generator.run(state)

    assert result["is_extended_mode"] is False
    assert result["all_stages_complete"] is True
    assert result["extension_turns_used"] == 10
    assert "추가 질문 없이" in result["messages"][0].content


def test_extended_mode_generates_question_and_increments_turn(first_turn_state, monkeypatch):
    """추가 대화 질문 생성 시 selected target을 기록하고 턴 카운트를 증가시킨다."""
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_return("연장 질문입니다."),
    )

    state = _mark_pre_evaluation_done(
        {
            **first_turn_state,
            "messages": [
                AIMessage(content="이전 질문"),
                HumanMessage(content="이전 답변"),
            ],
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_turns_used": 0,
            "extension_turns_max": 18,
        }
    )

    result = question_generator.run(state)

    assert result["next_node"] == "end"
    assert result["messages"][0].content == "연장 질문입니다."
    assert result["extension_turns_used"] == 1
    assert result["current_additional_question_target_id"] == "stage_3_episode_1_strategy_rationale"
    assert result["additional_question_target_statuses"]["stage_3_episode_1_strategy_rationale"][
        "asked_count"
    ] == 1


def test_extended_mode_fallback_increments_turn(first_turn_state, monkeypatch):
    """추가 대화에서 LLM 실패 시 selected target 기준 fallback을 만들고 턴 카운트를 증가시킨다."""
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_raise(),
    )

    state = _mark_pre_evaluation_done(
        {
            **first_turn_state,
            "messages": [
                AIMessage(content="이전 질문"),
                HumanMessage(content="이전 답변"),
            ],
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_turns_used": 0,
            "extension_turns_max": 18,
        }
    )

    result = question_generator.run(state)

    assert result["next_node"] == "end"
    assert "Episode 1 해결 전략과 선택 근거" in result["messages"][0].content
    assert result["extension_turns_used"] == 1


def test_extended_mode_includes_retrieved_insights_prompt_variable(first_turn_state, monkeypatch):
    """추가 대화 질문 생성 시 retrieved_insights와 selected_target을 프롬프트 변수로 전달한다."""
    captured: dict[str, object] = {}

    def _capture_invoke(chain, prompt_variables, max_retries_per_question):
        captured.update(prompt_variables)
        return "연장 질문"

    monkeypatch.setattr(question_generator, "_invoke_with_retry", _capture_invoke)
    monkeypatch.setattr(
        question_generator,
        "get_llm",
        lambda model=None, temperature=0.7: _mock_llm_return("사용되지 않는 질문"),
    )

    state = _mark_pre_evaluation_done(
        {
            **first_turn_state,
            "messages": [
                AIMessage(content="이전 질문"),
                HumanMessage(content="이전 답변"),
            ],
            "file_contexts": ["[파일: report.pdf]\n성과 지표가 포함된 문서"],
            "retrieved_insights": [
                {
                    "id": "insight-1",
                    "title": "학습 인사이트",
                    "activity_name": "프로젝트 C",
                    "category": "학습",
                    "content": "새로운 기술을 빠르게 익힌 경험",
                    "similarity_score": 0.73,
                    "source": "search",
                }
            ],
            "is_extended_mode": True,
            "all_stages_complete": False,
            "extension_turns_used": 0,
            "extension_turns_max": 18,
        }
    )

    result = question_generator.run(state)

    assert result["messages"][0].content == "연장 질문"
    assert captured["retrieved_insights"] == (
        "- [학습] 학습 인사이트\n"
        "  - 활동명: 프로젝트 C\n"
        "  - 출처: search\n"
        "  - 유사도: 0.73\n"
        "  - 내용: 새로운 기술을 빠르게 익힌 경험"
    )
    assert captured["file_contexts"] == "[파일: report.pdf]\n성과 지표가 포함된 문서"
    assert "Episode 1 해결 전략과 선택 근거" in captured["selected_target"]
    assert "problem_episodes" not in captured["selected_target"]
