"""첨삭 메인 서버 API 클라이언트"""

import logging
from typing import Any

from common.http_client import request_with_retry
from features.correction.schemas import CorrectionOutput

logger = logging.getLogger(__name__)

_FIELD_NAME_TO_SERVER = {
    "description": "description",
    "contributions": "responsibilities",
    "achievements": "problemSolving",
    "insights": "learnings",
}

_STATUS_TO_UPPER = {
    "not_started": "NOT_STARTED",
    "doing_rag": "DOING_RAG",
    "company_insight": "COMPANY_INSIGHT",
    "generating": "GENERATING",
    "done": "DONE",
    "failed": "FAILED",
}


def _transform_get_correction_response(raw: dict[str, Any]) -> dict[str, Any]:
    """메인 서버 응답을 AI 서버 형식으로 변환"""
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "positionName":
            result["job_title"] = value
        elif key == "highlightPoint":
            result["emphasis_points"] = value
        elif key == "status" and isinstance(value, str):
            result["status"] = value.lower()
        else:
            result[key] = value
    return result


def _correction_output_to_payload(result: CorrectionOutput) -> dict[str, Any]:
    """CorrectionOutput을 메인 서버 PATCH body로 변환"""
    fields_payload: list[dict[str, Any]] = []
    for field in result.fields:
        server_field_name = _FIELD_NAME_TO_SERVER[field.field_name]
        lines_payload = [
            {
                "lineNumber": line.line_number,
                "originalText": line.original_text,
                "type": line.type,
                "comment": line.comment,
            }
            for line in field.lines
        ]
        fields_payload.append({"fieldName": server_field_name, "lines": lines_payload})
    return {
        "fields": fields_payload,
        "overallReview": result.overall_summary,
    }


class CorrectionClient:
    """첨삭 메인 서버 API 클라이언트"""

    async def get_correction(self, correction_id: int) -> dict[str, Any]:
        """
        첨삭 단건 조회

        Args:
            correction_id: 첨삭 ID

        Returns:
            positionName->job_title, highlightPoint->emphasis_points,
            status UPPER_CASE->lowercase 변환된 딕셔너리
        """
        result = await request_with_retry(
            "GET",
            f"/corrections/{correction_id}",
        )
        if not isinstance(result, dict):
            return {}
        return _transform_get_correction_response(result)

    async def update_status(self, correction_id: int, status: str) -> None:
        """
        첨삭 상태 업데이트

        Args:
            correction_id: 첨삭 ID
            status: AI 서버 형식 상태 (lowercase), 전송 시 UPPER_CASE로 변환
        """
        upper_status = _STATUS_TO_UPPER.get(status, status.upper())
        await request_with_retry(
            "PATCH",
            f"/corrections/{correction_id}/status",
            json={"status": upper_status},
        )

    async def update_company_insight(self, correction_id: int, insight: str) -> None:
        """
        기업 인사이트 업데이트

        Args:
            correction_id: 첨삭 ID
            insight: 기업 인사이트 텍스트
        """
        await request_with_retry(
            "PATCH",
            f"/corrections/{correction_id}/company-insight",
            json={"companyInsight": insight},
        )

    async def update_result(self, correction_id: int, result: CorrectionOutput) -> None:
        """
        첨삭 결과 업데이트

        Args:
            correction_id: 첨삭 ID
            result: CorrectionOutput (features.correction.schemas)
        """
        payload = _correction_output_to_payload(result)
        await request_with_retry(
            "PATCH",
            f"/corrections/{correction_id}/result",
            json=payload,
        )

    async def save_rag_data(
        self,
        correction_id: int,
        search_query: str,
        search_results: list[dict],
    ) -> None:
        """
        RAG 검색 데이터 저장

        Args:
            correction_id: 첨삭 ID
            search_query: 검색어
            search_results: 검색 결과 리스트
        """
        await request_with_retry(
            "POST",
            f"/corrections/{correction_id}/rag-data",
            json={
                "searchQuery": search_query,
                "searchResults": search_results,
            },
        )

    async def get_rag_data(self, correction_id: int) -> dict:
        """
        RAG 검색 데이터 조회

        Args:
            correction_id: 첨삭 ID

        Returns:
            메인 서버 단일 객체 응답 (dict)
        """
        result = await request_with_retry(
            "GET",
            f"/corrections/{correction_id}/rag-data",
        )
        if not isinstance(result, dict):
            return {}
        return result
