"""Analyst 노드 테스트"""

from httpx import ReadTimeout, RemoteProtocolError
from langchain_core.runnables import RunnableLambda

from common.llm import client as llm_client
from common.llm import get_analyst_llm
from features.interview.agents.nodes import analyst
from features.interview.agents.nodes.utils import _format_retrieved_insights
from features.interview.agents.prompts.analyst import (
    AnalystFieldResult,
    AnalystResponse,
    ExtendedAnalystResponse,
)
from features.interview.agents.state import get_initial_interview_state
from features.interview.config.loader import load_stage_config


class _DummyLLM:
    def __init__(self, response: AnalystResponse):
        self._response = response

    def with_structured_output(self, _schema):
        return RunnableLambda(lambda _: self._response)


class _DummyGlobalConfig:
    def __init__(
        self,
        *,
        enable_dynamic_followup: bool = True,
        max_extensions: int = 1,
        extension_turns_per_session: int = 18,
    ):
        self.enable_dynamic_followup = enable_dynamic_followup
        self.context_window_size = 5
        self.max_extensions = max_extensions
        self.extension_turns_per_session = extension_turns_per_session


def _mock_analyst_llm(response: AnalystResponse):
    return _DummyLLM(response)


class _InvokeChain:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def invoke(self, payload):
        self.calls += 1
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_run_keeps_stage_when_not_complete(monkeypatch):
    """단계가 완료되지 않으면 current_stage를 유지한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="프로젝트 배경",
                completeness=0.8,
            )
        ]
    )
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

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


def test_format_retrieved_insights_includes_activity_source_and_similarity():
    """Analyst와 QuestionGenerator가 공유하는 인사이트 포맷에 핵심 필드가 포함된다."""
    formatted = _format_retrieved_insights(
        [
            {
                "id": "insight-1",
                "title": "문제 해결 경험",
                "activity_name": "프로젝트 A",
                "category": "문제해결",
                "content": "병목을 개선한 경험",
                "similarity_score": 0.91,
                "source": "search",
            },
            {
                "id": "insight-2",
                "title": "멘션 인사이트",
                "activity_name": "프로젝트 B",
                "category": "기타",
                "content": "사용자가 직접 언급한 내용",
                "similarity_score": None,
                "source": "mention",
            },
        ]
    )

    assert "활동명: 프로젝트 A" in formatted
    assert "출처: search" in formatted
    assert "유사도: 0.91" in formatted
    assert "활동명: 프로젝트 B" in formatted
    assert "출처: mention" in formatted
    assert "유사도: 없음" in formatted


def test_run_moves_to_next_stage_when_fixed_questions_exhausted(monkeypatch):
    """고정 질문이 소진되면 생성 질문 상태와 무관하게 다음 단계로 전환한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="프로젝트 배경",
                completeness=0.8,
            )
        ]
    )
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = 0
    state["stage_progress"]["generated_q_max"] = 99
    state["stage_progress"]["force_all_generated_q"] = True

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


def test_run_moves_to_next_stage_when_dynamic_followup_enabled(monkeypatch):
    """동적 질문 설정이 켜져 있어도 고정 질문이 소진되면 단계를 완료 처리한다."""
    response = AnalystResponse(fields=[])
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )
    monkeypatch.setattr(
        analyst,
        "get_global_config",
        lambda: _DummyGlobalConfig(enable_dynamic_followup=True),
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = 0
    state["stage_progress"]["generated_q_max"] = 99

    result = analyst.run(state)

    assert result["current_stage"] == 2
    assert result["next_node"] == "question_generator"


def test_run_keeps_stage_when_fixed_questions_remain_even_if_required_fields_complete(
    monkeypatch,
):
    """필수 필드가 충분해도 고정 질문이 남아 있으면 단계를 완료하지 않는다."""
    response = AnalystResponse(fields=[])
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    stage_1_config = load_stage_config(1)
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"] - 1
    state["stage_progress"]["generated_q_used"] = state["stage_progress"]["generated_q_max"]
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

    assert result["current_stage"] == 1
    assert result["next_node"] == "question_generator"
    assert result["stage_progress"]["is_complete"] is False


def test_run_starts_extended_mode_at_stage_4_completion(monkeypatch):
    """4단계 완료 시 같은 요청에서 연장 모드 첫 질문 생성으로 라우팅한다."""
    response = AnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="final_deliverable",
                value="서비스 런칭 완료",
                completeness=0.9,
            )
        ]
    )
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )
    monkeypatch.setattr(
        analyst,
        "_calculate_overall_completion_percentage",
        lambda experience_name, collected_data: (88.5, None),
    )
    monkeypatch.setattr(
        analyst,
        "get_global_config",
        lambda: _DummyGlobalConfig(max_extensions=1, extension_turns_per_session=18),
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_stage"] = 4
    stage_4_config = load_stage_config(4)
    state["stage_progress"]["fixed_q_total"] = len(stage_4_config.fixed_questions)
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]
    state["stage_progress"]["generated_q_used"] = 0
    state["stage_progress"]["generated_q_max"] = 99
    state["stage_progress"]["force_all_generated_q"] = True

    result = analyst.run(state)

    assert result["current_stage"] == 4
    assert result["stage_progress"]["is_complete"] is True
    assert result["all_stages_complete"] is False
    assert result["is_extended_mode"] is True
    assert result["extension_count"] == 1
    assert result["extension_turns_used"] == 0
    assert result["extension_turns_max"] == 18
    assert result["additional_question_target_statuses"] == {}
    assert result["additional_question_pre_evaluated"] is False
    assert result["current_additional_question_target_id"] is None
    assert result["overall_completion_percentage"] == 88.5
    assert result["next_node"] == "question_generator"
    assert result["collected_data"]["stage_4"]["final_deliverable"]["value"] == "서비스 런칭 완료"


