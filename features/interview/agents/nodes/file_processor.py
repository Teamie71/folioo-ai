"""FileProcessor 노드 - 파일 처리"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from common.llm.client import get_file_processor_llm
from features.interview.agents.prompts import FILE_EXTRACTION_SYSTEM_PROMPT

from ..state import FilePayload, InterviewState

logger = logging.getLogger(__name__)


def _load_file_bytes(file_payload: FilePayload) -> bytes:
    """임시 파일 경로에서 파일 바이트를 읽는다."""
    temp_path = Path(file_payload["temp_path"])
    try:
        with temp_path.open("rb") as file_handle:
            return file_handle.read()
    except OSError as exc:
        raise ValueError(
            f"임시 파일을 읽을 수 없습니다: {file_payload['filename']} ({temp_path})"
        ) from exc


def _encode_data_url(file_bytes: bytes, content_type: str) -> str:
    """파일 바이트를 base64 데이터 URL로 변환한다."""
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:{content_type};base64,{encoded}"


def _encode_file_content(file_payload: FilePayload, file_bytes: bytes) -> dict[str, object]:
    """FilePayload를 LangChain 멀티모달 content block으로 변환한다."""
    filename = file_payload["filename"]
    content_type = file_payload["content_type"]
    data_url = _encode_data_url(file_bytes, content_type)

    if content_type == "application/pdf":
        return {
            "type": "file",
            "file": {
                "filename": filename,
                "file_data": data_url,
            },
        }

    return {
        "type": "image_url",
        "image_url": {
            "url": data_url,
        },
    }


def _normalize_response_text(content: object) -> str:
    """LLM 응답 content를 문자열로 정규화한다."""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    texts.append(stripped)
                continue

            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())

        return "\n".join(texts).strip()

    return str(content).strip()


def _extract_file_text(file_payload: FilePayload) -> str:
    """Vision LLM으로 파일 텍스트를 추출한다."""
    file_bytes = _load_file_bytes(file_payload)
    multimodal_content = _encode_file_content(file_payload, file_bytes)
    messages = [
        SystemMessage(content=FILE_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "첨부 파일을 읽고 내용을 최대한 충실하게 추출해주세요. "
                        f"파일명은 '{file_payload['filename']}'입니다."
                    ),
                },
                multimodal_content,
            ]
        ),
    ]

    response = get_file_processor_llm().invoke(messages)
    extracted_text = _normalize_response_text(getattr(response, "content", response))
    if not extracted_text:
        raise ValueError(f"파일에서 추출된 텍스트가 비어 있습니다: {file_payload['filename']}")
    return extracted_text


def run(state: InterviewState) -> InterviewState:
    """첨부된 파일을 순차 처리하여 file_contexts를 생성한다."""
    current_turn_files = list(state.get("current_turn_files") or [])
    if not current_turn_files:
        return {
            **state,
            "file_contexts": [],
            "current_turn_files": [],
            "next_node": "retriever",
        }

    file_contexts: list[str] = []
    for file_payload in current_turn_files:
        filename = file_payload["filename"]
        try:
            extracted_text = _extract_file_text(file_payload)
            file_contexts.append(f"[파일: {filename}]\n{extracted_text}")
        except Exception as exc:
            logger.exception("파일 처리 실패: %s", filename)
            file_contexts.append(f"[파일: {filename}]\n파일 처리 실패: {exc}")

    return {
        **state,
        "file_contexts": file_contexts,
        "current_turn_files": [],
        "next_node": "retriever",
    }
