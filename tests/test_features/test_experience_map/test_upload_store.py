"""경험정리 임시 첨부 파일 저장 테스트"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from features.experience_map.config import MAX_UPLOAD_FILE_BYTES
from features.experience_map.errors import (
    FileTooLargeError,
    InvalidRequestError,
    UnsupportedFileTypeError,
)
from features.experience_map.upload_store import (
    StoredFile,
    UploadStore,
    request_prefix,
    validate_declared_type,
    validate_signature,
)

USER_ID = "123"
REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

PDF_BODY = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj"
PNG_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BODY = b"\xff\xd8\xff\xe0" + b"\x00" * 32
ZIP_BODY = b"PK\x03\x04" + b"\x00" * 32
TXT_BODY = "회원 가입 전환율을 15% 올렸다.".encode()


class FakeStream:
    """UploadFile 대역"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class FakeObjectStore:
    """GCS 대역"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.created: dict[str, datetime] = {}
        self.delete_calls: list[str] = []
        self.fail_delete = False

    async def upload(self, object_name: str, data: bytes, content_type: str) -> None:
        self.objects[object_name] = data
        self.created.setdefault(object_name, datetime.now(UTC))

    async def delete(self, object_name: str) -> None:
        self.delete_calls.append(object_name)
        if self.fail_delete:
            raise RuntimeError("삭제 실패")
        self.objects.pop(object_name, None)
        self.created.pop(object_name, None)

    async def list_names(self, prefix: str) -> list[str]:
        return [name for name in self.objects if name.startswith(prefix)]

    async def created_at(self, object_name: str) -> datetime | None:
        return self.created.get(object_name)


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def upload_store(store: FakeObjectStore) -> UploadStore:
    return UploadStore(store, file_ttl_seconds=3600)


# ===== 선언 형식 검증 =====


@pytest.mark.parametrize(
    "filename,mime",
    [
        ("메모.txt", "text/plain"),
        ("이력서.docx", DOCX_MIME),
        ("발표.pptx", PPTX_MIME),
        ("포트폴리오.pdf", "application/pdf"),
        ("화면.png", "image/png"),
        ("사진.jpg", "image/jpeg"),
        ("사진.jpeg", "image/jpeg"),
        ("대문자.PDF", "application/pdf"),
    ],
)
def test_accepts_supported_types(filename, mime):
    validate_declared_type(filename, mime)


def test_rejects_unsupported_mime():
    with pytest.raises(UnsupportedFileTypeError, match="지원하지 않는"):
        validate_declared_type("악성.exe", "application/x-msdownload")


def test_rejects_extension_mime_mismatch():
    """MIME은 PDF인데 확장자가 png면 거부한다."""
    with pytest.raises(UnsupportedFileTypeError, match="확장자"):
        validate_declared_type("문서.png", "application/pdf")


def test_content_type_with_charset_is_accepted():
    validate_declared_type("메모.txt", "text/plain; charset=utf-8")


# ===== signature 검증 =====


@pytest.mark.parametrize(
    "mime,head",
    [
        ("application/pdf", PDF_BODY),
        ("image/png", PNG_BODY),
        ("image/jpeg", JPEG_BODY),
        (DOCX_MIME, ZIP_BODY),
        (PPTX_MIME, ZIP_BODY),
        ("text/plain", TXT_BODY),
    ],
)
def test_signature_accepts_matching_content(mime, head):
    validate_signature(mime, head[:8])


def test_signature_rejects_forged_extension():
    """확장자와 MIME만 png로 바꾼 PDF는 거부된다."""
    with pytest.raises(UnsupportedFileTypeError, match="내용이 형식과"):
        validate_signature("image/png", PDF_BODY[:8])


def test_signature_rejects_non_utf8_text():
    """.txt는 signature가 없으므로 UTF-8 디코딩으로 판정한다."""
    with pytest.raises(UnsupportedFileTypeError, match="텍스트 파일"):
        validate_signature("text/plain", b"\xff\xfe\x00\x01\x02\x03\x04\x05")


def test_signature_tolerates_truncated_multibyte_head():
    """한글은 3바이트라 고정 길이 probe가 문자 중간에서 끊긴다.

    이걸 오류로 보면 정상적인 한글 txt가 전부 거부된다.
    """
    validate_signature("text/plain", TXT_BODY[:8])


@pytest.mark.asyncio
async def test_korean_text_spanning_chunks_is_accepted(upload_store, monkeypatch):
    """청크 경계에서 잘린 문자도 이어 붙여 판정한다."""
    monkeypatch.setattr("features.experience_map.upload_store.CHUNK_SIZE", 7)
    body = "회원 가입 전환율을 15% 올렸다. 이탈률은 절반으로 줄었다.".encode()

    stored = await upload_store.store_files(
        USER_ID, REQUEST_ID, [("메모.txt", "text/plain", FakeStream(body))]
    )

    assert stored[0].file_size == len(body)


@pytest.mark.asyncio
async def test_text_truncated_mid_character_is_rejected(upload_store):
    """마지막 문자가 잘린 채 끝나면 온전한 UTF-8이 아니다.

    끝의 `.`은 1바이트라 잘라도 유효하다. 3바이트인 `다`의 중간에서 끊어야 한다.
    """
    truncated = TXT_BODY[:-2]

    with pytest.raises(UnsupportedFileTypeError, match="텍스트 파일"):
        await upload_store.store_files(
            USER_ID, REQUEST_ID, [("깨진.txt", "text/plain", FakeStream(truncated))]
        )


# ===== 저장 =====


@pytest.mark.asyncio
async def test_store_files_uploads_and_hashes(upload_store, store):
    stored = await upload_store.store_files(
        USER_ID, REQUEST_ID, [("포트폴리오.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )

    assert len(stored) == 1
    item = stored[0]
    assert item.file_size == len(PDF_BODY)
    assert len(item.sha256) == 64
    assert item.gcs_object.startswith(request_prefix(USER_ID, REQUEST_ID))
    assert store.objects[item.gcs_object] == PDF_BODY


@pytest.mark.asyncio
async def test_store_files_preserves_single_input_filename(upload_store):
    """요청당 한 개인 파일의 이름을 변경하지 않는다."""
    stored = await upload_store.store_files(
        USER_ID,
        REQUEST_ID,
        [("경험정리.txt", "text/plain", FakeStream(TXT_BODY))],
    )

    assert [item.filename for item in stored] == ["경험정리.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "body", "expected"),
    [
        ("메모.txt", "text/plain", TXT_BODY, "parser"),
        ("포트폴리오.pdf", "application/pdf", PDF_BODY, "ocr"),
    ],
)
async def test_extractor_is_chosen_by_type(upload_store, filename, content_type, body, expected):
    stored = await upload_store.store_files(
        USER_ID,
        REQUEST_ID,
        [(filename, content_type, FakeStream(body))],
    )

    assert stored[0].extractor == expected


@pytest.mark.asyncio
async def test_rejects_too_many_files(upload_store, store):
    files = [(f"f{i}.txt", "text/plain", FakeStream(TXT_BODY)) for i in range(2)]

    with pytest.raises(InvalidRequestError, match="최대 1개"):
        await upload_store.store_files(USER_ID, REQUEST_ID, files)

    assert store.objects == {}


@pytest.mark.asyncio
async def test_rejects_oversized_file(upload_store):
    oversized = b"%PDF-" + b"0" * MAX_UPLOAD_FILE_BYTES

    with pytest.raises(FileTooLargeError, match="10MB"):
        await upload_store.store_files(
            USER_ID, REQUEST_ID, [("큰파일.pdf", "application/pdf", FakeStream(oversized))]
        )


@pytest.mark.asyncio
async def test_rejects_empty_file(upload_store):
    with pytest.raises(InvalidRequestError, match="빈 파일"):
        await upload_store.store_files(
            USER_ID, REQUEST_ID, [("빈파일.txt", "text/plain", FakeStream(b""))]
        )


# ===== 삭제와 수명 =====


@pytest.mark.asyncio
async def test_delete_after_extraction(upload_store, store):
    stored = await upload_store.store_files(
        USER_ID, REQUEST_ID, [("포트폴리오.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )

    await upload_store.delete_after_extraction(stored[0])

    assert store.objects == {}


@pytest.mark.asyncio
async def test_discard_request_removes_all_objects(upload_store, store):
    """request claim 실패나 저장 결과 재전송이면 방금 올린 것을 모두 지운다."""
    await upload_store.store_files(
        USER_ID,
        REQUEST_ID,
        [("a.pdf", "application/pdf", FakeStream(PDF_BODY))],
    )

    await upload_store.discard_request(USER_ID, REQUEST_ID)

    assert store.objects == {}


@pytest.mark.asyncio
async def test_discard_request_does_not_touch_other_requests(upload_store, store):
    await upload_store.store_files(
        USER_ID, REQUEST_ID, [("a.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )
    other_request = "660e8400-e29b-41d4-a716-446655440001"
    await upload_store.store_files(
        USER_ID, other_request, [("b.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )

    await upload_store.discard_request(USER_ID, REQUEST_ID)

    remaining = list(store.objects)
    assert len(remaining) == 1
    assert remaining[0].startswith(request_prefix(USER_ID, other_request))


@pytest.mark.asyncio
async def test_sweep_keeps_objects_within_ttl(upload_store, store):
    """추출 실패 후 1시간 안에는 원본으로 재시도할 수 있어야 한다."""
    stored = await upload_store.store_files(
        USER_ID, REQUEST_ID, [("포트폴리오.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )
    store.created[stored[0].gcs_object] = datetime.now(UTC) - timedelta(minutes=59)

    assert await upload_store.sweep_expired() == 0
    assert stored[0].gcs_object in store.objects


@pytest.mark.asyncio
async def test_sweep_removes_expired_objects(upload_store, store):
    stored = await upload_store.store_files(
        USER_ID, REQUEST_ID, [("포트폴리오.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )
    store.created[stored[0].gcs_object] = datetime.now(UTC) - timedelta(hours=2)

    assert await upload_store.sweep_expired() == 1
    assert store.objects == {}


@pytest.mark.asyncio
async def test_delete_failure_does_not_raise(upload_store, store):
    """삭제 실패가 요청 처리를 막지 않는다. TTL 정리가 뒤를 받친다."""
    stored = await upload_store.store_files(
        USER_ID, REQUEST_ID, [("포트폴리오.pdf", "application/pdf", FakeStream(PDF_BODY))]
    )
    store.fail_delete = True

    await upload_store.delete_after_extraction(stored[0])

    assert store.delete_calls


# ===== 개인정보 보호 =====


@pytest.mark.asyncio
async def test_filename_and_body_are_not_logged(upload_store, caplog):
    """이력서·포트폴리오가 올라온다. 파일명과 본문을 로그에 남기지 않는다."""
    secret_name = "홍길동_이력서_010-1234-5678.txt"
    secret_body = "주민등록번호 900101-1234567".encode()

    with caplog.at_level(logging.DEBUG):
        await upload_store.store_files(
            USER_ID, REQUEST_ID, [(secret_name, "text/plain", FakeStream(secret_body))]
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_name not in logged
    assert "홍길동" not in logged
    assert "900101" not in logged


def test_stored_file_reference_shape():
    """state의 FileReference와 같은 형태여야 한다."""
    reference = StoredFile(
        file_id="f_abc",
        filename="포트폴리오.pdf",
        content_type="application/pdf",
        file_size=1024,
        sha256="a" * 64,
        gcs_object="expmap/123/req/f_abc",
    ).as_reference()

    assert set(reference) == {
        "file_id",
        "filename",
        "content_type",
        "file_size",
        "sha256",
        "gcs_object",
    }
