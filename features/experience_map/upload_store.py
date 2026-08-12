"""경험정리 임시 첨부 파일 저장

프론트 → AI 직결로 파일이 들어오므로 검증과 수명 관리가 AI 서버 몫이다.

수명은 짧다.

```text
업로드 → GCS 임시 object → 텍스트 추출 → 즉시 삭제
                              └ 실패 → 1시간 TTL 뒤 정리
```

추출에 성공하면 원본은 바로 지우고 추출 텍스트만 checkpoint에 남긴다. 실패했을
때만 재시도를 위해 원본을 1시간 유지한다.

**개인정보가 담긴 이력서·포트폴리오가 올라온다.** 파일명·본문·추출 원문을 로그에
남기지 않는다. 로그에는 `file_id`와 크기만 쓴다.
"""

import asyncio
import codecs
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from features.experience_map.config import (
    ALLOWED_MIME_TYPES,
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_FILES,
    OCR_MIME_TYPES,
    PARSER_MIME_TYPES,
    get_settings,
)
from features.experience_map.errors import (
    FileTooLargeError,
    InvalidRequestError,
    UnsupportedFileTypeError,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
SIGNATURE_PROBE_BYTES = 8

# MIME별 파일 signature. `.txt`는 signature가 없어 UTF-8 디코딩으로 판정한다.
FILE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    # DOCX·PPTX는 ZIP 컨테이너다.
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
    ),
}


class AsyncFileLike(Protocol):
    """FastAPI `UploadFile`이 만족하는 최소 인터페이스"""

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class StoredFile:
    """저장된 파일의 참조. 파일 본문은 담지 않는다.

    `as_reference()` 결과가 state의 `FileReference` 형태와 같다.
    """

    file_id: str
    filename: str
    content_type: str
    file_size: int
    sha256: str
    gcs_object: str

    @property
    def extractor(self) -> Literal["parser", "ocr"]:
        """이 파일을 처리할 방식"""
        return "parser" if self.content_type in PARSER_MIME_TYPES else "ocr"

    def as_reference(self) -> dict[str, object]:
        """state에 넣을 dict로 변환"""
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "file_size": self.file_size,
            "sha256": self.sha256,
            "gcs_object": self.gcs_object,
        }


def _extension_of(filename: str) -> str:
    """파일명에서 소문자 확장자를 뽑는다."""
    _, dot, ext = filename.rpartition(".")
    return f".{ext.lower()}" if dot else ""


def validate_declared_type(filename: str, content_type: str) -> None:
    """MIME과 확장자의 조합을 검사한다. 본문을 읽기 전에 호출한다.

    Raises:
        UnsupportedFileTypeError: 허용 목록에 없거나 확장자가 MIME과 맞지 않는 경우
    """
    normalized = (content_type or "").split(";")[0].strip().lower()
    allowed_extensions = ALLOWED_MIME_TYPES.get(normalized)
    if allowed_extensions is None:
        raise UnsupportedFileTypeError("지원하지 않는 파일 형식입니다.")

    if _extension_of(filename) not in allowed_extensions:
        raise UnsupportedFileTypeError("파일 확장자가 형식과 일치하지 않습니다.")


def validate_signature(content_type: str, head: bytes) -> None:
    """실제 파일 내용의 signature를 검사한다.

    확장자와 MIME은 클라이언트가 정하는 값이라 위조할 수 있다. 첫 바이트를 보고
    실제 형식을 확인한다.

    Raises:
        UnsupportedFileTypeError: signature가 선언한 형식과 다른 경우
    """
    signatures = FILE_SIGNATURES.get(content_type)
    if signatures is None:
        # text/plain은 signature가 없다. UTF-8로 읽히는지로 판정한다.
        # 잘린 멀티바이트 문자를 오류로 보지 않도록 증분 디코더를 쓴다.
        # 한글은 3바이트라 고정 길이로 자르면 거의 항상 문자 중간에서 끊긴다.
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            decoder.decode(head, final=False)
        except UnicodeDecodeError as exc:
            raise UnsupportedFileTypeError("텍스트 파일을 읽을 수 없습니다.") from exc
        return

    if not any(head.startswith(signature) for signature in signatures):
        raise UnsupportedFileTypeError("파일 내용이 형식과 일치하지 않습니다.")


class ObjectStore(Protocol):
    """GCS 연산의 최소 인터페이스. 테스트에서 대체한다."""

    async def upload(self, object_name: str, data: bytes, content_type: str) -> None: ...

    async def download(self, object_name: str) -> bytes: ...

    async def delete(self, object_name: str) -> None: ...

    async def list_names(self, prefix: str) -> list[str]: ...

    async def created_at(self, object_name: str) -> datetime | None: ...


