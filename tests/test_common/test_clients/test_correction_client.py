"""첨삭 클라이언트 callback payload 테스트"""

from collections.abc import Sequence
from typing import Any, get_type_hints
from unittest.mock import AsyncMock, patch

import pytest

from common.clients.correction_client import CorrectionClient
from features.portfolio.pdf_extraction.schemas import PdfActivity


def test_complete_pdf_extraction_type_hint_accepts_model_sequences():
    """complete_pdf_extraction activities는 모델 리스트도 허용하는 시퀀스 타입 힌트여야 한다."""
    activities_hint = get_type_hints(CorrectionClient.complete_pdf_extraction)["activities"]

    assert activities_hint == Sequence[Any]


@pytest.mark.asyncio
async def test_complete_pdf_extraction_sends_camelcase_payload():
    """PDF 추출 성공 callback은 camelCase payload로 전송한다."""
    client = CorrectionClient(base_url="https://example.com", api_key="test-key")

    try:
        with patch.object(client, "post", new_callable=AsyncMock, return_value={}) as mock_post:
            await client.complete_pdf_extraction(
                correction_id=174,
                activities=[
                    PdfActivity.model_validate(
                        {
                            "activity_name": "프로젝트명",
                            "detail": ["상세 설명"],
                            "responsibility": ["담당 업무"],
                            "problem_solving": [
                                {
                                    "no": 1,
                                    "situation": "문제 상황",
                                    "strategy": "대응 전략",
                                    "reason": "선택 이유",
                                },
                            ],
                            "learning": ["배운 점"],
                        }
                    )
                ],
            )

            mock_post.assert_awaited_once_with(
                "/internal/corrections/174/pdf-extraction-result",
                json={
                    "activities": [
                        {
                            "activityName": "프로젝트명",
                            "detail": ["상세 설명"],
                            "responsibility": ["담당 업무"],
                            "problemSolving": [
                                {
                                    "no": 1,
                                    "situation": "문제 상황",
                                    "strategy": "대응 전략",
                                    "reason": "선택 이유",
                                }
                            ],
                            "learning": ["배운 점"],
                        }
                    ],
                    "sourceType": "EXTERNAL",
                },
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_complete_pdf_extraction_allows_source_type_override():
    """source_type 인자를 전달하면 sourceType 값에 반영한다."""
    client = CorrectionClient(base_url="https://example.com", api_key="test-key")

    try:
        with patch.object(client, "post", new_callable=AsyncMock, return_value={}) as mock_post:
            await client.complete_pdf_extraction(
                correction_id=175,
                activities=[
                    {
                        "activity_name": "다른 프로젝트",
                        "detail": ["상세 설명"],
                        "responsibility": ["담당 업무"],
                        "problem_solving": [
                            {
                                "no": 1,
                                "situation": "문제 상황",
                                "strategy": "대응 전략",
                                "reason": "선택 이유",
                            }
                        ],
                        "learning": ["배운 점"],
                    },
                ],
                source_type="INTERNAL",
            )

            mock_post.assert_awaited_once_with(
                "/internal/corrections/175/pdf-extraction-result",
                json={
                    "activities": [
                        {
                            "activityName": "다른 프로젝트",
                            "detail": ["상세 설명"],
                            "responsibility": ["담당 업무"],
                            "problemSolving": [
                                {
                                    "no": 1,
                                    "situation": "문제 상황",
                                    "strategy": "대응 전략",
                                    "reason": "선택 이유",
                                }
                            ],
                            "learning": ["배운 점"],
                        }
                    ],
                    "sourceType": "INTERNAL",
                },
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_fail_pdf_extraction_sends_error_message_payload():
    """PDF 추출 실패 callback은 errorMessage payload로 전송한다."""
    client = CorrectionClient(base_url="https://example.com", api_key="test-key")

    try:
        with patch.object(client, "post", new_callable=AsyncMock, return_value={}) as mock_post:
            await client.fail_pdf_extraction(
                correction_id=174,
                error_message="PDF 추출 중 오류가 발생했습니다.",
            )

            mock_post.assert_awaited_once_with(
                "/internal/corrections/174/pdf-extraction-result",
                json={"errorMessage": "PDF 추출 중 오류가 발생했습니다."},
            )
    finally:
        await client.close()