def test_run_marks_all_complete_at_stage_4_when_extension_limit_reached(monkeypatch):
    """연장 가능 횟수가 없으면 4단계 완료 시 기존처럼 종료한다."""
    response = AnalystResponse(fields=[])
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )
    monkeypatch.setattr(
        analyst,
        "_calculate_overall_completion_percentage",
        lambda experience_name, collected_data: (88.5, None),
    )
    monkeypatch.setattr(
        analyst,
        "get_global_config",
        lambda: _DummyGlobalConfig(max_extensions=1, extension_turns_per_session=18),
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_stage"] = 4
    state["extension_count"] = 1
    stage_4_config = load_stage_config(4)
    state["stage_progress"]["fixed_q_total"] = len(stage_4_config.fixed_questions)
    state["stage_progress"]["fixed_q_used"] = state["stage_progress"]["fixed_q_total"]

    result = analyst.run(state)

    assert result["stage_progress"]["is_complete"] is True
    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False
    assert result["overall_completion_percentage"] == 88.5
    assert result["next_node"] == "end"


def test_run_extended_mode_routes_to_question_generator(monkeypatch):
    """연장 모드에서 턴이 남아있으면 question_generator로 라우팅한다."""
    response = ExtendedAnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="연장 모드에서 보완한 프로젝트 배경",
                completeness=0.9,
            )
        ]
    )
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 1
    state["extension_turns_max"] = 18
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
    response = ExtendedAnalystResponse(fields=[])
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 18
    state["extension_turns_max"] = 18

    result = analyst.run(state)

    assert result["next_node"] == "end"
    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False


def test_run_extended_mode_finishes_when_all_additional_targets_satisfied(monkeypatch):
    """추가 질문 target이 모두 충분하면 턴이 남아도 종료한다."""
    response = ExtendedAnalystResponse(fields=[])
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 1
    state["extension_turns_max"] = 18
    state["additional_question_target_statuses"] = {
        target["target"]: {"asked_count": 0, "is_satisfied": True}
        for target in analyst._flatten_additional_question_targets(
            analyst.get_global_config(),
            analyst.get_all_stages(),
        )
    }

    result = analyst.run(state)

    assert result["next_node"] == "end"
    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False


def test_run_extended_mode_finishes_when_no_askable_target_remains(monkeypatch):
    """질문 가능한 target이 없으면 미충족 target이 남아도 종료한다."""
    response = ExtendedAnalystResponse(fields=[])
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 10
    state["extension_turns_max"] = 18
    state["additional_question_target_statuses"] = {
        target["target"]: {"asked_count": 2, "is_satisfied": False}
        for target in analyst._flatten_additional_question_targets(
            analyst.get_global_config(),
            analyst.get_all_stages(),
        )
    }

    result = analyst.run(state)

    assert result["next_node"] == "end"
    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False


def test_run_extended_mode_routes_to_question_generator_on_end_intent(monkeypatch):
    """사용자가 추가 대화 종료 의도를 표현하면 안내 생성을 위해 QG로 라우팅한다."""
    response = ExtendedAnalystResponse(fields=[], should_end_extended_mode=True)
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 3
    state["extension_turns_max"] = 18

    result = analyst.run(state)

    assert result["next_node"] == "question_generator"
    assert result["all_stages_complete"] is False
    assert result["is_extended_mode"] is True
    assert result["pending_extended_end_guide"] is True
    assert result["extension_turns_used"] == 3


