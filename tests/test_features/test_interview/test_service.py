"""InterviewService 유닛 테스트"""

import asyncio
import importlib
import sys
import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage


def _install_dummy_langchain_openai():
    """테스트용 langchain_openai 더미 모듈 설치"""
    dummy_module = types.ModuleType("langchain_openai")

    class DummyChatOpenAI:  # pragma: no cover - 간단 더미
        def __init__(self, *args, **kwargs):
            pass

    dummy_module.ChatOpenAI = DummyChatOpenAI
    sys.modules.setdefault("langchain_openai", dummy_module)


_install_dummy_langchain_openai()

interview_service = importlib.import_module("features.interview.service")


class DummyStateSnapshot:
    """그래프 상태 스냅샷 더미"""

    def __init__(self, values):
        self.values = values


class DummyGraph:
    """그래프 더미 객체"""

    def __init__(self):
        self.invocations = []
        self.invoke_result = None
        self.invoke_error = None
        self.state_snapshot = None
        self.last_get_state_config = None
        self.update_state_calls = []

    async def ainvoke(self, state, config=None):
        self.invocations.append({"state": state, "config": config})
        if self.invoke_error is not None:
            raise self.invoke_error
        return self.invoke_result

    async def aget_state(self, config=None):
        self.last_get_state_config = config
        return self.state_snapshot

    async def aupdate_state(self, config, state):
        self.update_state_calls.append({"config": config, "state": state})
        if self.state_snapshot is None:
            self.state_snapshot = DummyStateSnapshot(values=dict(state))
        else:
            self.state_snapshot.values = {**self.state_snapshot.values, **state}


def _build_service(monkeypatch, dummy_graph):
    monkeypatch.setattr(
        interview_service,
        "build_graph",
        lambda _checkpointer=None, **_kwargs: dummy_graph,
    )
    monkeypatch.setattr(interview_service, "get_checkpointer", lambda: object())
    return interview_service.InterviewService()


def _completed_stage_4_progress() -> dict:
    return {
        "fixed_q_used": 3,
        "fixed_q_total": 3,
        "generated_q_used": 0,
        "generated_q_max": 0,
        "force_all_generated_q": False,
        "is_complete": True,
    }


@pytest.mark.asyncio
async def test_create_session_validation_error(monkeypatch):
    """필수 파라미터 누락 시 예외 발생 테스트"""
    dummy_graph = DummyGraph()
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="필수"):
        await service.create_session(user_id="", session_id="sid", experience_name="exp")