class GcsObjectStore:
    """google-cloud-storage 기반 구현.

    클라이언트가 동기라 blocking 호출을 스레드로 넘긴다.
    """

    def __init__(self, bucket_name: str) -> None:
        self._bucket_name = bucket_name
        self._client = None

    def _bucket(self):
        from google.cloud import storage

        if self._client is None:
            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    async def upload(self, object_name: str, data: bytes, content_type: str) -> None:
        def _upload() -> None:
            self._bucket().blob(object_name).upload_from_string(data, content_type=content_type)

        await asyncio.to_thread(_upload)

    async def download(self, object_name: str) -> bytes:
        def _download() -> bytes:
            return self._bucket().blob(object_name).download_as_bytes()

        return await asyncio.to_thread(_download)

    async def delete(self, object_name: str) -> None:
        def _delete() -> None:
            self._bucket().blob(object_name).delete()

        await asyncio.to_thread(_delete)

    async def list_names(self, prefix: str) -> list[str]:
        def _list() -> list[str]:
            return [blob.name for blob in self._bucket().list_blobs(prefix=prefix)]

        return await asyncio.to_thread(_list)

    async def created_at(self, object_name: str) -> datetime | None:
        def _created() -> datetime | None:
            blob = self._bucket().get_blob(object_name)
            return blob.time_created if blob else None

        return await asyncio.to_thread(_created)


def request_prefix(user_id: str, request_id: str) -> str:
    """요청 전용 object prefix. 요청 단위로 한 번에 지울 수 있게 한다."""
    return f"expmap/{user_id}/{request_id}/"