def test_run_extended_mode_marks_last_target_satisfied(monkeypatch):
    """last_target_satisfied=True면 직전 질문 target 상태가 충족으로 갱신된다."""
    response = ExtendedAnalystResponse(fields=[], last_target_satisfied=True)
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    targets = analyst._flatten_additional_question_targets(
        analyst.get_global_config(),
        analyst.get_all_stages(),
    )
    first_target = targets[0]["target"]

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 2
    state["extension_turns_max"] = 18
    state["current_additional_question_target_id"] = first_target
    state["additional_question_target_statuses"] = {
        first_target: {"asked_count": 2, "is_satisfied": False}
    }

    result = analyst.run(state)

    statuses = result["additional_question_target_statuses"]
    assert statuses[first_target]["is_satisfied"] is True
    # asked_count는 보존된다
    assert statuses[first_target]["asked_count"] == 2


def test_run_extended_mode_keeps_status_when_last_target_not_satisfied(monkeypatch):
    """last_target_satisfied=False는 기존 is_satisfied 값을 되돌리지 않는다 (단조 증가)."""
    response = ExtendedAnalystResponse(fields=[], last_target_satisfied=False)
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    targets = analyst._flatten_additional_question_targets(
        analyst.get_global_config(),
        analyst.get_all_stages(),
    )
    first_target = targets[0]["target"]

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 2
    state["extension_turns_max"] = 18
    state["current_additional_question_target_id"] = first_target
    state["additional_question_target_statuses"] = {
        first_target: {"asked_count": 1, "is_satisfied": True}
    }

    result = analyst.run(state)

    statuses = result["additional_question_target_statuses"]
    assert statuses[first_target]["is_satisfied"] is True
    assert statuses[first_target]["asked_count"] == 1


def test_run_extended_mode_ignores_target_status_without_last_target(monkeypatch):
    """직전 추가 질문 target이 없으면 상태 갱신을 건너뛰고 오류 없이 진행한다."""
    response = ExtendedAnalystResponse(fields=[], last_target_satisfied=True)
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 1
    state["extension_turns_max"] = 18
    state["current_additional_question_target_id"] = None

    result = analyst.run(state)

    assert result["next_node"] == "question_generator"
    statuses = result["additional_question_target_statuses"]
    assert all(status["is_satisfied"] is False for status in statuses.values())


def test_run_extended_mode_defaults_end_intent_false_on_llm_failure(monkeypatch):
    """연장 모드 LLM 호출 실패 시 종료 의도는 기본값 False로 동작을 보존한다."""

    class _ProtectedLLM:
        def with_structured_output(self, _schema):
            return RunnableLambda(lambda payload: payload)

    def _raise(_chain, _payload):
        raise RuntimeError("연장 모드 LLM 실패")

    monkeypatch.setattr(analyst, "get_analyst_llm", lambda temperature=0.3: _ProtectedLLM())
    monkeypatch.setattr(analyst, "_invoke_with_retry", _raise)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["all_stages_complete"] = False
    state["extension_turns_used"] = 1
    state["extension_turns_max"] = 18

    result = analyst.run(state)

    assert result["next_node"] == "question_generator"
    assert result["all_stages_complete"] is False
    assert result["is_extended_mode"] is True
    assert result["llm_error"] == "연장 모드 LLM 실패"