@pytest.mark.asyncio
async def test_create_session_returns_expected_payload(monkeypatch):
    """세션 생성 결과 포맷 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="첫 질문")],
        "current_stage": 1,
        "stage_progress": {"fixed_q_used": 1},
    }

    service = _build_service(monkeypatch, dummy_graph)

    result = await service.create_session(
        user_id="user_1",
        session_id="session_1",
        experience_name="프로젝트 A",
    )

    assert result["session_id"] == "session_1"
    assert result["first_question"] == "첫 질문"
    assert result["current_stage"] == 1
    assert result["stage_progress"]["fixed_q_used"] == 1

    assert len(dummy_graph.invocations) == 1
    invocation = dummy_graph.invocations[0]
    assert invocation["config"] == {"configurable": {"thread_id": "session_1"}}
    assert invocation["state"]["user_id"] == "user_1"
    assert invocation["state"]["session_id"] == "session_1"
    assert invocation["state"]["experience_name"] == "프로젝트 A"
    assert invocation["state"]["status"] == "completed"
    assert dummy_graph.update_state_calls[-1]["config"] == {
        "configurable": {"thread_id": "session_1"}
    }
    assert dummy_graph.update_state_calls[-1]["state"]["user_id"] == "user_1"
    assert dummy_graph.update_state_calls[-1]["state"]["session_id"] == "session_1"
    assert dummy_graph.update_state_calls[-1]["state"]["experience_name"] == "프로젝트 A"
    assert dummy_graph.update_state_calls[-1]["state"]["messages"] == [AIMessage(content="첫 질문")]
    assert dummy_graph.update_state_calls[-1]["state"]["current_stage"] == 1
    assert dummy_graph.update_state_calls[-1]["state"]["stage_progress"] == {"fixed_q_used": 1}
    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "completed"


@pytest.mark.asyncio
async def test_create_session_sets_failed_status_when_graph_raises(monkeypatch):
    """세션 생성 실패 시 fallback_state 기반 failed 상태를 저장한다."""
    dummy_graph = DummyGraph()
    dummy_graph.invoke_error = RuntimeError("boom")
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(RuntimeError, match="boom"):
        await service.create_session(
            user_id="user_1",
            session_id="session_1",
            experience_name="프로젝트 A",
        )

    assert dummy_graph.update_state_calls[-1]["config"] == {
        "configurable": {"thread_id": "session_1"}
    }
    assert dummy_graph.update_state_calls[-1]["state"]["user_id"] == "user_1"
    assert dummy_graph.update_state_calls[-1]["state"]["session_id"] == "session_1"
    assert dummy_graph.update_state_calls[-1]["state"]["experience_name"] == "프로젝트 A"
    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "failed"


@pytest.mark.asyncio
async def test_create_session_sets_failed_status_when_graph_is_cancelled(monkeypatch):
    """세션 생성 취소 시 failed 상태를 저장하고 취소를 재전파한다."""
    dummy_graph = DummyGraph()
    dummy_graph.invoke_error = asyncio.CancelledError()
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(asyncio.CancelledError):
        await service.create_session(
            user_id="user_1",
            session_id="session_1",
            experience_name="프로젝트 A",
        )

    assert dummy_graph.update_state_calls[-1]["state"]["status"] == "failed"


@pytest.mark.asyncio
async def test_create_session_returns_payload_even_if_completed_status_persist_fails(monkeypatch):
    """첫 질문 생성 성공 후 completed 상태 저장 실패는 결과 반환을 막지 않는다."""
    dummy_graph = DummyGraph()
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="첫 질문")],
        "current_stage": 1,
        "stage_progress": {"fixed_q_used": 1},
    }
    service = _build_service(monkeypatch, dummy_graph)

    async def _failing_set_session_status(
        session_id: str,
        status: str,
        fallback_state: dict | None = None,
    ) -> None:
        if status == "completed":
            raise RuntimeError("status persist failed")

    monkeypatch.setattr(service, "_set_session_status", _failing_set_session_status)

    result = await service.create_session(
        user_id="user_1",
        session_id="session_1",
        experience_name="프로젝트 A",
    )

    assert result == {
        "session_id": "session_1",
        "first_question": "첫 질문",
        "current_stage": 1,
        "stage_progress": {"fixed_q_used": 1},
    }


@pytest.mark.asyncio
async def test_process_message_session_not_found(monkeypatch):
    """세션이 없을 때 예외 발생 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = None
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="세션을 찾을 수 없습니다"):
        await service.process_message(session_id="missing", message="안녕하세요")


@pytest.mark.asyncio
async def test_process_message_with_files(monkeypatch):
    """파일 payload 포함 메시지 처리 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(values={"session_id": "session_1"})
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="응답")],
        "current_stage": 2,
        "stage_progress": {"fixed_q_used": 2},
        "overall_completion_percentage": 40.0,
        "all_stages_complete": False,
    }

    service = _build_service(monkeypatch, dummy_graph)

    result = await service.process_message(
        session_id="session_1",
        message="답변입니다.",
        files=[
            {
                "filename": "portfolio.pdf",
                "content_type": "application/pdf",
                "temp_path": "/tmp/portfolio.pdf",
            },
            {
                "filename": "image.png",
                "content_type": "image/png",
                "temp_path": "/tmp/image.png",
            },
        ],
    )

    assert result["ai_response"] == "응답"
    assert result["current_stage"] == 2
    assert result["stage_progress"]["fixed_q_used"] == 2
    assert result["overall_completion"] == 40.0
    assert result["all_complete"] is False
    assert result["is_extended_mode"] is False
    assert result["extension_turns_used"] is None
    assert result["extension_turns_max"] is None

    invocation = dummy_graph.invocations[0]
    assert invocation["config"] == {"configurable": {"thread_id": "session_1"}}
    assert isinstance(invocation["state"]["messages"][0], HumanMessage)
    assert invocation["state"]["messages"][0].content == "답변입니다."
    assert invocation["state"]["current_turn_files"] == [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/portfolio.pdf",
        },
        {
            "filename": "image.png",
            "content_type": "image/png",
            "temp_path": "/tmp/image.png",
        },
    ]
    assert invocation["state"]["file_contexts"] == []


@pytest.mark.asyncio
async def test_process_message_accepts_file_only_request(monkeypatch):
    """file-only 요청이면 빈 HumanMessage를 주입해 기존 graph 흐름을 유지한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(values={"session_id": "session_1"})
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="응답")],
        "current_stage": 2,
        "stage_progress": {"fixed_q_used": 2},
        "overall_completion_percentage": 40.0,
        "all_stages_complete": False,
    }

    service = _build_service(monkeypatch, dummy_graph)

    await service.process_message(
        session_id="session_1",
        message=None,
        files=[
            {
                "filename": "portfolio.pdf",
                "content_type": "application/pdf",
                "temp_path": "/tmp/portfolio.pdf",
            }
        ],
    )

    invocation = dummy_graph.invocations[0]
    assert isinstance(invocation["state"]["messages"][0], HumanMessage)
    assert invocation["state"]["messages"][0].content == ""
    assert invocation["state"]["current_turn_files"] == [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/portfolio.pdf",
        }
    ]
    assert invocation["state"]["file_contexts"] == []


