"""PDF 추출 스키마 테스트"""

import pytest
from pydantic import ValidationError

from features.portfolio.pdf_extraction.schemas import (
    PdfActivity,
    PdfExtractionResult,
    PdfProblemSolvingItem,
)


def test_pdf_problem_solving_item_validates_positive_no():
    """문제 해결 순번은 1 이상이어야 한다."""
    with pytest.raises(ValidationError):
        PdfProblemSolvingItem(no=0, situation="상황", strategy="전략", reason="이유")


def test_pdf_activity_requires_mandatory_fields():
    """활동 스키마는 필수 필드를 검증한다."""
    with pytest.raises(ValidationError):
        PdfActivity(
            activity_name="",
            detail="상세 설명",
            responsibility="담당 업무",
            problem_solving=[],
            learning="배운 점",
        )


def test_pdf_extraction_result_serializes_and_deserializes():
    """추출 결과는 직렬화/역직렬화 가능하다."""
    result = PdfExtractionResult(
        activities=[
            PdfActivity(
                activity_name="프로젝트명",
                detail="상세 설명",
                responsibility="담당 업무",
                problem_solving=[
                    PdfProblemSolvingItem(
                        no=1,
                        situation="문제 상황",
                        strategy="대응 전략",
                        reason="선택 이유",
                    )
                ],
                learning="배운 점",
            )
        ]
    )

    dumped = result.model_dump()
    restored = PdfExtractionResult.model_validate(dumped)

    assert restored == result