def test_invoke_with_retry_retries_retryable_errors(monkeypatch):
    """재시도 대상 예외는 최대 2회까지 재시도 후 성공한다."""
    chain = _InvokeChain(
        [
            RemoteProtocolError("temporary protocol error"),
            ReadTimeout("temporary timeout"),
            {"status": "ok"},
        ]
    )

    sleep_calls = []
    monkeypatch.setattr(analyst.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    result = analyst._invoke_with_retry(chain, {"payload": True})

    assert result == {"status": "ok"}
    assert chain.calls == 3
    assert len(sleep_calls) == 2


def test_invoke_with_retry_retries_wrapped_retryable_errors(monkeypatch):
    """재시도 대상 전송 예외가 래핑돼도 재시도한다."""

    class _WrappedTransportError(RuntimeError):
        pass

    wrapped_error = _WrappedTransportError("wrapped")
    wrapped_error.__cause__ = RemoteProtocolError("temporary protocol error")
    chain = _InvokeChain([wrapped_error, {"status": "ok"}])

    sleep_calls = []
    monkeypatch.setattr(analyst.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    result = analyst._invoke_with_retry(chain, {"payload": True})

    assert result == {"status": "ok"}
    assert chain.calls == 2
    assert len(sleep_calls) == 1


def test_invoke_with_retry_raises_non_retryable_error(monkeypatch):
    """재시도 대상이 아닌 예외는 즉시 다시 발생시킨다."""
    chain = _InvokeChain([ValueError("bad payload")])

    monkeypatch.setattr(analyst.time, "sleep", lambda _seconds: None)

    try:
        analyst._invoke_with_retry(chain, {"payload": True})
    except ValueError as exc:
        assert str(exc) == "bad payload"
    else:
        raise AssertionError("ValueError가 다시 발생해야 합니다.")

    assert chain.calls == 1


def test_run_preserves_state_and_records_error_when_retries_exhausted(monkeypatch):
    """재시도 소진 후에도 기존 데이터 유지와 최종 llm_error 기록을 보존한다."""

    class _PromptStub:
        def __or__(self, other):
            return other

    class _FailingLLM:
        def with_structured_output(self, _schema):
            return _InvokeChain(
                [
                    RemoteProtocolError("temporary protocol error"),
                    ReadTimeout("temporary timeout"),
                    ReadTimeout("final timeout"),
                ]
            )

    monkeypatch.setattr(analyst, "analyst_prompt", _PromptStub())
    monkeypatch.setattr(analyst, "get_analyst_llm", lambda temperature=0.3: _FailingLLM())
    monkeypatch.setattr(analyst.time, "sleep", lambda _seconds: None)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    original_data = state["collected_data"]

    result = analyst.run(state)

    assert result["collected_data"] == original_data
    assert result["llm_error"] == "final timeout"
    assert result["next_node"] == "question_generator"


def test_run_extended_mode_uses_protected_invoke_helper(monkeypatch):
    """연장 모드 분석은 보호된 invoke 헬퍼를 사용한다."""
    response = ExtendedAnalystResponse(
        fields=[
            AnalystFieldResult(
                field_name="project_background",
                value="연장 모드 업데이트",
                completeness=0.9,
            )
        ]
    )
    invoke_calls = []

    class _ProtectedLLM:
        def with_structured_output(self, _schema):
            return RunnableLambda(lambda payload: payload)

    monkeypatch.setattr(analyst, "get_analyst_llm", lambda temperature=0.3: _ProtectedLLM())
    monkeypatch.setattr(
        analyst,
        "_invoke_with_retry",
        lambda chain, payload: invoke_calls.append((chain, payload)) or response,
    )

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["is_extended_mode"] = True
    state["extension_turns_used"] = 0
    state["extension_turns_max"] = 18

    result = analyst.run(state)

    assert len(invoke_calls) == 1
    assert (
        result["collected_data"]["stage_1"]["project_background"]["value"] == "연장 모드 업데이트"
    )


def test_calculate_overall_completion_uses_protected_invoke_helper(monkeypatch):
    """전체 완료율 계산도 보호된 invoke 헬퍼를 사용한다."""
    invoke_calls = []

    class _CompletionResponse:
        content = "88.5"

    monkeypatch.setattr(
        analyst,
        "get_analyst_llm",
        lambda temperature=0.3: RunnableLambda(lambda payload: payload),
    )
    monkeypatch.setattr(
        analyst,
        "_invoke_with_retry",
        lambda chain, payload: invoke_calls.append((chain, payload)) or _CompletionResponse(),
    )

    score, error = analyst._calculate_overall_completion_percentage(
        "테스트 경험",
        {"stage_1": {}},
    )

    assert score == 88.5
    assert error is None
    assert len(invoke_calls) == 1


def test_get_analyst_llm_uses_dedicated_configuration(monkeypatch):
    """Analyst 전용 LLM helper는 스트리밍 비활성화와 긴 타임아웃을 사용한다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test")
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    llm_client.get_analyst_llm.cache_clear()

    captured = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeChatOpenAI)

    result = get_analyst_llm(temperature=0.2)

    assert isinstance(result, _FakeChatOpenAI)
    assert captured["model"] == "test-model"
    assert captured["temperature"] == 0.2
    assert captured["request_timeout"] == 120
    assert captured["disable_streaming"] is True
    assert captured["max_retries"] == 0
    llm_client.get_analyst_llm.cache_clear()


def test_get_llm_omits_max_retries_when_unset(monkeypatch):
    """기본 LLM helper는 max_retries를 명시하지 않아 provider 기본값을 유지한다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test")
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    llm_client.get_llm.cache_clear()

    captured = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeChatOpenAI)

    result = llm_client.get_llm(temperature=0.6)

    assert isinstance(result, _FakeChatOpenAI)
    assert captured["model"] == "test-model"
    assert captured["temperature"] == 0.6
    assert captured["request_timeout"] is None
    assert "max_retries" not in captured
    llm_client.get_llm.cache_clear()
