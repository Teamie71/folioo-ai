"""FileProcessor 노드 테스트"""

import base64
import importlib
import sys
import types

from langchain_core.messages import AIMessage


def _install_dummy_langchain_openai():
    """테스트용 langchain_openai 더미 모듈 설치"""
    dummy_module = types.ModuleType("langchain_openai")

    class DummyChatOpenAI:  # pragma: no cover - 간단 더미
        def __init__(self, *args, **kwargs):
            pass

    dummy_module.ChatOpenAI = DummyChatOpenAI
    sys.modules.setdefault("langchain_openai", dummy_module)


_install_dummy_langchain_openai()

llm_client = importlib.import_module("common.llm.client")
file_processor = importlib.import_module("features.interview.agents.nodes.file_processor")
get_initial_interview_state = importlib.import_module(
    "features.interview.agents.state"
).get_initial_interview_state


class _CaptureVisionLLM:
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.invocations: list[list] = []

    def invoke(self, messages):
        self.invocations.append(messages)
        return AIMessage(content=self.response_text)


def test_file_processor_run_reads_temp_path_and_clears_current_turn_files(tmp_path, monkeypatch):
    """temp_path에서 파일을 읽어 file_contexts를 만들고 stale ref를 제거한다."""
    file_path = tmp_path / "portfolio.pdf"
    file_bytes = b"%PDF-1.4\nportfolio"
    file_path.write_bytes(file_bytes)
    fake_llm = _CaptureVisionLLM("추출된 PDF 텍스트")
    monkeypatch.setattr(file_processor, "get_file_processor_llm", lambda: fake_llm)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": str(file_path),
        }
    ]

    result = file_processor.run(state)

    assert result["next_node"] == "retriever"
    assert result["current_turn_files"] == []
    assert result["file_contexts"] == ["[파일: portfolio.pdf]\n추출된 PDF 텍스트"]

    human_content = fake_llm.invocations[0][1].content
    assert human_content[1]["type"] == "file"
    assert human_content[1]["file"]["filename"] == "portfolio.pdf"
    assert human_content[1]["file"]["file_data"] == (
        "data:application/pdf;base64," + base64.b64encode(file_bytes).decode("utf-8")
    )


def test_file_processor_run_extracts_image_text(tmp_path, monkeypatch):
    """이미지 파일은 Vision LLM의 image_url 입력으로 전달한다."""
    file_path = tmp_path / "architecture.png"
    file_bytes = b"png-binary-data"
    file_path.write_bytes(file_bytes)
    fake_llm = _CaptureVisionLLM("추출된 이미지 텍스트")
    monkeypatch.setattr(file_processor, "get_file_processor_llm", lambda: fake_llm)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "architecture.png",
            "content_type": "image/png",
            "temp_path": str(file_path),
        }
    ]

    result = file_processor.run(state)

    assert result["file_contexts"] == ["[파일: architecture.png]\n추출된 이미지 텍스트"]
    assert result["current_turn_files"] == []

    human_content = fake_llm.invocations[0][1].content
    assert human_content[1]["type"] == "image_url"
    assert human_content[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(file_bytes).decode("utf-8")
    )


def test_file_processor_run_continues_when_one_file_fails(tmp_path, monkeypatch):
    """파일 하나가 실패해도 나머지 파일은 계속 처리한다."""
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"png-bytes")

    def _extract_file_text(file_payload):
        if file_payload["filename"] == "broken.pdf":
            raise ValueError("임시 파일을 읽을 수 없습니다.")
        return "이미지 설명 텍스트"

    monkeypatch.setattr(file_processor, "_extract_file_text", _extract_file_text)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "broken.pdf",
            "content_type": "application/pdf",
            "temp_path": str(tmp_path / "missing.pdf"),
        },
        {
            "filename": "image.png",
            "content_type": "image/png",
            "temp_path": str(image_path),
        },
    ]

    result = file_processor.run(state)

    assert result["current_turn_files"] == []
    assert result["next_node"] == "retriever"
    assert result["file_contexts"] == [
        "[파일: broken.pdf]\n파일 처리 실패",
        "[파일: image.png]\n이미지 설명 텍스트",
    ]


def test_file_processor_run_does_not_expose_raw_exception_message(monkeypatch):
    """파일 처리 실패 시 내부 예외 문자열을 file_contexts에 노출하지 않는다."""

    def _extract_file_text(_file_payload):
        raise RuntimeError("request_id=req-123 temp_path=/tmp/internal.pdf")

    monkeypatch.setattr(file_processor, "_extract_file_text", _extract_file_text)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "broken.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/broken.pdf",
        }
    ]

    result = file_processor.run(state)

    assert result["file_contexts"] == ["[파일: broken.pdf]\n파일 처리 실패"]


def test_file_processor_run_returns_empty_contexts_when_no_files():
    """업로드 파일이 없으면 빈 file_contexts를 반환한다."""
    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )

    result = file_processor.run(state)

    assert result["file_contexts"] == []
    assert result["current_turn_files"] == []
    assert result["next_node"] == "retriever"


