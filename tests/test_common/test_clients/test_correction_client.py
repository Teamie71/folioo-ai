"""CorrectionClient PDF callback 테스트"""

from unittest.mock import AsyncMock

import pytest

from common.clients.correction_client import CorrectionClient


@pytest.mark.asyncio
async def test_complete_pdf_extraction_posts_camel_case_payload():
    """complete_pdf_extraction은 camelCase payload로 전송한다."""
    client = CorrectionClient.__new__(CorrectionClient)
    client.post = AsyncMock(return_value={"ok": True})

    activities = [
        {
            "activity_name": "프로젝트명",
            "detail": "상세 설명",
            "responsibility": "담당 업무",
            "problem_solving": [
                {
                    "no": 1,
                    "situation": "문제 상황",
                    "strategy": "대응 전략",
                    "reason": "선택 이유",
                }
            ],
            "learning": "배운 점",
        }
    ]

    await client.complete_pdf_extraction(123, activities, "EXTERNAL")

    client.post.assert_awaited_once_with(
        "/internal/corrections/123/pdf-extraction-result",
        json={
            "activities": [
                {
                    "activityName": "프로젝트명",
                    "detail": "상세 설명",
                    "responsibility": "담당 업무",
                    "problemSolving": [
                        {
                            "no": 1,
                            "situation": "문제 상황",
                            "strategy": "대응 전략",
                            "reason": "선택 이유",
                        }
                    ],
                    "learning": "배운 점",
                }
            ],
            "sourceType": "EXTERNAL",
        },
    )


@pytest.mark.asyncio
async def test_fail_pdf_extraction_posts_error_message_payload():
    """fail_pdf_extraction은 errorMessage payload로 전송한다."""
    client = CorrectionClient.__new__(CorrectionClient)
    client.post = AsyncMock(return_value={"ok": True})

    await client.fail_pdf_extraction(123, "PDF 추출 실패")

    client.post.assert_awaited_once_with(
        "/internal/corrections/123/pdf-extraction-result",
        json={"errorMessage": "PDF 추출 실패"},
    )
