"""파일처리 노드 테스트 (에이전트 문서 5-2)"""

import asyncio
import base64
import io
import zipfile

import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from features.experience_map import extractors
from features.experience_map.errors import LlmError
from features.experience_map.extractors import (
    PAGE_TRUNCATION_NOTE_MARKER,
    TRUNCATION_NOTE,
    FileUnreadableError,
    extract,
    extractor_kind,
)
from features.experience_map.nodes import file_processor as node_module
from features.experience_map.nodes.file_processor import (
    cleanup_extracted_files,
    next_node,
    process_files,
)
from features.experience_map.state import start_turn
from features.experience_map.upload_store import UploadStore

SESSION_ID = "d9428888-122b-11e1-b85c-61cd3cbb3210"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def reference(file_id: str, filename: str, content_type: str) -> dict:
    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "file_size": 10,
        "sha256": f"{file_id}-hash",
        "gcs_object": f"expmap/1/req/{file_id}",
    }


def make_state(**overrides):
    state = start_turn(
        {"user_id": "1", "session_id": SESSION_ID},
        request_id=REQUEST_ID,
        request_hash="a" * 64,
        user_message=None,
    )
    state.update(overrides)
    return state


class FakeObjectStore:
    """GCS 대역"""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.deleted: list[str] = []

    async def upload(self, object_name: str, data: bytes, content_type: str) -> None:
        self.objects[object_name] = data

    async def download(self, object_name: str) -> bytes:
        return self.objects[object_name]

    async def delete(self, object_name: str) -> None:
        self.deleted.append(object_name)
        self.objects.pop(object_name, None)

    async def list_names(self, prefix: str) -> list[str]:
        return [n for n in self.objects if n.startswith(prefix)]

    async def created_at(self, object_name: str):
        return None


@pytest.fixture
def store(monkeypatch) -> FakeObjectStore:
    """UploadStore 를 대역 GCS 로 바꾼다."""
    backing = FakeObjectStore()
    upload_store = UploadStore(backing, file_ttl_seconds=3600)
    monkeypatch.setattr(node_module, "get_upload_store", lambda: upload_store)
    return backing


@pytest.fixture
def fake_extract(monkeypatch):
    """추출기를 대역으로 바꾼다. 파일별 결과(또는 예외)를 정한다."""

    def _set(results: dict[str, str | Exception]):
        calls: list[str] = []

        async def _extract(data: bytes, filename: str, content_type: str) -> str:
            calls.append(filename)
            outcome = results[filename]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(node_module, "extract", _extract)
        return calls

    return _set


# ===== 순서 =====


@pytest.mark.asyncio
async def test_mixed_formats_keep_input_order(store, fake_extract):
    """파서 형식과 OCR 형식이 섞여도 올린 순서를 지킨다."""
    refs = [
        reference("f_1", "메모.txt", "text/plain"),
        reference("f_2", "포트폴리오.pdf", "application/pdf"),
        reference(
            "f_3",
            "발표.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ]
    for ref in refs:
        store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"메모.txt": "첫째", "포트폴리오.pdf": "둘째", "발표.pptx": "셋째"})

    result = await process_files(make_state(file_references=refs))

    assert result["extracted_text"].index("첫째") < result["extracted_text"].index("둘째")
    assert result["extracted_text"].index("둘째") < result["extracted_text"].index("셋째")
    assert [item["file_id"] for item in result["extracted_files"]] == ["f_1", "f_2", "f_3"]


@pytest.mark.asyncio
async def test_page_truncation_note_sets_file_content_truncated_flag(store, fake_extract):
    """추출기가 남긴 페이지 상한 안내를 file_processor가 상태 플래그로 받는다.

    실제로 지적된 문제다 — PDF 페이지 상한(MAX_PDF_PAGES)에 걸려 뒤쪽이
    잘려도 사용자 응답에는 아무 표시가 없었다. extractors가 결과 텍스트에
    남기는 안내 문구를 이 노드가 감지해 `file_content_truncated`를 세워야
    이후 result_response가 최종 응답에 반영할 수 있다.
    """
    ref = reference("f_1", "긴문서.pdf", "application/pdf")
    store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"긴문서.pdf": f"내용{PAGE_TRUNCATION_NOTE_MARKER} 앞 10페이지만 사용했습니다.]"})

    result = await process_files(make_state(file_references=[ref]))

    assert result["file_content_truncated"] is True


