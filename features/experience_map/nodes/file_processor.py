"""파일처리 노드 (에이전트 문서 5-2)

업로드한 파일에서 텍스트를 뽑아 `extracted_text` 를 채운다.

**추출에 성공한 파일의 원본은 즉시 지운다.** 결과는 checkpoint 에 남으므로,
재시도할 때 원본이 없어도 이어서 갈 수 있다. 반대로 추출 자체가 실패하면
원본을 1시간 유지해 다른 worker 가 다시 시도할 수 있게 한다 (명세 4-4).

**두 실패를 구분한다.**

| 상황 | 처리 |
| --- | --- |
| 추출 성공 | 다음 노드 |
| 파일 상태·품질 문제 | **Fallback** (노드 실패 아님) |
| 시스템 오류 (타임아웃·API 실패) | 노드 실패 → 자동 재시도 |
"""

import logging

from features.experience_map.config import MAX_TOTAL_TEXT_CHARS
from features.experience_map.errors import LlmError
from features.experience_map.extractors import FileUnreadableError, extract, extractor_kind
from features.experience_map.state import ExperienceMapState, ExtractedFile
from features.experience_map.upload_store import get_upload_store

logger = logging.getLogger(__name__)

FILE_SEPARATOR = "\n\n---\n\n"


async def process_files(state: ExperienceMapState) -> ExperienceMapState:
    """첨부 파일에서 텍스트를 뽑는다.

    파일이 섞여 있어도 **입력 순서대로** 이어 붙인다. 사용자가 올린 순서가
    이야기의 순서다.

    Raises:
        LlmError: 시스템 오류. 자동 재시도 대상이며 원본은 남겨 둔다
    """
    updated = dict(state)
    updated["current_node"] = "file_processor"

    references = state.get("file_references") or []
    if not references:
        logger.info("file_processor: 파일이 없습니다")
        return updated  # type: ignore[return-value]

    # 이미 추출한 파일은 건너뛴다 (재시도 경로).
    done = {item["file_id"] for item in state.get("extracted_files") or []}
    extracted: list[ExtractedFile] = list(state.get("extracted_files") or [])
    unreadable = 0

    store = get_upload_store()

    for reference in references:
        if reference["file_id"] in done:
            continue

        try:
            data = await store.read(reference["gcs_object"])
            text = await extract(data, reference["filename"], reference["content_type"])
        except FileUnreadableError:
            # 품질 문제다. 재시도해도 같은 결과이므로 노드를 실패시키지 않는다.
            logger.info("file_processor: 읽을 수 없는 파일 (file_id=%s)", reference["file_id"])
            unreadable += 1
            continue
        except Exception as exc:
            # 시스템 오류다. 원본을 남겨 둬야 다른 worker 가 재시도할 수 있다.
            logger.exception("file_processor: 추출 실패 (file_id=%s)", reference["file_id"])
            raise LlmError(
                "첨부 파일을 처리하지 못했습니다.", failed_node="file_processor"
            ) from exc

        extracted.append(
            ExtractedFile(
                file_id=reference["file_id"],
                text=text,
                source_hash=reference["sha256"],
                extractor=extractor_kind(reference["content_type"]),
            )
        )
        # 추출 결과가 checkpoint 에 남으므로 원본은 더 필요하지 않다.
        await store.delete_object(reference["gcs_object"])

    updated["extracted_files"] = extracted
    updated["extracted_text"] = _join(extracted, references)

    if not extracted:
        # 올린 파일을 하나도 못 읽었다.
        updated["fallback_reason"] = "file_unreadable"
        logger.info("file_processor: 모든 파일을 읽지 못했습니다 (%d개)", unreadable)
    elif unreadable:
        logger.info("file_processor: %d개 추출, %d개는 읽지 못했습니다", len(extracted), unreadable)

    return updated  # type: ignore[return-value]


def _join(extracted: list[ExtractedFile], references: list[dict]) -> str | None:
    """추출 결과를 **입력 순서대로** 이어 붙인다.

    파서와 OCR 이 섞여도 순서는 사용자가 올린 순서를 따른다. 전체 길이가 상한을
    넘으면 뒤쪽을 버린다 — 앞에 올린 파일이 대개 더 중요하다.
    """
    if not extracted:
        return None

    by_id = {item["file_id"]: item for item in extracted}
    ordered = [by_id[ref["file_id"]] for ref in references if ref["file_id"] in by_id]

    parts: list[str] = []
    total = 0
    for item in ordered:
        if total + len(item["text"]) > MAX_TOTAL_TEXT_CHARS:
            logger.info("file_processor: 전체 상한을 넘어 이후 파일을 제외합니다")
            break
        parts.append(item["text"])
        total += len(item["text"])

    return FILE_SEPARATOR.join(parts) if parts else None


def next_node(state: ExperienceMapState) -> str:
    """추출 결과로 다음 노드를 고른다."""
    if not (state.get("extracted_text") or "").strip():
        return "fallback"
    return "content_filter"