@pytest.mark.asyncio
async def test_process_message_returns_none_when_all_complete(monkeypatch):
    """모든 단계 완료 상태면 ai_response를 None으로 반환한다."""
    dummy_graph = DummyGraph()
    previous_messages = [AIMessage(content="직전 질문")]
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "messages": previous_messages,
            "all_stages_complete": False,
            "is_extended_mode": False,
            "extension_count": 0,
        }
    )
    dummy_graph.invoke_result = {
        "messages": [*previous_messages, HumanMessage(content="사용자 최종 답변")],
        "current_stage": 4,
        "stage_progress": {"fixed_q_used": 3},
        "overall_completion_percentage": 100.0,
        "all_stages_complete": True,
    }

    service = _build_service(monkeypatch, dummy_graph)

    result = await service.process_message(
        session_id="session_1",
        message="마지막 답변입니다.",
    )

    assert result["ai_response"] is None
    assert result["all_complete"] is True
    assert result["is_extended_mode"] is False
    invocation = dummy_graph.invocations[0]
    assert invocation["state"]["current_turn_files"] == []
    assert invocation["state"]["file_contexts"] == []


@pytest.mark.asyncio
async def test_process_message_returns_immediate_extension_question_after_stage_four_completion(
    monkeypatch,
):
    """정규 마지막 답변 처리 결과로 생성된 첫 연장 질문을 반환한다."""
    dummy_graph = DummyGraph()
    previous_messages = [
        AIMessage(content="정규 마지막 질문"),
    ]
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "messages": previous_messages,
            "all_stages_complete": False,
            "is_extended_mode": False,
            "extension_count": 0,
        }
    )
    dummy_graph.invoke_result = {
        "messages": [
            *previous_messages,
            HumanMessage(content="정규 마지막 답변입니다."),
            AIMessage(content="연장 첫 질문"),
        ],
        "current_stage": 4,
        "stage_progress": {"fixed_q_used": 3},
        "overall_completion_percentage": 100.0,
        "all_stages_complete": False,
        "is_extended_mode": True,
        "extension_count": 1,
        "extension_turns_used": 1,
        "extension_turns_max": 18,
    }
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.process_message(
        session_id="session_1",
        message="정규 마지막 답변입니다.",
    )

    assert result["ai_response"] == "연장 첫 질문"
    assert result["all_complete"] is False
    assert result["is_extended_mode"] is True
    assert result["extension_turns_used"] == 1
    assert result["extension_turns_max"] == 18

    invocation = dummy_graph.invocations[0]
    assert "is_extended_mode" not in invocation["state"]
    assert invocation["state"]["messages"] == [HumanMessage(content="정규 마지막 답변입니다.")]