@pytest.mark.asyncio
async def test_char_truncation_note_sets_file_content_truncated_flag(store, fake_extract):
    """글자 수 상한 안내(TRUNCATION_NOTE)도 같은 플래그를 세운다."""
    ref = reference("f_1", "긴문서.pdf", "application/pdf")
    store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"긴문서.pdf": f"내용{TRUNCATION_NOTE}"})

    result = await process_files(make_state(file_references=[ref]))

    assert result["file_content_truncated"] is True


@pytest.mark.asyncio
async def test_untruncated_content_leaves_flag_false(store, fake_extract):
    """상한에 안 걸리면 플래그가 안 세워진다."""
    ref = reference("f_1", "짧은문서.pdf", "application/pdf")
    store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"짧은문서.pdf": "내용"})

    result = await process_files(make_state(file_references=[ref]))

    assert result["file_content_truncated"] is False


@pytest.mark.asyncio
async def test_source_hash_and_extractor_are_recorded(store, fake_extract):
    refs = [
        reference("f_1", "메모.txt", "text/plain"),
        reference("f_2", "사진.png", "image/png"),
    ]
    for ref in refs:
        store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"메모.txt": "글", "사진.png": "그림 속 글자"})

    result = await process_files(make_state(file_references=refs))

    assert result["extracted_files"][0]["source_hash"] == "f_1-hash"
    assert result["extracted_files"][0]["extractor"] == "parser"
    assert result["extracted_files"][1]["extractor"] == "ocr"


# ===== 원본 삭제와 재시도 =====


@pytest.mark.asyncio
async def test_original_is_deleted_only_after_checkpoint_cleanup_node(store, fake_extract):
    """추출 state를 반환한 뒤 별도 cleanup 단계에서만 원본을 지운다."""
    ref = reference("f_1", "메모.txt", "text/plain")
    store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"메모.txt": "글"})

    extracted = await process_files(make_state(file_references=[ref]))

    assert store.deleted == []
    await cleanup_extracted_files(extracted)
    assert ref["gcs_object"] in store.deleted


@pytest.mark.asyncio
async def test_already_extracted_file_is_skipped(store, fake_extract):
    """재시도할 때 원본이 없어도 이어서 간다."""
    ref = reference("f_1", "메모.txt", "text/plain")
    # 원본은 이미 지워졌다 — store 에 없다.
    calls = fake_extract({})
    state = make_state(
        file_references=[ref],
        extracted_files=[
            {"file_id": "f_1", "text": "이미 뽑은 글", "source_hash": "h", "extractor": "parser"}
        ],
    )

    result = await process_files(state)

    assert calls == []  # 다시 뽑지 않았다
    assert "이미 뽑은 글" in result["extracted_text"]


@pytest.mark.asyncio
async def test_system_error_keeps_original(store, fake_extract):
    """시스템 오류면 원본을 남긴다. 다른 worker 가 재시도해야 한다."""
    ref = reference("f_1", "포트폴리오.pdf", "application/pdf")
    store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"포트폴리오.pdf": TimeoutError("upstream")})

    with pytest.raises(LlmError) as exc_info:
        await process_files(make_state(file_references=[ref]))

    assert exc_info.value.failed_node == "file_processor"
    assert store.deleted == []
    assert ref["gcs_object"] in store.objects


# ===== 두 실패의 구분 =====


@pytest.mark.asyncio
async def test_unreadable_file_goes_to_fallback_not_failure(store, fake_extract):
    """품질 문제는 노드 실패가 아니다. 재시도 버튼을 줘도 같은 결과다."""
    ref = reference("f_1", "손상.pdf", "application/pdf")
    store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"손상.pdf": FileUnreadableError("읽을 수 없음")})

    result = await process_files(make_state(file_references=[ref]))

    assert result["fallback_reason"] == "file_unreadable"
    assert next_node(result) == "fallback"
    assert result["extracted_text"] is None


@pytest.mark.asyncio
async def test_partial_failure_keeps_readable_files(store, fake_extract):
    """일부만 못 읽으면 읽은 것으로 계속 간다."""
    refs = [
        reference("f_1", "정상.txt", "text/plain"),
        reference("f_2", "손상.pdf", "application/pdf"),
    ]
    for ref in refs:
        store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"정상.txt": "읽힌 내용", "손상.pdf": FileUnreadableError("x")})

    result = await process_files(make_state(file_references=refs))

    assert result.get("fallback_reason") is None
    assert "읽힌 내용" in result["extracted_text"]
    assert next_node(result) == "content_filter"


