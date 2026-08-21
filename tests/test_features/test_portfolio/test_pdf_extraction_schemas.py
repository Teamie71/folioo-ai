"""PDF 포트폴리오 구조화 스키마 테스트"""

import pytest
from pydantic import ValidationError

from app.schemas.pdf_extraction import PdfExtractionAcceptedResponse
from features.portfolio.pdf_extraction.schemas import PdfExtractionResult


def test_pdf_extraction_result_schema():
    """PDF 추출 결과 스키마 생성 테스트"""
    result = PdfExtractionResult.model_validate(
        {
            "activities": [
                {
                    "activity_name": "포트폴리오 개선 프로젝트",
                    "detail": ["PDF 기반 경력기술서 구조화 기능 구현"],
                    "responsibility": ["백엔드 설계 및 API 연동"],
                    "problem_solving": [
                        {
                            "no": 1,
                            "situation": "입력 PDF마다 구조가 달랐습니다.",
                            "strategy": "공통 활동 스키마를 먼저 정의했습니다.",
                            "reason": "후속 로직에서 동일한 계약을 재사용하기 위해서입니다.",
                        }
                    ],
                    "learning": ["추출 결과 계약을 먼저 고정하면 후속 구현이 쉬워집니다."],
                }
            ]
        }
    )

    assert result.activities[0].activity_name == "포트폴리오 개선 프로젝트"
    assert result.activities[0].detail == ["PDF 기반 경력기술서 구조화 기능 구현"]
    assert result.activities[0].problem_solving[0].no == 1


def test_pdf_extraction_result_rejects_empty_activities():
    """PDF 추출 성공 결과는 최소 1개의 활동을 포함해야 한다."""
    with pytest.raises(ValidationError):
        PdfExtractionResult.model_validate({"activities": []})


def test_pdf_extraction_result_rejects_more_than_four_activities():
    """PDF 추출 성공 결과는 활동 4개를 초과할 수 없다."""
    with pytest.raises(ValidationError):
        PdfExtractionResult.model_validate(
            {
                "activities": [
                    {
                        "activity_name": f"활동 {index}",
                        "detail": ["상세"],
                        "responsibility": ["담당"],
                        "problem_solving": [],
                        "learning": ["배운 점"],
                    }
                    for index in range(5)
                ]
            }
        )


def test_pdf_extraction_accepted_response_schema():
    """PDF 추출 요청 접수 응답 스키마 생성 테스트"""
    response = PdfExtractionAcceptedResponse(
        correction_id="174",
        status="accepted",
        message="PDF 추출 작업이 시작되었습니다.",
    )

    assert response.correction_id == "174"
    assert response.status == "accepted"
    assert response.message == "PDF 추출 작업이 시작되었습니다."
