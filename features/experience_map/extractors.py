"""첨부 파일 텍스트 추출 (에이전트 문서 5-2)

두 경로가 있다.

| 처리 | 형식 |
| --- | --- |
| 파일 파서 | TXT, DOCX, PPTX |
| OCR 모델 | PDF, PNG, JPG/JPEG |

**두 실패를 구분하는 것이 이 모듈의 핵심이다.**

- `FileUnreadableError` — 파일 상태·품질 문제. 재시도해도 같은 결과다.
  노드 실패가 아니라 **Fallback** 으로 간다
- 그 밖의 예외 — 타임아웃·API 실패 같은 시스템 오류. **노드 실패**이며 자동
  재시도 대상이다

손상된 PDF 를 올린 사용자에게 재시도 버튼을 보여주면 몇 번을 눌러도 같은
결과다.
"""

import asyncio
import base64
import io
import logging
import zipfile

from common.llm.client import get_file_processor_llm
from features.experience_map.config import (
    MAX_FILE_TEXT_CHARS,
    OCR_MIME_TYPES,
    PARSER_MIME_TYPES,
    get_settings,
)

logger = logging.getLogger(__name__)

TRUNCATION_NOTE = "\n\n[내용이 길어 앞부분만 사용했습니다.]"

OCR_INSTRUCTION = """\
첨부한 문서나 이미지에 있는 **텍스트를 그대로 옮겨** 주세요.

- 요약하거나 다시 쓰지 마세요. 보이는 글자를 그대로 옮깁니다.
- 표는 읽는 순서대로 풀어서 적습니다.
- 읽을 수 없는 부분은 건너뜁니다. 추측해서 채우지 마세요.
- 텍스트가 전혀 없으면 빈 문자열만 반환합니다.
"""


class FileUnreadableError(Exception):
    """파일 상태·품질 문제로 추출할 수 없다.

    **노드 실패가 아니다.** Fallback 으로 가야 한다 (5-2).
    """


def _truncate(text: str, limit: int = MAX_FILE_TEXT_CHARS) -> str:
    """파일 하나가 프롬프트를 독차지하지 않게 자른다."""
    if len(text) <= limit:
        return text
    logger.info("추출 텍스트가 길어 자릅니다 (%d → %d자)", len(text), limit)
    return text[:limit] + TRUNCATION_NOTE


def _extract_text_plain(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileUnreadableError("텍스트 파일을 읽을 수 없습니다.") from exc


def _require_valid_zip(data: bytes) -> None:
    """DOCX·PPTX 가 온전한 zip 컨테이너인지 확인한다.

    **markitdown 은 손상된 zip 에 예외를 던지지 않는다.** 바이트를 평문으로
    해석해 깨진 글자(`偋̄扲潫敮`)를 돌려준다. 그대로 두면 그 쓰레기가 LLM 까지
    흘러가 사용자 경험 정리에 들어간다.

    업로드 검증(3.07)은 첫 4바이트 signature 만 본다. 여기서 컨테이너 전체를
    확인한다.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip() is not None:
                raise FileUnreadableError("문서가 손상되었습니다.")
    except zipfile.BadZipFile as exc:
        raise FileUnreadableError("문서가 손상되었습니다.") from exc


def _extract_with_markitdown(data: bytes, filename: str) -> str:
    """DOCX·PPTX 를 markitdown 으로 읽는다.

    라이브러리가 동기라 호출자가 스레드로 넘긴다.
    """
    from markitdown import MarkItDown

    _require_valid_zip(data)

    try:
        result = MarkItDown().convert_stream(io.BytesIO(data), file_extension=_suffix(filename))
    except Exception as exc:
        # 지원하지 않는 내부 구조 등. 재시도해도 같은 결과다.
        raise FileUnreadableError("문서를 읽을 수 없습니다.") from exc

    text = (result.text_content or "").strip()
    if not text:
        # 읽히긴 했는데 글자가 없다. 빈 문서이거나 이미지만 있는 문서다.
        raise FileUnreadableError("문서에서 텍스트를 찾지 못했습니다.")
    return text


def _suffix(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return f".{ext.lower()}" if dot else ""


async def extract_with_parser(data: bytes, filename: str, content_type: str) -> str:
    """파서로 텍스트를 뽑는다 (TXT·DOCX·PPTX)."""
    if content_type == "text/plain":
        text = _extract_text_plain(data).strip()
        if not text:
            raise FileUnreadableError("빈 파일입니다.")
        return _truncate(text)

    text = await asyncio.to_thread(_extract_with_markitdown, data, filename)
    return _truncate(text)


def _ocr_attachment_block(content_type: str, filename: str, encoded: str) -> dict:
    """콘텐츠 타입에 맞는 첨부 블록을 만든다.

    **`image_url` 은 PNG·JPEG 전용이다.** PDF 를 여기 넣으면 모델이 이미지로
    해석을 시도하다 실패한다. 완전히 못 읽지도 않고(그러면 `FileUnreadableError`
    로 걸린다) 깨끗이 읽지도 못한 애매한 텍스트가 나와서, 다음 단계인
    content_filter 가 "불분명함"으로 정확히 분류하고 마는 조용한 실패였다.

    PDF 는 `type: "file"` 블록을 써야 한다. `features/portfolio/pdf_extraction`
    이 이미 이 형식으로 PDF 를 정상 처리하고 있다.
    """
    if content_type == "application/pdf":
        return {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": f"data:{content_type};base64,{encoded}",
            },
        }
    return {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{encoded}"}}


async def extract_with_ocr(data: bytes, filename: str, content_type: str) -> str:
    """OCR 모델로 텍스트를 뽑는다 (PDF·PNG·JPEG).

    Raises:
        FileUnreadableError: 모델이 읽을 내용을 찾지 못함
        Exception: 시스템 오류 (타임아웃·API 실패) — 노드 실패로 올라간다
    """
    from langchain_core.messages import HumanMessage

    encoded = base64.b64encode(data).decode("utf-8")
    message = HumanMessage(
        content=[
            {"type": "text", "text": OCR_INSTRUCTION},
            _ocr_attachment_block(content_type, filename, encoded),
        ]
    )

    llm = get_file_processor_llm()
    response = await asyncio.wait_for(llm.ainvoke([message]), timeout=get_settings().timeouts.file)

    text = (getattr(response, "content", "") or "").strip()
    if not text:
        # 모델은 정상 응답했는데 읽을 글자가 없다. 스캔 품질 문제이거나 빈 문서다.
        raise FileUnreadableError("파일에서 텍스트를 찾지 못했습니다.")

    return _truncate(text)


async def extract(data: bytes, filename: str, content_type: str) -> str:
    """형식에 맞는 경로로 텍스트를 뽑는다.

    Raises:
        FileUnreadableError: 파일 품질 문제 (Fallback 대상)
    """
    if content_type in PARSER_MIME_TYPES:
        return await extract_with_parser(data, filename, content_type)
    if content_type in OCR_MIME_TYPES:
        return await extract_with_ocr(data, filename, content_type)

    # 업로드 검증(3.07)을 통과했다면 여기 오지 않는다.
    raise FileUnreadableError(f"지원하지 않는 형식입니다: {content_type}")


def extractor_kind(content_type: str) -> str:
    """`parser` 또는 `ocr`"""
    return "parser" if content_type in PARSER_MIME_TYPES else "ocr"