@pytest.mark.asyncio
async def test_no_files_is_noop(store, fake_extract):
    fake_extract({})

    result = await process_files(make_state())

    assert result["extracted_text"] is None
    assert result["current_node"] == "file_processor"


# ===== 길이 제한 =====


@pytest.mark.asyncio
async def test_total_length_cap_drops_later_files(store, fake_extract, monkeypatch):
    """전체 상한을 넘으면 뒤쪽을 버린다. 앞에 올린 파일이 대개 더 중요하다."""
    monkeypatch.setattr(node_module, "MAX_TOTAL_TEXT_CHARS", 10)
    refs = [
        reference("f_1", "첫째.txt", "text/plain"),
        reference("f_2", "둘째.txt", "text/plain"),
    ]
    for ref in refs:
        store.objects[ref["gcs_object"]] = b"x"
    fake_extract({"첫째.txt": "가" * 8, "둘째.txt": "나" * 8})

    result = await process_files(make_state(file_references=refs))

    assert "가" in result["extracted_text"]
    assert "나" not in result["extracted_text"]


def test_single_file_is_truncated():
    long_text = "가" * (extractors.MAX_FILE_TEXT_CHARS + 100)

    truncated = extractors._truncate(long_text)

    assert len(truncated) < len(long_text)
    assert truncated.endswith(extractors.TRUNCATION_NOTE)


# ===== 추출기 =====


@pytest.mark.asyncio
async def test_plain_text_extraction():
    text = "회원 가입 전환율을 15% 올렸다."

    assert await extract(text.encode(), "메모.txt", "text/plain") == text


@pytest.mark.asyncio
async def test_non_utf8_text_is_unreadable():
    with pytest.raises(FileUnreadableError):
        await extract(b"\xff\xfe\x00\x01", "메모.txt", "text/plain")


@pytest.mark.asyncio
async def test_corrupt_docx_is_unreadable():
    """손상된 zip 은 품질 문제다. 노드 실패가 아니다."""
    with pytest.raises(FileUnreadableError):
        await extract(b"PK\x03\x04broken", "이력서.docx", DOCX_MIME)


@pytest.mark.asyncio
async def test_docx_is_parsed():
    """최소 구조의 docx 를 실제로 읽는다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>전환율 15% 개선</w:t></w:r></w:p></w:body></w:document>",
        )

    text = await extract(buffer.getvalue(), "이력서.docx", DOCX_MIME)

    assert "전환율 15% 개선" in text


@pytest.mark.asyncio
async def test_pptx_slides_are_parsed_in_numeric_order():
    """PPTX 슬라이드는 파일명 문자열이 아니라 실제 슬라이드 번호 순으로 읽는다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "ppt/slides/slide10.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:p><a:r><a:t>열 번째</a:t></a:r></a:p></p:sld>',
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            '<p:sld xmlns:p="p" xmlns:a="a"><a:p><a:r><a:t>두 번째</a:t></a:r></a:p></p:sld>',
        )

    text = await extract(buffer.getvalue(), "발표자료.pptx", PPTX_MIME)

    assert text.splitlines() == ["두 번째", "열 번째"]


@pytest.mark.asyncio
async def test_unsupported_type_is_unreadable():
    with pytest.raises(FileUnreadableError):
        await extract(b"x", "악성.exe", "application/x-msdownload")


@pytest.mark.parametrize(
    "content_type,expected",
    [
        ("text/plain", "parser"),
        (DOCX_MIME, "parser"),
        ("application/pdf", "ocr"),
        ("image/png", "ocr"),
    ],
)
def test_extractor_kind(content_type, expected):
    assert extractor_kind(content_type) == expected


# ===== PDF: 텍스트 레이어와 관계없이 모든 페이지 OCR =====


def minimal_pdf() -> bytes:
    """Helvetica 기본 폰트만 쓰는 최소 1페이지 PDF."""
    return b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 44>>stream
BT /F1 24 Tf 20 100 Td (Hello PDF) Tj ET
endstream
endobj
xref
0 6
trailer<</Size 6/Root 1 0 R>>
startxref
0
%%EOF"""


def scanned_pdf(page_count: int = 1) -> bytes:
    """텍스트 레이어가 없는 이미지 기반 PDF."""
    images = [Image.new("RGB", (200, 200), "white") for _ in range(page_count)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


class _CaptureVisionLlm:
    """OCR vision 호출을 기록하는 대역."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.invocations: list[list] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        return AIMessage(content=self.response_text)


