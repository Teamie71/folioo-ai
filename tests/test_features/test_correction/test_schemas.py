"""첨삭 스키마 테스트"""

from app.schemas.correction import (
    CorrectionResultResponse,
    CreateCorrectionRequest,
    UpdateEmphasisPointsRequest,
)
from features.correction.schemas import CorrectionOutput, CorrectionStatus


def test_correction_output_schema():
    """CorrectionOutput 스키마 생성 테스트"""
    output = CorrectionOutput.model_validate(
        {
            "fields": [
                {
                    "field_name": "description",
                    "lines": [
                        {
                            "line_number": 1,
                            "original_text": "원문",
                            "type": "keep",
                            "comment": "좋습니다.",
                        }
                    ],
                }
            ],
            "overall_summary": "요약",
        }
    )

    assert output.fields[0].field_name == "description"
    assert output.fields[0].lines[0].type == "keep"
    assert output.overall_summary == "요약"


def test_create_correction_request_schema():
    """CreateCorrectionRequest 스키마 생성 테스트"""
    request = CreateCorrectionRequest(
        portfolio_id="portfolio-1",
        user_id="user-1",
        company_name="테스트 회사",
        job_title="백엔드 개발자",
        job_description="직무 설명",
    )

    assert request.portfolio_id == "portfolio-1"
    assert request.user_id == "user-1"


def test_correction_result_response_schema():
    """CorrectionResultResponse 스키마 생성 테스트"""
    response = CorrectionResultResponse(
        correction_id="correction-1",
        status=CorrectionStatus.DONE,
        result=None,
    )

    assert response.status == CorrectionStatus.DONE
    assert response.result is None


def test_update_emphasis_points_request_schema():
    """UpdateEmphasisPointsRequest 스키마 생성 테스트"""
    request = UpdateEmphasisPointsRequest(emphasis_points="핵심 역량과 성과 지표를 강조")

    assert request.emphasis_points == "핵심 역량과 성과 지표를 강조"