@pytest.mark.asyncio
async def test_process_message_auto_starts_extension_for_completed_legacy_session(monkeypatch):
    """이미 완료 상태로 저장된 기존 세션도 후속 채팅으로 연장 모드에 진입한다."""
    dummy_graph = DummyGraph()
    previous_messages = [
        AIMessage(content="정규 마지막 질문"),
        HumanMessage(content="정규 마지막 답변"),
    ]
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "messages": previous_messages,
            "all_stages_complete": True,
            "is_extended_mode": False,
            "extension_count": 0,
            "current_stage": 4,
            "stage_progress": _completed_stage_4_progress(),
        }
    )
    dummy_graph.invoke_result = {
        "messages": [
            *previous_messages,
            HumanMessage(content="추가로 정리하고 싶은 내용입니다."),
            AIMessage(content="연장 질문"),
        ],
        "current_stage": 4,
        "stage_progress": {"fixed_q_used": 3},
        "overall_completion_percentage": 100.0,
        "all_stages_complete": False,
        "is_extended_mode": True,
        "extension_count": 1,
        "extension_turns_used": 1,
        "extension_turns_max": 18,
    }
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.process_message(
        session_id="session_1",
        message="추가로 정리하고 싶은 내용입니다.",
    )

    assert result["ai_response"] == "연장 질문"
    assert result["all_complete"] is False
    assert result["is_extended_mode"] is True
    assert result["extension_turns_used"] == 1
    assert result["extension_turns_max"] == 18

    invocation = dummy_graph.invocations[0]
    assert invocation["state"]["is_extended_mode"] is True
    assert invocation["state"]["all_stages_complete"] is False
    assert invocation["state"]["extension_count"] == 1
    assert invocation["state"]["extension_turns_used"] == 0
    assert invocation["state"]["extension_turns_max"] == 18
    assert invocation["state"]["additional_question_target_statuses"] == {}
    assert invocation["state"]["additional_question_pre_evaluated"] is False
    assert invocation["state"]["current_additional_question_target_id"] is None
    assert invocation["state"]["messages"] == [
        HumanMessage(content="추가로 정리하고 싶은 내용입니다.")
    ]


@pytest.mark.asyncio
async def test_process_message_does_not_auto_start_extension_before_stage_four(monkeypatch):
    """완료 플래그가 잘못 켜져 있어도 4단계 완료 전이면 자동 연장으로 넘기지 않는다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "messages": [AIMessage(content="3단계 마지막 질문")],
            "all_stages_complete": True,
            "is_extended_mode": False,
            "extension_count": 0,
            "current_stage": 3,
            "stage_progress": {
                "fixed_q_used": 3,
                "fixed_q_total": 3,
                "generated_q_used": 0,
                "generated_q_max": 0,
                "force_all_generated_q": False,
                "is_complete": True,
            },
        }
    )
    dummy_graph.invoke_result = {
        "messages": [
            AIMessage(content="3단계 마지막 질문"),
            HumanMessage(content="3단계 마지막 답변"),
            AIMessage(content="4단계 첫 질문"),
        ],
        "current_stage": 4,
        "stage_progress": {
            "fixed_q_used": 1,
            "fixed_q_total": 3,
            "generated_q_used": 0,
            "generated_q_max": 0,
            "force_all_generated_q": False,
            "is_complete": False,
        },
        "overall_completion_percentage": 0.0,
        "all_stages_complete": False,
        "is_extended_mode": False,
        "extension_turns_used": 0,
        "extension_turns_max": 18,
    }
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.process_message(
        session_id="session_1",
        message="3단계 마지막 답변",
    )

    assert result["current_stage"] == 4
    assert result["is_extended_mode"] is False
    assert result["ai_response"] == "4단계 첫 질문"
    invocation = dummy_graph.invocations[0]
    assert "is_extended_mode" not in invocation["state"]
    assert "extension_count" not in invocation["state"]


@pytest.mark.asyncio
async def test_process_message_resets_current_turn_files_when_no_files(monkeypatch):
    """파일이 없는 턴에도 current_turn_files를 빈 리스트로 초기화한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={"session_id": "session_1", "current_turn_files": ["old_file"]}
    )
    dummy_graph.invoke_result = {
        "messages": [AIMessage(content="응답")],
        "current_stage": 2,
        "stage_progress": {"fixed_q_used": 2},
        "overall_completion_percentage": 40.0,
        "all_stages_complete": False,
    }

    service = _build_service(monkeypatch, dummy_graph)

    await service.process_message(
        session_id="session_1",
        message="파일 없는 답변입니다.",
    )

    invocation = dummy_graph.invocations[0]
    assert invocation["state"]["current_turn_files"] == []
    assert invocation["state"]["file_contexts"] == []