@pytest.mark.asyncio
async def test_text_pdf_is_always_rendered_and_sent_to_ocr(monkeypatch):
    """텍스트 레이어가 있는 PDF도 직접 추출하지 않고 이미지 OCR한다."""
    fake_llm = _CaptureVisionLlm("OCR Hello PDF")
    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: fake_llm)

    text = await extractors.extract_with_ocr(minimal_pdf(), "이력서.pdf", "application/pdf")

    assert text == "OCR Hello PDF"
    assert len(fake_llm.invocations) == 1
    [message] = fake_llm.invocations[0]
    image_blocks = [block for block in message.content if block["type"] == "image_url"]
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_scanned_pdf_is_rendered_to_png_before_ocr(monkeypatch):
    """텍스트 레이어가 없는 페이지만 PNG로 렌더링해 OCR한다.

    원본 바이트를 그대로 `image_url`로 보내면, 제공자가 내부적으로 PDF를
    이미지로 바꾸는 과정에서 한글처럼 폰트가 내장된 텍스트를 제대로
    래스터화하지 못해 한글만 빈칸으로 사라지는 사고가 실제로 있었다. 이
    테스트는 모델에 실제로 전달되는 content block이 `image/png`인지 확인한다
    — `application/pdf` 그대로 보내면 회귀다.
    """
    fake_llm = _CaptureVisionLlm("Hello PDF")
    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: fake_llm)

    text = await extractors.extract_with_ocr(scanned_pdf(), "스캔.pdf", "application/pdf")

    assert text == "Hello PDF"
    [message] = fake_llm.invocations[0]
    image_blocks = [block for block in message.content if block["type"] == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_pdf_ocr_runs_per_page_and_preserves_page_order(monkeypatch):
    """스캔 페이지를 한 요청에 몰지 않고 페이지별 호출한 뒤 원래 순서로 합친다."""

    class PageAwareLlm:
        def __init__(self) -> None:
            self.invocations: list[list] = []

        async def ainvoke(self, messages):
            self.invocations.append(messages)
            image_url = messages[0].content[1]["image_url"]["url"]
            encoded = image_url.removeprefix("data:image/png;base64,")
            page = base64.b64decode(encoded).decode("utf-8")
            return AIMessage(content=f"OCR {page}")

    fake_llm = PageAwareLlm()
    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: fake_llm)
    monkeypatch.setattr(
        extractors,
        "_render_pdf_pages",
        lambda _: [f"page-{index + 1}".encode() for index in range(3)],
    )

    text = await extractors.extract_with_ocr(b"pdf", "스캔.pdf", "application/pdf")

    assert text.split("\n\n") == ["OCR page-1", "OCR page-2", "OCR page-3"]
    assert len(fake_llm.invocations) == 3
    assert all(len(invocation[0].content) == 2 for invocation in fake_llm.invocations)


@pytest.mark.asyncio
async def test_pdf_ocr_notes_when_page_limit_truncates_document(monkeypatch):
    """실제 페이지 수가 처리 상한보다 많으면 결과 텍스트에 그 사실을 남긴다.

    실제로 지적된 문제다 — `MAX_PDF_PAGES`를 넘는 PDF는 뒤 페이지가 조용히
    버려지고 사용자는 몰랐다. `_pdf_total_page_count`(실제 전체 페이지 수)가
    렌더링된 페이지 수보다 크면, 그 차이를 결과 텍스트에 안내 문구로 남겨야
    이후 file_processor가 `file_content_truncated`를 세우고 최종 응답에도
    반영할 수 있다.
    """

    class PageAwareLlm:
        async def ainvoke(self, messages):
            image_url = messages[0].content[1]["image_url"]["url"]
            encoded = image_url.removeprefix("data:image/png;base64,")
            page = base64.b64decode(encoded).decode("utf-8")
            return AIMessage(content=f"OCR {page}")

    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: PageAwareLlm())
    monkeypatch.setattr(
        extractors,
        "_render_pdf_pages",
        lambda _: [f"page-{index + 1}".encode() for index in range(3)],
    )
    # 실제 PDF는 20페이지지만 렌더링은 상한에 걸려 3페이지만 됐다고 가정한다.
    monkeypatch.setattr(extractors, "_pdf_total_page_count", lambda _: 20)

    text = await extractors.extract_with_ocr(b"pdf", "스캔.pdf", "application/pdf")

    assert extractors.PAGE_TRUNCATION_NOTE_MARKER in text
    assert "3페이지" in text and "20페이지" in text