def test_file_processor_run_truncates_long_single_file_context(monkeypatch):
    """파일별 컨텍스트 길이 상한을 넘기면 잘라서 저장한다."""
    monkeypatch.setattr(file_processor, "_MAX_FILE_CONTEXT_CHARS_PER_FILE", 40)
    monkeypatch.setattr(file_processor, "_MAX_FILE_CONTEXT_CHARS_TOTAL", 200)
    monkeypatch.setattr(file_processor, "_PER_FILE_TRUNCATION_NOTICE", "...(생략)")
    monkeypatch.setattr(file_processor, "_TOTAL_TRUNCATION_NOTICE", "...(전체생략)")
    monkeypatch.setattr(file_processor, "_extract_file_text", lambda _payload: "a" * 200)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "portfolio.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/portfolio.pdf",
        }
    ]

    result = file_processor.run(state)

    assert len(result["file_contexts"]) == 1
    assert result["file_contexts"][0].endswith("...(생략)")
    assert len(result["file_contexts"][0]) == 40


def test_file_processor_run_applies_total_context_limit(monkeypatch):
    """여러 파일의 총합 길이가 상한을 넘기면 뒤 컨텍스트를 잘라서 중단한다."""
    monkeypatch.setattr(file_processor, "_MAX_FILE_CONTEXT_CHARS_PER_FILE", 200)
    monkeypatch.setattr(file_processor, "_MAX_FILE_CONTEXT_CHARS_TOTAL", 60)
    monkeypatch.setattr(file_processor, "_PER_FILE_TRUNCATION_NOTICE", "...(생략)")
    monkeypatch.setattr(file_processor, "_TOTAL_TRUNCATION_NOTICE", "...(전체생략)")

    def _extract_file_text(file_payload):
        return f"내용-{file_payload['filename']}-" + ("a" * 40)

    monkeypatch.setattr(file_processor, "_extract_file_text", _extract_file_text)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "first.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/first.pdf",
        },
        {
            "filename": "second.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/second.pdf",
        },
    ]

    result = file_processor.run(state)

    assert len(result["file_contexts"]) == 1
    assert result["file_contexts"][0].endswith("...(전체생략)")
    assert len(result["file_contexts"][0]) == 60


def test_file_processor_run_marks_last_context_when_total_budget_exactly_exhausted(monkeypatch):
    """총량을 정확히 소진해도 뒤 파일이 남아 있으면 마지막 컨텍스트에 전체 생략 notice를 남긴다."""
    monkeypatch.setattr(file_processor, "_MAX_FILE_CONTEXT_CHARS_PER_FILE", 200)
    monkeypatch.setattr(file_processor, "_MAX_FILE_CONTEXT_CHARS_TOTAL", 25)
    monkeypatch.setattr(file_processor, "_PER_FILE_TRUNCATION_NOTICE", "...(생략)")
    monkeypatch.setattr(file_processor, "_TOTAL_TRUNCATION_NOTICE", "...(전체생략)")

    def _extract_file_text(file_payload):
        if file_payload["filename"] == "first.pdf":
            return "a" * 9
        return "b" * 30

    monkeypatch.setattr(file_processor, "_extract_file_text", _extract_file_text)

    state = get_initial_interview_state(
        user_id="test_user",
        session_id="test_session",
        experience_name="테스트 경험",
    )
    state["current_turn_files"] = [
        {
            "filename": "first.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/first.pdf",
        },
        {
            "filename": "second.pdf",
            "content_type": "application/pdf",
            "temp_path": "/tmp/second.pdf",
        },
    ]

    result = file_processor.run(state)

    assert len(result["file_contexts"]) == 1
    assert result["file_contexts"][0].endswith("...(전체생략)")
    assert len(result["file_contexts"][0]) <= 25


def test_get_file_processor_llm_uses_dedicated_configuration(monkeypatch):
    """FileProcessor 전용 LLM helper는 Vision 추출용 설정을 사용한다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test")
    monkeypatch.setenv("FILE_PROCESSOR_MODEL_NAME", "google/gemini-test")
    llm_client.get_file_processor_llm.cache_clear()

    captured = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeChatOpenAI)

    result = llm_client.get_file_processor_llm()

    assert isinstance(result, _FakeChatOpenAI)
    assert captured["model"] == "google/gemini-test"
    assert captured["temperature"] == 0.0
    assert captured["request_timeout"] == 120
    assert captured["disable_streaming"] is True
    assert captured["max_retries"] == 0
    # max_tokens를 안 정하면 provider 기본값(예: 65536)을 그대로 요청해,
    # 계정 잔여 크레딧이 그 최대치를 못 감당하면 402로 통째로 거부된다.
    assert captured["max_tokens"] == llm_client.FILE_PROCESSOR_MAX_TOKENS
    llm_client.get_file_processor_llm.cache_clear()


def test_get_file_processor_llm_uncached_returns_new_instances(monkeypatch):
    """캐시 없는 FileProcessor LLM helper는 호출마다 새 클라이언트를 만든다."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test")
    monkeypatch.setenv("FILE_PROCESSOR_MODEL_NAME", "google/gemini-test")

    captured: list[dict[str, object]] = []

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(llm_client, "ChatOpenAI", _FakeChatOpenAI)

    first = llm_client.get_file_processor_llm_uncached()
    second = llm_client.get_file_processor_llm_uncached()

    assert isinstance(first, _FakeChatOpenAI)
    assert isinstance(second, _FakeChatOpenAI)
    assert first is not second
    assert [item["model"] for item in captured] == ["google/gemini-test", "google/gemini-test"]
