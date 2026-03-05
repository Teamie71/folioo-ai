"""첨삭 클라이언트 필드 변환 테스트"""

from unittest.mock import AsyncMock, patch

import pytest

from common.main_server.correction_client import (
    _FIELD_NAME_TO_SERVER,
    _STATUS_TO_UPPER,
    CorrectionClient,
    _correction_output_to_payload,
    _transform_get_correction_response,
)
from features.correction.schemas import CorrectionOutput


class TestFieldNameMapping:
    """필드명 매핑 상수 검증"""

    def test_field_name_to_server(self):
        assert _FIELD_NAME_TO_SERVER == {
            "description": "description",
            "contributions": "responsibilities",
            "achievements": "problemSolving",
            "insights": "learnings",
        }

    def test_status_to_upper(self):
        assert _STATUS_TO_UPPER["not_started"] == "NOT_STARTED"
        assert _STATUS_TO_UPPER["doing_rag"] == "DOING_RAG"
        assert _STATUS_TO_UPPER["company_insight"] == "COMPANY_INSIGHT"
        assert _STATUS_TO_UPPER["done"] == "DONE"
        assert _STATUS_TO_UPPER["failed"] == "FAILED"


class TestTransformGetCorrectionResponse:
    """get_correction 응답 변환 테스트"""

    def test_field_renaming(self):
        """필드명 변환 확인"""
        raw = {
            "id": 1,
            "positionName": "백엔드 개발자",
            "highlightPoint": "성과 중심 기술",
            "status": "COMPANY_INSIGHT",
            "portfolioIds": [1, 2, 3],
        }
        result = _transform_get_correction_response(raw)

        assert result["job_title"] == "백엔드 개발자"
        assert result["emphasis_points"] == "성과 중심 기술"
        assert result["status"] == "company_insight"
        assert result["portfolioIds"] == [1, 2, 3]
        assert "positionName" not in result
        assert "highlightPoint" not in result

    def test_status_lowercase_conversion(self):
        """상태값 대소문자 변환"""
        for upper, lower in [
            ("NOT_STARTED", "not_started"),
            ("DOING_RAG", "doing_rag"),
            ("DONE", "done"),
            ("FAILED", "failed"),
        ]:
            raw = {"status": upper}
            result = _transform_get_correction_response(raw)
            assert result["status"] == lower


class TestCorrectionOutputToPayload:
    """CorrectionOutput -> 메인 서버 payload 변환 테스트"""

    @pytest.fixture
    def sample_output(self) -> CorrectionOutput:
        return CorrectionOutput.model_validate(
            {
                "fields": [
                    {
                        "field_name": "description",
                        "lines": [
                            {
                                "line_number": 1,
                                "original_text": "원문1",
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
                                "original_text": "원문2",
                                "type": "emphasize",
                                "comment": "강조하세요.",
                            }
                        ],
                    },
                    {
                        "field_name": "achievements",
                        "lines": [
                            {
                                "line_number": 1,
                                "original_text": "원문3",
                                "type": "reduce",
                                "comment": "줄이세요.",
                            }
                        ],
                    },
                    {
                        "field_name": "insights",
                        "lines": [
                            {
                                "line_number": 1,
                                "original_text": "원문4",
                                "type": "keep",
                                "comment": "유지.",
                            }
                        ],
                    },
                ],
                "overall_summary": "전체 요약 내용",
            }
        )

    def test_field_name_mapping(self, sample_output):
        """AI 서버 필드명 -> 메인 서버 필드명 변환"""
        payload = _correction_output_to_payload(sample_output)

        field_names = [f["fieldName"] for f in payload["fields"]]
        assert "description" in field_names
        assert "responsibilities" in field_names
        assert "problemSolving" in field_names
        assert "learnings" in field_names
        assert "contributions" not in field_names
        assert "achievements" not in field_names
        assert "insights" not in field_names

    def test_overall_summary_to_overall_review(self, sample_output):
        """overall_summary -> overallReview 변환"""
        payload = _correction_output_to_payload(sample_output)
        assert payload["overallReview"] == "전체 요약 내용"
        assert "overall_summary" not in payload

    def test_lines_camelcase(self, sample_output):
        """라인 필드 camelCase 변환"""
        payload = _correction_output_to_payload(sample_output)
        first_line = payload["fields"][0]["lines"][0]

        assert "lineNumber" in first_line
        assert "originalText" in first_line
        assert "type" in first_line
        assert "comment" in first_line
        assert "line_number" not in first_line
        assert "original_text" not in first_line


class TestCorrectionClientGetCorrection:
    """CorrectionClient.get_correction 테스트"""

    @pytest.mark.asyncio
    async def test_get_correction(self):
        """첨삭 조회 및 필드 변환"""
        mock_result = {
            "id": 10,
            "positionName": "프론트엔드 개발자",
            "highlightPoint": "UI/UX 개선 성과",
            "status": "DONE",
            "portfolioIds": [1],
        }

        client = CorrectionClient()
        with patch(
            "common.main_server.correction_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await client.get_correction(10)

            assert result["job_title"] == "프론트엔드 개발자"
            assert result["emphasis_points"] == "UI/UX 개선 성과"
            assert result["status"] == "done"


class TestCorrectionClientUpdateStatus:
    """CorrectionClient.update_status 테스트"""

    @pytest.mark.asyncio
    async def test_status_uppercase_conversion(self):
        """소문자 -> UPPER_CASE 변환하여 전송"""
        client = CorrectionClient()
        with patch(
            "common.main_server.correction_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_req:
            await client.update_status(1, "doing_rag")

            mock_req.assert_called_once_with(
                "PATCH",
                "/corrections/1/status",
                json={"status": "DOING_RAG"},
            )


class TestCorrectionClientUpdateCompanyInsight:
    """CorrectionClient.update_company_insight 테스트"""

    @pytest.mark.asyncio
    async def test_camelcase_key(self):
        """companyInsight 키로 전송"""
        client = CorrectionClient()
        with patch(
            "common.main_server.correction_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_req:
            await client.update_company_insight(5, "기업 분석 내용")

            mock_req.assert_called_once_with(
                "PATCH",
                "/corrections/5/company-insight",
                json={"companyInsight": "기업 분석 내용"},
            )


class TestCorrectionClientSaveRagData:
    """CorrectionClient.save_rag_data 테스트"""

    @pytest.mark.asyncio
    async def test_camelcase_payload(self):
        """searchQuery, searchResults camelCase 전송"""
        client = CorrectionClient()
        with patch(
            "common.main_server.correction_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_req:
            await client.save_rag_data(
                correction_id=3,
                search_query="백엔드 개발",
                search_results=[{"title": "결과1"}],
            )

            mock_req.assert_called_once_with(
                "POST",
                "/corrections/3/rag-data",
                json={
                    "searchQuery": "백엔드 개발",
                    "searchResults": [{"title": "결과1"}],
                },
            )


class TestCorrectionClientGetRagData:
    """CorrectionClient.get_rag_data 테스트"""

    @pytest.mark.asyncio
    async def test_returns_dict(self):
        """RAG 데이터 조회 시 dict 반환"""
        mock_result = {"searchQuery": "test", "searchResults": []}

        client = CorrectionClient()
        with patch(
            "common.main_server.correction_client.request_with_retry",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await client.get_rag_data(3)
            assert result == {"searchQuery": "test", "searchResults": []}
