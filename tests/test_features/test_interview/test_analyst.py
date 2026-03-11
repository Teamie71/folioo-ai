"""Analyst 노드 테스트"""

from httpx import ReadTimeout, RemoteProtocolError
from langchain_core.runnables import RunnableLambda

from common.llm import client as llm_client
from common.llm import get_analyst_llm
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


def test_run_moves_to_next_stage_when_questions_exhausted(monkeypatch):
    """고정/생성 질문이 모두 소진되면 다음 단계로 전환한다."""
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
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )
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
    monkeypatch.setattr(
        analyst, "get_analyst_llm", lambda temperature=0.3: _mock_analyst_llm(response)
    )

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
    state["extension_turns_max"] = 3

    result = analyst.run(state)

    assert result["next_node"] == "end"
    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False


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
    response = AnalystResponse(
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
    state["extension_turns_max"] = 3

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