@pytest.mark.asyncio
async def test_extend_session_success(monkeypatch):
    """완료된 세션은 연장 모드로 전환되고 첫 연장 질문을 반환한다."""
    dummy_graph = DummyGraph()
    previous_messages = [AIMessage(content="정규 마지막 질문")]
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "messages": previous_messages,
            "all_stages_complete": True,
            "current_stage": 4,
            "stage_progress": _completed_stage_4_progress(),
            "extension_count": 0,
        }
    )
    dummy_graph.invoke_result = {
        "messages": [*previous_messages, AIMessage(content="연장 첫 질문")],
        "extension_count": 1,
        "extension_turns_max": 18,
    }
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.extend_session("session_1")

    assert result["ai_response"] == "연장 첫 질문"
    assert result["extension_count"] == 1
    assert result["extension_turns_max"] == 18

    invocation = dummy_graph.invocations[0]
    assert invocation["state"]["is_extended_mode"] is True
    assert invocation["state"]["all_stages_complete"] is False
    assert invocation["state"]["extension_count"] == 1
    assert invocation["state"]["extension_turns_used"] == 0
    assert invocation["state"]["extension_turns_max"] == 18
    assert invocation["state"]["additional_question_target_statuses"] == {}
    assert invocation["state"]["additional_question_pre_evaluated"] is False
    assert invocation["state"]["current_additional_question_target_id"] is None
    assert invocation["state"]["current_turn_files"] == []
    assert invocation["state"]["file_contexts"] == []
    assert invocation["state"]["mentioned_insight"] is None


@pytest.mark.asyncio
async def test_extend_session_raises_when_no_new_ai_response(monkeypatch):
    """연장 실행 결과에 새 AI 메시지가 없으면 과거 질문을 재사용하지 않고 실패한다."""
    dummy_graph = DummyGraph()
    previous_messages = [AIMessage(content="정규 마지막 질문")]
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "messages": previous_messages,
            "all_stages_complete": True,
            "current_stage": 4,
            "stage_progress": _completed_stage_4_progress(),
            "extension_count": 0,
        }
    )
    dummy_graph.invoke_result = {
        "messages": previous_messages,
        "extension_count": 1,
        "extension_turns_max": 18,
    }
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="연장 질문을 생성하지 못했습니다"):
        await service.extend_session("session_1")


@pytest.mark.asyncio
async def test_extend_session_raises_when_not_completed(monkeypatch):
    """완료되지 않은 세션은 연장할 수 없다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "all_stages_complete": False,
            "extension_count": 0,
        }
    )
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="4단계 정규 인터뷰 완료"):
        await service.extend_session("session_1")


@pytest.mark.asyncio
async def test_extend_session_raises_when_completion_flag_is_set_before_stage_four(monkeypatch):
    """완료 플래그가 잘못 켜진 3단계 상태는 명시적 연장도 차단한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "all_stages_complete": True,
            "current_stage": 3,
            "stage_progress": {
                "fixed_q_used": 3,
                "fixed_q_total": 3,
                "generated_q_used": 0,
                "generated_q_max": 0,
                "force_all_generated_q": False,
                "is_complete": True,
            },
            "extension_count": 0,
        }
    )
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="4단계 정규 인터뷰 완료"):
        await service.extend_session("session_1")


