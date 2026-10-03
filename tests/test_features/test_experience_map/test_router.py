"""Router·Fallback 노드 테스트 (에이전트 문서 5-1, 5-11)

LLM 은 대역으로 바꿉니다. 실제 호출은 비용·비결정성 때문에 단위 테스트에
넣지 않습니다 — 분기와 계약만 검증합니다.
"""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map.errors import LlmError
from features.experience_map.nodes import router as router_node
from features.experience_map.nodes.fallback import (
    FALLBACK_MESSAGES,
    fallback,
    fallback_message,
)
from features.experience_map.nodes.router import next_node, route
from features.experience_map.prompts.router import build_gap_context
from features.experience_map.schemas import FallbackReason, RouterOutput
from features.experience_map.state import start_turn

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def make_state(**overrides):
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
        user_message=overrides.pop("user_message", "결제 실패 문제를 해결한 내용을 정리해줘"),
    )
    state.update(overrides)
    return state


@pytest.fixture
def fake_llm(monkeypatch):
    """LLM 을 대역으로 바꾸고 **렌더된 프롬프트**를 수집한다.

    실제 `RunnableLambda` 를 써서 프롬프트 템플릿을 그대로 통과시킨다. 변수를
    가로채는 대신 완성된 프롬프트를 보므로, 템플릿이 실제로 그 값을 쓰는지까지
    검증된다.

    Returns:
        반환값(또는 예외)을 받아 `prompts` 리스트를 돌려주는 setter
    """

    def _set(result: RouterOutput | Exception) -> list[str]:
        prompts: list[str] = []

        async def _handle(prompt_value) -> RouterOutput:
            prompts.append(prompt_value.to_string())
            if isinstance(result, Exception):
                raise result
            return result

        class _FakeLlm:
            def with_structured_output(self, schema):
                return RunnableLambda(_handle)

        monkeypatch.setattr(router_node, "get_experience_map_llm", lambda **kw: _FakeLlm())
        return prompts

    return _set


# ===== 코드로 판정하는 것 =====


@pytest.mark.asyncio
async def test_file_input_is_decided_by_code(fake_llm):
    """파일이 있으면 LLM 에게 묻지 않는다."""
    prompts = fake_llm(RouterOutput(intent="out_of_scope", reason="틀린 답"))
    state = make_state(file_references=[{"file_id": "f_1"}], user_message=None)

    result = await route(state)

    assert result["intent"] == "file_input"
    assert prompts == []  # LLM 을 부르지 않았다


@pytest.mark.asyncio
async def test_empty_input_goes_to_fallback(fake_llm):
    """메시지도 파일도 없으면 반영할 것이 없다."""
    fake_llm(RouterOutput(intent="chat_input", reason="-"))
    state = make_state(user_message="   ")

    result = await route(state)

    assert result["intent"] == "out_of_scope"
    assert result["fallback_reason"] == "nothing_to_apply"


# ===== LLM 판정 =====


@pytest.mark.asyncio
async def test_chat_input_passes_through(fake_llm):
    fake_llm(RouterOutput(intent="chat_input", reason="경험의 과정이 담겨 있음"))

    result = await route(make_state())

    assert result["intent"] == "chat_input"
    assert result.get("fallback_reason") is None


@pytest.mark.asyncio
async def test_out_of_scope_sets_reason(fake_llm):
    fake_llm(RouterOutput(intent="out_of_scope", reason="자기소개서 생성 요청"))

    result = await route(make_state(user_message="자기소개서 써줘"))

    assert result["intent"] == "out_of_scope"
    assert result["fallback_reason"] == "out_of_scope"


@pytest.mark.asyncio
async def test_router_records_current_node(fake_llm):
    fake_llm(RouterOutput(intent="chat_input", reason="-"))

    assert (await route(make_state()))["current_node"] == "router"


@pytest.mark.asyncio
async def test_llm_failure_raises_retryable_node_error(fake_llm):
    """그래프 공통 정책이 재시도할 수 있도록 시스템 실패를 fallback으로 숨기지 않는다."""
    prompts = fake_llm(RuntimeError("upstream 500"))

    with pytest.raises(LlmError) as exc_info:
        await route(make_state())

    assert len(prompts) == 1
    assert exc_info.value.failed_node == "router"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_user_message_not_logged_on_failure(fake_llm, caplog):
    """분류 실패 로그에 입력 원문이 새지 않는다."""
    fake_llm(RuntimeError("boom"))
    secret = "주민등록번호 900101-1234567 로 가입했다"

    with pytest.raises(LlmError):
        await route(make_state(user_message=secret))

    assert secret not in caplog.text


# ===== gap 맥락 =====


@pytest.mark.asyncio
async def test_active_gap_is_passed_as_context(fake_llm):
    """직전 질문을 맥락으로 준다. 짧은 답변을 무관한 입력으로 오해하지 않게."""
    prompts = fake_llm(RouterOutput(intent="chat_input", reason="-"))
    gap = {"message": "그 해결 방법을 고른 기준이 무엇이었나요?"}

    await route(make_state(active_gap=gap, user_message="비용이 적게 들어서요"))

    assert "그 해결 방법을 고른 기준" in prompts[0]
    assert "비용이 적게 들어서요" in prompts[0]