@pytest.mark.asyncio
async def test_pdf_ocr_limits_concurrent_page_requests(monkeypatch):
    """페이지 수가 늘어도 OCR 동시 호출은 설정한 상한을 넘지 않는다."""

    class ConcurrencyLlm:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def ainvoke(self, messages):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                return AIMessage(content="페이지 내용")
            finally:
                self.active -= 1

    fake_llm = ConcurrencyLlm()
    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: fake_llm)
    monkeypatch.setattr(
        extractors,
        "_render_pdf_pages",
        lambda _: [f"page-{index + 1}".encode() for index in range(7)],
    )

    text = await extractors.extract_with_ocr(b"pdf", "스캔.pdf", "application/pdf")

    assert text.split("\n\n") == ["페이지 내용"] * 7
    assert fake_llm.max_active == extractors.PDF_OCR_CONCURRENCY


@pytest.mark.asyncio
async def test_pdf_ocr_timeout_scales_with_concurrency_rounds(monkeypatch):
    """페이지가 동시성 상한보다 많아 여러 라운드로 나뉘어도, 개별 페이지가 제
    시간 안에 끝나면 전체가 타임아웃되지 않는다.

    페이지별 예산(timeouts.file)을 라운드 수만큼 늘리지 않으면, 페이지가 전부
    정상 처리돼도 라운드가 여러 번 돈다는 이유만으로 asyncio.TimeoutError 가
    난다.
    """
    page_count = extractors.PDF_OCR_CONCURRENCY * 3  # 3라운드가 필요하도록

    class SlowLlm:
        async def ainvoke(self, messages):
            await asyncio.sleep(0.05)
            return AIMessage(content="페이지 내용")

    class FakeTimeouts:
        file = (
            0.12  # 한 라운드(~0.05초)는 통과하지만, 스케일 안 하면 3라운드 합(~0.15초)엔 못 미친다
        )

    class FakeSettings:
        timeouts = FakeTimeouts()

    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: SlowLlm())
    monkeypatch.setattr(extractors, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        extractors,
        "_render_pdf_pages",
        lambda _: [f"page-{index + 1}".encode() for index in range(page_count)],
    )

    text = await extractors.extract_with_ocr(b"pdf", "스캔.pdf", "application/pdf")

    assert text.split("\n\n") == ["페이지 내용"] * page_count


@pytest.mark.asyncio
async def test_mixed_pdf_ocrs_every_page(monkeypatch):
    """텍스트·스캔 혼합 PDF도 예외 없이 모든 렌더링 페이지를 OCR한다."""

    class PageAwareLlm:
        async def ainvoke(self, messages):
            image_url = messages[0].content[1]["image_url"]["url"]
            encoded = image_url.removeprefix("data:image/png;base64,")
            page = base64.b64decode(encoded).decode("utf-8")
            return AIMessage(content=f"OCR {page}")

    monkeypatch.setattr(extractors, "get_file_processor_llm", PageAwareLlm)
    monkeypatch.setattr(
        extractors,
        "_render_pdf_pages",
        lambda _: [b"page-1", b"page-2", b"page-3"],
    )

    text = await extractors.extract_with_ocr(b"pdf", "혼합.pdf", "application/pdf")

    assert text.split("\n\n") == ["OCR page-1", "OCR page-2", "OCR page-3"]


@pytest.mark.asyncio
async def test_image_files_are_sent_as_is_without_rendering(monkeypatch):
    """PNG·JPEG는 이미 raster 이미지이므로 렌더링 없이 그대로 보낸다."""
    fake_llm = _CaptureVisionLlm("이미지 속 텍스트")
    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: fake_llm)

    text = await extractors.extract_with_ocr(b"fake-png-bytes", "스크린샷.png", "image/png")

    assert text == "이미지 속 텍스트"
    [message] = fake_llm.invocations[0]
    image_blocks = [block for block in message.content if block["type"] == "image_url"]
    assert image_blocks[0]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"fake-png-bytes").decode()
    )


@pytest.mark.asyncio
async def test_corrupt_pdf_is_unreadable(monkeypatch):
    """PDF 자체를 열 수 없으면 품질 문제다. 노드 실패가 아니다."""
    monkeypatch.setattr(extractors, "get_file_processor_llm", lambda: _CaptureVisionLlm(""))

    with pytest.raises(FileUnreadableError):
        await extractors.extract_with_ocr(b"not a pdf", "손상.pdf", "application/pdf")


def test_render_pdf_pages_returns_one_png_per_page():
    images = extractors._render_pdf_pages(minimal_pdf())

    assert len(images) == 1
    assert images[0].startswith(b"\x89PNG")