@pytest.mark.asyncio
async def test_extend_session_raises_when_max_extensions_reached(monkeypatch):
    """최대 연장 횟수에 도달하면 연장을 차단한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "all_stages_complete": True,
            "current_stage": 4,
            "stage_progress": _completed_stage_4_progress(),
            "extension_count": 1,
        }
    )
    monkeypatch.setattr(
        interview_service,
        "get_global_config",
        lambda: type(
            "Config",
            (),
            {
                "max_extensions": 1,
                "extension_turns_per_session": 18,
            },
        )(),
    )
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="최대 연장 횟수"):
        await service.extend_session("session_1")


@pytest.mark.asyncio
async def test_complete_extended_session_updates_state(monkeypatch):
    """연장 모드 세션을 포트폴리오 생성 가능한 완료 상태로 전환한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "all_stages_complete": False,
            "is_extended_mode": True,
            "pending_extended_end_guide": True,
        }
    )
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.complete_extended_session("session_1")

    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False
    assert result["pending_extended_end_guide"] is False
    assert result["current_additional_question_target_id"] is None
    assert dummy_graph.update_state_calls[-1]["config"] == {
        "configurable": {"thread_id": "session_1"}
    }
    assert dummy_graph.update_state_calls[-1]["state"] == {
        "all_stages_complete": True,
        "is_extended_mode": False,
        "pending_extended_end_guide": False,
        "current_additional_question_target_id": None,
        "current_turn_files": [],
        "file_contexts": [],
        "mentioned_insight": None,
        "status": "completed",
    }


@pytest.mark.asyncio
async def test_complete_extended_session_returns_completed_state_without_update(monkeypatch):
    """이미 완료된 세션이면 추가 상태 업데이트 없이 현재 상태를 반환한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "all_stages_complete": True,
            "is_extended_mode": False,
        }
    )
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.complete_extended_session("session_1")

    assert result["all_stages_complete"] is True
    assert result["is_extended_mode"] is False
    assert dummy_graph.update_state_calls == []


@pytest.mark.asyncio
async def test_complete_extended_session_raises_when_not_extended_or_complete(monkeypatch):
    """미완료 일반 세션은 연장 완료 처리 대상이 아니다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "all_stages_complete": False,
            "is_extended_mode": False,
        }
    )
    service = _build_service(monkeypatch, dummy_graph)

    with pytest.raises(ValueError, match="연장 모드가 아닌 세션"):
        await service.complete_extended_session("session_1")


@pytest.mark.asyncio
async def test_get_session_state_returns_none_on_empty_snapshot(monkeypatch):
    """스냅샷이 없거나 비어있을 때 None 반환 테스트"""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(values={})
    service = _build_service(monkeypatch, dummy_graph)

    assert await service.get_session_state("session_1") is None


@pytest.mark.asyncio
async def test_get_session_state_returns_values(monkeypatch):
    """스냅샷 값 반환 시 신규 기본 필드를 보강한다."""
    dummy_graph = DummyGraph()
    expected_state = {
        "session_id": "session_1",
        "current_stage": 1,
        "messages": [HumanMessage(content="답변")],
    }
    dummy_graph.state_snapshot = DummyStateSnapshot(values=expected_state)
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.get_session_state("session_1")

    assert result is not None
    assert result["session_id"] == "session_1"
    assert result["turn_number"] == 1
    assert result["status"] == "completed"
    assert result["retrieved_insights"] == []
    assert result["mentioned_insight"] is None
    assert result["insight_turn_history"] == []
    assert result["current_turn_files"] == []
    assert result["file_contexts"] == []


@pytest.mark.asyncio
async def test_get_session_status_returns_compact_payload(monkeypatch):
    """세션 경량 상태 조회 시 status와 핵심 필드만 반환한다."""
    dummy_graph = DummyGraph()
    dummy_graph.state_snapshot = DummyStateSnapshot(
        values={
            "session_id": "session_1",
            "current_stage": 2,
            "all_stages_complete": False,
        }
    )
    service = _build_service(monkeypatch, dummy_graph)

    result = await service.get_session_status("session_1")

    assert result == {
        "session_id": "session_1",
        "status": "completed",
        "current_stage": 2,
        "all_complete": False,
    }


def test_singleton_get_and_reset(monkeypatch):
    """싱글톤 생성 및 초기화 테스트"""
    dummy_graph = DummyGraph()
    monkeypatch.setattr(
        interview_service,
        "build_graph",
        lambda _checkpointer=None, **_kwargs: dummy_graph,
    )
    monkeypatch.setattr(interview_service, "get_checkpointer", lambda: object())

    interview_service.reset_interview_service()
    first = interview_service.get_interview_service()
    second = interview_service.get_interview_service()

    assert first is second

    interview_service.reset_interview_service()
    third = interview_service.get_interview_service()
    assert first is not third