@pytest.mark.asyncio
async def test_router_does_not_classify_gap_answer(fake_llm):
    """gap 이 있어도 Router 가 gap 답변으로 분기하지 않는다 (5-1).

    분류는 반영 내용 필터링이 원문과 함께 본다.
    """
    fake_llm(RouterOutput(intent="chat_input", reason="-"))
    gap = {"message": "무엇을 배웠나요?", "gap_type": "extend_block"}

    result = await route(make_state(active_gap=gap))

    assert result["intent"] == "chat_input"
    assert next_node(result) == "content_filter"


def test_gap_context_is_empty_without_gap():
    assert build_gap_context(None) == ""
    assert build_gap_context({}) == ""
    assert build_gap_context({"message": "  "}) == ""


# ===== 다음 노드 =====


@pytest.mark.parametrize(
    "intent,expected",
    [
        ("file_input", "file_processor"),
        ("chat_input", "content_filter"),
        ("out_of_scope", "fallback"),
        (None, "fallback"),
    ],
)
def test_next_node(intent, expected):
    assert next_node({"intent": intent}) == expected


# ===== Fallback =====


@pytest.mark.parametrize(
    "reason", ["out_of_scope", "file_unreadable", "nothing_to_apply", "ambiguous_target"]
)
def test_each_reason_has_its_own_message(reason):
    """진입 경로 4가지가 각각 자기 문구를 낸다 (5-11)."""
    assert fallback_message(reason) == FALLBACK_MESSAGES[reason]


def test_messages_are_all_distinct():
    """문구가 하나로 합쳐지면 사용자가 다음에 뭘 할지 알 수 없다."""
    assert len(set(FALLBACK_MESSAGES.values())) == len(FALLBACK_MESSAGES)


def test_file_unreadable_suggests_another_file():
    """손상된 파일을 올린 사용자가 다시 시도할 방법을 알아야 한다."""
    message = FALLBACK_MESSAGES["file_unreadable"]
    assert "다른 파일" in message or "직접 입력" in message


def test_ambiguous_target_asks_back():
    """대상이 불명확하면 되묻는다 (6-2)."""
    assert "알려주세요" in FALLBACK_MESSAGES["ambiguous_target"]


def test_unknown_reason_falls_back_to_default():
    assert fallback_message("무슨사유") == FALLBACK_MESSAGES["out_of_scope"]
    assert fallback_message(None) == FALLBACK_MESSAGES["out_of_scope"]


@pytest.mark.asyncio
async def test_fallback_keeps_reason():
    state = make_state(fallback_reason="file_unreadable")

    result = await fallback(state)

    assert result["fallback_reason"] == "file_unreadable"
    assert result["current_node"] == "fallback"


@pytest.mark.asyncio
async def test_fallback_defaults_when_reason_missing():
    assert (await fallback(make_state()))["fallback_reason"] == "out_of_scope"


def test_all_fallback_reasons_are_covered():
    """`FallbackReason` 리터럴과 문구 표가 일치한다."""
    declared = set(FallbackReason.__args__)
    assert declared == set(FALLBACK_MESSAGES)


# ===== PartialGraphRunner (3.17 전까지의 임시 실행기) =====


@pytest.mark.asyncio
async def test_router_only_runner_emits_fallback_message(fake_llm):
    """out_of_scope 면 fallback 문구를 보내고 커밋하지 않는다."""
    from features.experience_map.graph_runner import PartialGraphRunner

    fake_llm(RouterOutput(intent="out_of_scope", reason="자기소개서 생성 요청"))
    events = [e async for e in PartialGraphRunner().run(make_state(user_message="자소서 써줘"))]

    types = [e.model_dump()["type"] for e in events]
    assert types == ["node_status", "node_status", "message_complete"]

    message = events[-1].model_dump()["message"]
    assert message["response_kind"] == "fallback"
    assert message["committed"] is False
    assert message["ai_response"] == FALLBACK_MESSAGES["out_of_scope"]
    assert "commit_result" not in types


@pytest.mark.asyncio
async def test_partial_runner_falls_through_for_chat_input(fake_llm, monkeypatch):
    """chat_input 이면 뒤 노드로 넘긴다. 각 노드 이벤트는 한 번만 나간다."""
    from features.experience_map.graph_runner import PartialGraphRunner
    from features.experience_map.nodes import content_filter as filter_node
    from features.experience_map.schemas import ContentFilterOutput

    fake_llm(RouterOutput(intent="chat_input", reason="경험이 담겨 있음"))

    # content_filter 도 실제로 돈다. 그쪽 LLM 도 대역으로 바꾼다.
    def _filter_llm(**kwargs):
        async def _output(_):
            return ContentFilterOutput(
                new_items=[
                    {
                        "item_id": "it_1",
                        "text": "결제 실패 문제를 해결한 내용을 정리해줘",
                        "source": "message",
                    }
                ]
            )

        class _FakeLlm:
            def with_structured_output(self, schema):
                return RunnableLambda(_output)

        return _FakeLlm()

    monkeypatch.setattr(filter_node, "get_experience_map_llm", _filter_llm)

    events = [e async for e in PartialGraphRunner().run(make_state())]
    dumped = [e.model_dump() for e in events]

    for node in ("router", "content_filter"):
        emitted = [d for d in dumped if d["type"] == "node_status" and d["node"] == node]
        assert len(emitted) == 2, f"{node} 이벤트가 중복되거나 빠졌습니다"

    assert any(d["type"] == "commit_result" for d in dumped)
