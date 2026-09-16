"""첨삭 스키마 테스트"""

import pytest

from app.schemas.correction import (
    CorrectionResultResponse,
    CreateCorrectionRequest,
    UpdateCompanyInsightRequest,
    UpdateEmphasisPointsRequest,
)
from features.correction.schemas import (
    CorrectionOutput,
    CorrectionStatus,
    PortfolioCorrectionResult,
    SingleCorrectionDecisionOutput,
    SingleCorrectionOutput,
)


def _sample_fields() -> list[dict]:
    return [
        {
            "field_name": "description",
            "lines": [
                {
                    "line_number": 1,
                    "original_text": "원문",
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
        {
            "field_name": "contributions",
            "lines": [
                {
                    "line_number": 1,
                    "original_text": "원문",
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
        {
            "field_name": "achievements",
            "lines": [
                {
                    "line_number": 1,
                    "original_text": "원문",
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
        {
            "field_name": "insights",
            "lines": [
                {
                    "line_number": 1,
                    "original_text": "원문",
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
    ]


def _sample_decision_fields() -> list[dict]:
    return [
        {
            "field_name": "description",
            "lines": [
                {
                    "line_number": 1,
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
        {
            "field_name": "contributions",
            "lines": [
                {
                    "line_number": 1,
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
        {
            "field_name": "achievements",
            "lines": [
                {
                    "line_number": 1,
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
        {
            "field_name": "insights",
            "lines": [
                {
                    "line_number": 1,
                    "type": "keep",
                    "comment": None,
                }
            ],
        },
    ]


def test_single_correction_decision_output_schema_excludes_original_text():
    """LLM decision 스키마는 원문 없이 판단 값만 포함한다."""
    output = SingleCorrectionDecisionOutput.model_validate({"fields": _sample_decision_fields()})

    assert output.fields[0].field_name == "description"
    assert not hasattr(output.fields[0].lines[0], "original_text")


def test_single_correction_decision_output_rejects_original_text():
    """LLM decision 스키마는 original_text/originalText 추가 출력을 거부한다."""
    fields = _sample_decision_fields()
    fields[0]["lines"][0]["original_text"] = "원문"

    with pytest.raises(ValueError):
        SingleCorrectionDecisionOutput.model_validate({"fields": fields})

    fields = _sample_decision_fields()
    fields[0]["lines"][0]["originalText"] = "원문"

    with pytest.raises(ValueError):
        SingleCorrectionDecisionOutput.model_validate({"fields": fields})


def test_single_correction_output_schema():
    """SingleCorrectionOutput 스키마 생성 테스트"""
    output = SingleCorrectionOutput.model_validate({"fields": _sample_fields()})

    assert output.fields[0].field_name == "description"
    assert output.fields[0].lines[0].type == "keep"


def test_single_correction_output_rejects_missing_section():
    """필수 섹션 누락 시 ValidationError 발생 테스트"""
    with pytest.raises(ValueError):
        SingleCorrectionOutput.model_validate({"fields": _sample_fields()[:-1]})


def test_single_correction_output_rejects_duplicated_section():
    """필수 섹션 중복 시 ValidationError 발생 테스트"""
    duplicated_fields = _sample_fields()
    duplicated_fields[1]["field_name"] = "description"

    with pytest.raises(ValueError):
        SingleCorrectionOutput.model_validate({"fields": duplicated_fields})


def test_portfolio_correction_result_schema():
    """PortfolioCorrectionResult 스키마 생성 테스트"""
    result = PortfolioCorrectionResult.model_validate(
        {"portfolio_id": 101, "fields": _sample_fields()}
    )

    assert result.portfolio_id == 101
    assert len(result.fields) == 4


def test_correction_output_schema():
    """다중 포트폴리오 CorrectionOutput 스키마 생성 테스트"""
    output = CorrectionOutput.model_validate(
        {
            "portfolio_corrections": [
                {"portfolio_id": 101, "fields": _sample_fields()},
                {"portfolio_id": 202, "fields": _sample_fields()},
            ],
            "overall_summary": "전체 포트폴리오 총평",
        }
    )

    assert output.portfolio_corrections[0].portfolio_id == 101
    assert output.overall_summary == "전체 포트폴리오 총평"


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
        result=CorrectionOutput.model_validate(
            {
                "portfolio_corrections": [
                    {"portfolio_id": 101, "fields": _sample_fields()},
                ],
                "overall_summary": "총평",
            }
        ),
    )

    assert response.status == CorrectionStatus.DONE
    assert response.result is not None
    assert response.result.overall_summary == "총평"


def test_update_emphasis_points_request_schema():
    """UpdateEmphasisPointsRequest 스키마 생성 테스트"""
    request = UpdateEmphasisPointsRequest(emphasis_points="핵심 역량과 성과 지표를 강조")

    assert request.emphasis_points == "핵심 역량과 성과 지표를 강조"


def test_update_emphasis_points_request_allows_empty_string():
    """강조 포인트는 선택 항목이라 빈 문자열을 허용한다."""
    request = UpdateEmphasisPointsRequest(emphasis_points="")

    assert request.emphasis_points == ""


def test_update_company_insight_request_accepts_exactly_2000_chars():
    """UpdateCompanyInsightRequest는 2000자까지 허용한다."""
    request = UpdateCompanyInsightRequest(company_insight="가" * 2000)

    assert len(request.company_insight) == 2000


def test_update_company_insight_request_rejects_more_than_2000_chars():
    """UpdateCompanyInsightRequest는 2001자를 거부한다."""
    with pytest.raises(ValueError):
        UpdateCompanyInsightRequest(company_insight="가" * 2001)


def test_single_correction_line_comment_allows_null_for_keep():
    """keep 타입의 comment는 null 허용 테스트"""
    output = SingleCorrectionOutput.model_validate({"fields": _sample_fields()})

    assert output.fields[0].lines[0].comment is None
