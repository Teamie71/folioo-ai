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
    MAX_PDF_PAGES,
    OCR_MIME_TYPES,
    PARSER_MIME_TYPES,
    PDF_RENDER_SCALE,
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


def _render_pdf_pages(data: bytes) -> list[bytes]:
    """PDF 페이지를 PNG 이미지로 직접 렌더링한다.

    PDF 원본 바이트를 그대로 vision 모델에 `image_url`로 넘기면, 제공자가
    내부적으로 PDF를 이미지로 변환하는 과정에서 한글처럼 폰트가 내장된 텍스트를
    제대로 래스터화하지 못해 **한글만 빈칸으로 사라지는** 사고가 실제로
    있었다(영문·숫자·문장부호는 기본 폰트라 살아남는다). PDFium은 CJK를
    포함한 폰트 렌더링을 자체적으로 처리하므로, 서버가 직접 페이지를 그려
    실제 이미지로 만든 뒤 그 이미지를 모델에 보낸다 — "PDF는 OCR로 처리한다"는
    정책(문서 3-2)은 그대로 지키면서 래스터화 결함만 없앤다.

    페이지가 너무 많으면 비용·시간이 무한정 늘어나므로 앞쪽 `MAX_PDF_PAGES`
    장만 쓴다 — 이력서·포트폴리오류는 뒤로 갈수록 부가 자료인 경우가 많다.
    """
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise FileUnreadableError("PDF를 열 수 없습니다.") from exc

    try:
        page_count = min(len(pdf), MAX_PDF_PAGES)
        if page_count == 0:
            raise FileUnreadableError("PDF에 페이지가 없습니다.")
        images: list[bytes] = []
        for index in range(page_count):
            page = pdf[index]
            try:
                bitmap = page.render(scale=PDF_RENDER_SCALE)
                buffer = io.BytesIO()
                bitmap.to_pil().save(buffer, format="PNG")
                images.append(buffer.getvalue())
            finally:
                page.close()
        return images
    except pdfium.PdfiumError as exc:
        raise FileUnreadableError("PDF 페이지를 읽을 수 없습니다.") from exc
    finally:
        pdf.close()


async def extract_with_parser(data: bytes, filename: str, content_type: str) -> str:
    """파서로 텍스트를 뽑는다 (TXT·DOCX·PPTX)."""
    if content_type == "text/plain":
        text = _extract_text_plain(data).strip()
        if not text:
            raise FileUnreadableError("빈 파일입니다.")
        return _truncate(text)

    text = await asyncio.to_thread(_extract_with_markitdown, data, filename)
    return _truncate(text)


def _image_blocks(images: list[bytes], mime_type: str) -> list[dict]:
    """PNG/JPEG 바이트 목록을 vision 메시지의 `image_url` 블록으로 바꾼다."""
    blocks = []
    for image in images:
        encoded = base64.b64encode(image).decode("utf-8")
        blocks.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}}
        )
    return blocks


async def extract_with_ocr(data: bytes, filename: str, content_type: str) -> str:
    """OCR 모델로 텍스트를 뽑는다 (PDF·PNG·JPEG).

    PDF는 원본 바이트를 그대로 보내지 않는다. 페이지를 PNG로 직접 렌더링한
    뒤(`_render_pdf_pages`) 그 이미지들을 보낸다 — 한글 래스터화 결함을 없애기
    위해서다. PNG·JPEG는 이미 raster 이미지라 그대로 보낸다.

    Raises:
        FileUnreadableError: 모델이 읽을 내용을 찾지 못함, 또는 PDF 자체를 열 수 없음
        Exception: 시스템 오류 (타임아웃·API 실패) — 노드 실패로 올라간다
    """
    from langchain_core.messages import HumanMessage

    if content_type == "application/pdf":
        pages = await asyncio.to_thread(_render_pdf_pages, data)
        image_blocks = _image_blocks(pages, "image/png")
        instruction = OCR_INSTRUCTION
        if len(pages) > 1:
            instruction += "\n- 여러 페이지입니다. 순서대로 이어서 옮겨 적습니다.\n"
    else:
        image_blocks = _image_blocks([data], content_type)
        instruction = OCR_INSTRUCTION

    message = HumanMessage(content=[{"type": "text", "text": instruction}, *image_blocks])

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