class UploadStore:
    """첨부 파일 검증·저장·삭제"""

    def __init__(self, store: ObjectStore, *, file_ttl_seconds: int | None = None) -> None:
        self._store = store
        self._file_ttl_seconds = (
            file_ttl_seconds if file_ttl_seconds is not None else get_settings().file_ttl_seconds
        )

    async def store_files(
        self,
        user_id: str,
        request_id: str,
        files: list[tuple[str, str, AsyncFileLike]],
    ) -> list[StoredFile]:
        """파일을 검증하고 GCS 임시 object로 올린다.

        입력 순서를 유지한다. 파일처리 노드가 추출 결과를 이 순서로 이어 붙인다.

        Args:
            user_id: 십진 문자열 사용자 ID
            request_id: 요청 UUID
            files: `(filename, content_type, stream)` 목록

        Returns:
            list[StoredFile]: 입력 순서와 같은 저장 결과

        Raises:
            InvalidRequestError: 개수 초과
            UnsupportedFileTypeError: 형식·확장자·signature 불일치
            FileTooLargeError: 파일당 크기 초과
        """
        if len(files) > MAX_UPLOAD_FILES:
            raise InvalidRequestError(f"파일은 최대 {MAX_UPLOAD_FILES}개까지 첨부할 수 있습니다.")

        stored: list[StoredFile] = []
        try:
            for filename, content_type, stream in files:
                stored.append(
                    await self._store_one(user_id, request_id, filename, content_type, stream)
                )
        except Exception:
            # 일부만 올라간 상태를 남기지 않는다.
            await self._delete_all(stored)
            raise

        return stored

    async def _store_one(
        self,
        user_id: str,
        request_id: str,
        filename: str,
        content_type: str,
        stream: AsyncFileLike,
    ) -> StoredFile:
        validate_declared_type(filename, content_type)
        normalized_type = content_type.split(";")[0].strip().lower()

        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        checked_signature = False
        # text/plain은 signature가 없으므로 본문 전체가 UTF-8인지 확인한다.
        text_decoder = (
            codecs.getincrementaldecoder("utf-8")() if normalized_type == "text/plain" else None
        )

        while True:
            chunk = await stream.read(CHUNK_SIZE)
            if not chunk:
                break

            total += len(chunk)
            if total > MAX_UPLOAD_FILE_BYTES:
                # 남은 본문을 계속 읽지 않는다.
                raise FileTooLargeError(
                    f"파일 크기가 {MAX_UPLOAD_FILE_BYTES // (1024 * 1024)}MB를 초과했습니다."
                )

            if not checked_signature:
                validate_signature(normalized_type, chunk[:SIGNATURE_PROBE_BYTES])
                checked_signature = True

            if text_decoder is not None:
                try:
                    text_decoder.decode(chunk, final=False)
                except UnicodeDecodeError as exc:
                    raise UnsupportedFileTypeError("텍스트 파일을 읽을 수 없습니다.") from exc

            digest.update(chunk)
            chunks.append(chunk)

        if total == 0:
            raise InvalidRequestError("빈 파일은 첨부할 수 없습니다.")

        if text_decoder is not None:
            # 마지막 문자가 잘린 채 끝났으면 온전한 UTF-8이 아니다.
            try:
                text_decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise UnsupportedFileTypeError("텍스트 파일을 읽을 수 없습니다.") from exc

        file_id = f"f_{uuid.uuid4().hex[:12]}"
        object_name = f"{request_prefix(user_id, request_id)}{file_id}"
        await self._store.upload(object_name, b"".join(chunks), normalized_type)

        # 파일명과 본문은 로그에 남기지 않는다.
        logger.info("경험정리 첨부 파일 저장 (file_id=%s, size=%d)", file_id, total)

        return StoredFile(
            file_id=file_id,
            filename=filename,
            content_type=normalized_type,
            file_size=total,
            sha256=digest.hexdigest(),
            gcs_object=object_name,
        )

    async def read(self, object_name: str) -> bytes:
        """저장한 원본을 읽는다. 추출 노드가 쓴다.

        추출에 성공하면 원본은 바로 지우므로, 이 호출이 실패하면 이미 처리가
        끝났거나 TTL 이 지났다는 뜻이다.
        """
        return await self._store.download(object_name)

    async def delete_after_extraction(self, stored: StoredFile) -> None:
        """추출에 성공한 파일의 원본을 즉시 지운다."""
        await self._delete_quietly(stored.gcs_object)

    async def delete_object(self, object_name: str) -> None:
        """object 이름만 아는 경우의 삭제 (state 의 file_reference)"""
        await self._delete_quietly(object_name)

    async def discard_request(self, user_id: str, request_id: str) -> None:
        """요청 전용 object를 모두 지운다.

        request claim 실패나 저장 결과 재전송처럼 이번 업로드를 쓰지 않게 된 경우에
        호출한다.
        """
        prefix = request_prefix(user_id, request_id)
        for name in await self._store.list_names(prefix):
            await self._delete_quietly(name)

    async def sweep_expired(self, prefix: str = "expmap/") -> int:
        """TTL이 지난 object를 정리한다.

        bucket lifecycle 규칙으로 대체할 수 있다. 두 방식 중 하나만 있으면 된다.

        Returns:
            int: 삭제한 object 수
        """
        deadline = datetime.now(UTC) - timedelta(seconds=self._file_ttl_seconds)
        deleted = 0

        for name in await self._store.list_names(prefix):
            created = await self._store.created_at(name)
            if created is not None and created > deadline:
                continue
            await self._delete_quietly(name)
            deleted += 1

        if deleted:
            logger.info("경험정리 만료 첨부 파일 정리 (count=%d)", deleted)
        return deleted

    async def _delete_all(self, stored: list[StoredFile]) -> None:
        for item in stored:
            await self._delete_quietly(item.gcs_object)

    async def _delete_quietly(self, object_name: str) -> None:
        """삭제 실패가 요청 처리를 막지 않게 한다. TTL 정리가 뒤를 받친다."""
        try:
            await self._store.delete(object_name)
        except Exception:
            logger.warning("첨부 파일 삭제 실패 (object=%s)", object_name, exc_info=True)


_upload_store: UploadStore | None = None


def get_upload_store() -> UploadStore:
    """UploadStore 싱글톤 반환

    Raises:
        RuntimeError: `EXPMAP_UPLOAD_BUCKET`이 설정되지 않은 경우
    """
    global _upload_store

    if _upload_store is None:
        bucket = get_settings().upload_bucket
        if not bucket:
            raise RuntimeError("EXPMAP_UPLOAD_BUCKET 환경변수가 설정되지 않았습니다.")
        _upload_store = UploadStore(GcsObjectStore(bucket))
    return _upload_store


def set_upload_store(store: UploadStore | None) -> None:
    """UploadStore 주입 (테스트·기동 시)"""
    global _upload_store
    _upload_store = store


__all__ = [
    "ALLOWED_MIME_TYPES",
    "OCR_MIME_TYPES",
    "PARSER_MIME_TYPES",
    "AsyncFileLike",
    "GcsObjectStore",
    "ObjectStore",
    "StoredFile",
    "UploadStore",
    "get_upload_store",
    "request_prefix",
    "set_upload_store",
    "validate_declared_type",
    "validate_signature",
]
