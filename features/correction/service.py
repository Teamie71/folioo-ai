"""첨삭 서비스 오케스트레이션 (메인 서버 API 호출 기반)"""

import asyncio
import json
import logging
import re
import time

from fastapi import BackgroundTasks

from common.clients.correction_client import CorrectionClient, get_correction_client
from common.clients.portfolio_client import PortfolioClient, get_portfolio_client

from .generator import CorrectionGenerator, get_correction_generator
from .rag import (
    RAGInsightGenerationError,
    RAGKeywordExtractionError,
    RAGPipeline,
    RAGSearchError,
)
from .schemas import CorrectionOutput, CorrectionStatus, PortfolioCorrectionResult

logger = logging.getLogger(__name__)

_service: "CorrectionService | None" = None
_MAX_PORTFOLIO_COUNT = 4
_FIELD_NAME_TO_SERVER = {
    "description": "description",
    "contributions": "responsibilities",
    "achievements": "problemSolving",
    "insights": "learnings",
}


def _get_portfolio_text_field(portfolio: dict, *field_names: str) -> str:
    """여러 서버 필드명 후보 중 비어 있지 않은 첫 문자열 값을 반환한다."""
    for field_name in field_names:
        if field_name not in portfolio:
            continue

        value = portfolio[field_name]
        if value is None:
            continue

        normalized = str(value).strip()
        if normalized in {"", "0"}:
            continue
        return normalized

    return ""


def _to_upper_status(status: CorrectionStatus) -> str:
    """CorrectionStatus enum을 메인 서버가 기대하는 UPPER_CASE 문자열로 변환"""
    return status.value.upper()


def _normalize_status(raw: str | None) -> str:
    """메인 서버 응답의 상태값을 내부 CorrectionStatus.value(lower_case)로 정규화"""
    return raw.lower() if raw else ""


class CorrectionService:
    """
    첨삭 생성 전 과정을 조율하는 서비스

    DB 직접 접근 대신 메인 서버 API(httpx)를 통해 데이터를 읽고 쓴다.
    상태 전이는 메인 서버가 원자적으로 처리한다.
    """

    def __init__(
        self,
        correction_client: CorrectionClient,
        portfolio_client: PortfolioClient,
        generator: CorrectionGenerator,
        rag_pipeline: RAGPipeline,
    ) -> None:
        self._correction_client = correction_client
        self._portfolio_client = portfolio_client
        self._generator = generator
        self._rag_pipeline = rag_pipeline

    # ------------------------------------------------------------------
    # RAG 단계
    # ------------------------------------------------------------------

    async def start_rag(self, correction_id: int, background_tasks: BackgroundTasks) -> None:
        """RAG 단계를 시작하고 백그라운드 작업을 등록"""
        logger.info("RAG 시작 요청 (correction_id: %s)", correction_id)
        await self._correction_client.update_status(
            correction_id, _to_upper_status(CorrectionStatus.DOING_RAG)
        )
        logger.info("상태 DOING_RAG 전이 완료 (correction_id: %s)", correction_id)
        background_tasks.add_task(self._run_rag, correction_id)

    async def _run_rag(self, correction_id: int) -> None:
        """RAG 실행 후 기업 인사이트를 저장"""
        try:
            correction = await self._correction_client.get_correction(correction_id)

            logger.info(
                "get_correction 응답 keys (correction_id: %s): %s",
                correction_id,
                list(correction.keys()),
            )

            company_name = correction["companyName"]
            job_title = correction["positionName"]
            job_description = correction["jobDescription"]

            rag_result = await self._rag_pipeline.run(
                company_name,
                job_title,
                job_description,
            )
            search_query = (
                ", ".join(rag_result.keywords)
                if rag_result.keywords
                else f"{company_name} {job_title}"
            )
            await self._correction_client.save_rag_data(
                correction_id,
                search_query,
                {
                    "keywords": rag_result.keywords,
                    "results": rag_result.search_results,
                },
            )
            await self._correction_client.update_company_insight(correction_id, rag_result.insight)
        except Exception as exc:
            if isinstance(exc, RAGKeywordExtractionError):
                logger.exception("RAG 키워드 추출 실패 (correction_id: %s)", correction_id)
            elif isinstance(exc, RAGSearchError):
                logger.exception("RAG 검색 실패 (correction_id: %s)", correction_id)
            elif isinstance(exc, RAGInsightGenerationError):
                logger.exception("RAG 인사이트 생성 실패 (correction_id: %s)", correction_id)
            else:
                logger.exception("RAG 처리 실패 (correction_id: %s)", correction_id)
            await self._mark_failed(correction_id)

    # ------------------------------------------------------------------
    # RAG 데이터 파싱 유틸리티
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_search_results(rag_data: dict) -> list[dict]:
        """rag_data에서 검색 결과 목록 추출"""
        stored = rag_data.get("searchResults")

        if isinstance(stored, dict):
            results = stored.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
            return []

        if isinstance(stored, list):
            return [item for item in stored if isinstance(item, dict)]

        return []

    @staticmethod
    def _extract_keywords(rag_data: dict) -> list[str]:
        """rag_data에서 키워드 목록 추출"""
        stored = rag_data.get("searchResults")
        if isinstance(stored, dict):
            keywords = stored.get("keywords")
            if isinstance(keywords, list):
                normalized: list[str] = []
                for item in keywords:
                    keyword = str(item).strip()
                    if keyword:
                        normalized.append(keyword)
                if normalized:
                    return normalized

        search_query = rag_data.get("searchQuery")
        if isinstance(search_query, str):
            return [keyword.strip() for keyword in search_query.split(",") if keyword.strip()]

        return []

    async def _run_rag_from_search_results(
        self,
        correction_id: int,
        search_results: list[dict],
        keywords: list[str],
    ) -> None:
        """저장된 RAG 검색 결과로 인사이트만 재생성"""
        try:
            correction = await self._correction_client.get_correction(correction_id)

            insight = await self._rag_pipeline.run_from_search_results(
                search_results=search_results,
                company_name=correction["companyName"],
                job_title=correction["positionName"],
                keywords=keywords,
            )
            await self._correction_client.update_company_insight(correction_id, insight)
        except Exception:
            logger.exception(
                "저장된 검색 결과 기반 RAG 재시도 실패 (correction_id: %s)",
                correction_id,
            )
            await self._mark_failed(correction_id)

    # ------------------------------------------------------------------
    # 생성 단계
    # ------------------------------------------------------------------

    async def start_generation(self, correction_id: int, background_tasks: BackgroundTasks) -> None:
        """첨삭 생성 단계를 시작하고 백그라운드 작업을 등록"""
        logger.info("첨삭 생성 시작 요청 (correction_id: %s)", correction_id)
        await self._correction_client.update_status(
            correction_id, _to_upper_status(CorrectionStatus.GENERATING)
        )
        logger.info("상태 GENERATING 전이 완료 (correction_id: %s)", correction_id)
        background_tasks.add_task(self._run_generation, correction_id)

    async def _generate_portfolio_correction(
        self,
        *,
        correction_id: int,
        portfolio_id: int,
        index: int,
        total: int,
        company_name: str,
        job_title: str,
        job_description: str,
        company_insight: str,
        emphasis_points: str,
    ) -> PortfolioCorrectionResult:
        """단일 포트폴리오 첨삭을 생성한다."""
        started_at = time.perf_counter()
        logger.info(
            "포트폴리오 첨삭 생성 시작 (%s/%s, portfolio_id: %s, correction_id: %s)",
            index,
            total,
            portfolio_id,
            correction_id,
        )

        portfolio = await self._portfolio_client.get_portfolio(portfolio_id)
        portfolio_output = {
            "description": _get_portfolio_text_field(portfolio, "description"),
            "contributions": _get_portfolio_text_field(
                portfolio, "responsibilities", "contributions", "responsibility"
            ),
            "achievements": _get_portfolio_text_field(
                portfolio, "problemSolving", "problem_solving", "achievements"
            ),
            "insights": _get_portfolio_text_field(portfolio, "learnings", "learning", "insights"),
        }
        logger.debug(
            "포트폴리오 첨삭 입력 요약 (correction_id: %s, portfolio_id: %s, description_length: %s, contributions_length: %s, achievements_length: %s, insights_length: %s)",
            correction_id,
            portfolio_id,
            len(portfolio_output["description"]),
            len(portfolio_output["contributions"]),
            len(portfolio_output["achievements"]),
            len(portfolio_output["insights"]),
        )

        generated = await asyncio.to_thread(
            self._generator.generate,
            company_name,
            job_title,
            job_description,
            company_insight,
            portfolio_output,
            emphasis_points,
        )
        elapsed = time.perf_counter() - started_at
        logger.info(
            "포트폴리오 첨삭 생성 완료 (%s/%s, portfolio_id: %s, correction_id: %s, 소요: %.1f초)",
            index,
            total,
            portfolio_id,
            correction_id,
            elapsed,
        )
        return PortfolioCorrectionResult(portfolio_id=portfolio_id, fields=generated.fields)

    async def _run_generation(self, correction_id: int) -> None:
        """첨삭 생성기를 호출하고 결과를 저장"""
        try:
            logger.info("백그라운드 첨삭 생성 시작 (correction_id: %s)", correction_id)
            correction = await self._correction_client.get_correction(correction_id)
            company_name = correction.get("companyName")
            job_title = correction.get("positionName")
            job_description = correction.get("jobDescription")

            if not company_name or not job_title or not job_description:
                raise ValueError("첨삭 생성에 필요한 회사명, 직무명, 채용 공고가 누락되었습니다.")

            company_insight = correction.get("companyInsight") or ""
            if isinstance(company_insight, dict):
                company_insight = json.dumps(company_insight, ensure_ascii=False)

            emphasis_points = correction.get("highlightPoint") or ""
            if isinstance(emphasis_points, dict):
                emphasis_points = json.dumps(emphasis_points, ensure_ascii=False)

            portfolio_ids = correction.get("portfolioIds") or []
            if not portfolio_ids:
                raise ValueError("포트폴리오 ID가 없습니다.")
            if len(portfolio_ids) > _MAX_PORTFOLIO_COUNT:
                raise ValueError(f"포트폴리오는 최대 {_MAX_PORTFOLIO_COUNT}개까지 허용됩니다.")

            logger.info(
                "첨삭 데이터 조회 완료 (correction_id: %s, company: %s, portfolios: %s개)",
                correction_id,
                company_name,
                len(portfolio_ids),
            )
            logger.debug(
                "첨삭 생성 상세 변수 (correction_id: %s, portfolio_ids: %s, company_insight_length: %s, emphasis_points_length: %s)",
                correction_id,
                portfolio_ids,
                len(company_insight),
                len(emphasis_points),
            )

            generation_results = await asyncio.gather(
                *[
                    self._generate_portfolio_correction(
                        correction_id=correction_id,
                        portfolio_id=portfolio_id,
                        index=index,
                        total=len(portfolio_ids),
                        company_name=company_name,
                        job_title=job_title,
                        job_description=job_description,
                        company_insight=company_insight,
                        emphasis_points=emphasis_points,
                    )
                    for index, portfolio_id in enumerate(portfolio_ids, start=1)
                ],
                return_exceptions=True,
            )

            portfolio_corrections: list[PortfolioCorrectionResult] = []
            generation_errors: list[Exception] = []
            for portfolio_id, result in zip(portfolio_ids, generation_results, strict=True):
                if isinstance(result, Exception):
                    logger.exception(
                        "포트폴리오 첨삭 생성 실패 (correction_id: %s, portfolio_id: %s)",
                        correction_id,
                        portfolio_id,
                        exc_info=result,
                    )
                    generation_errors.append(result)
                    continue

                portfolio_corrections.append(result)

            if generation_errors:
                raise RuntimeError(
                    f"포트폴리오 첨삭 생성 중 {len(generation_errors)}건의 실패가 발생했습니다."
                )

            logger.info("총평 생성 시작 (correction_id: %s)", correction_id)
            overall_summary = await asyncio.to_thread(
                self._generator.generate_overall_summary,
                company_name,
                job_title,
                job_description,
                company_insight,
                portfolio_corrections,
                emphasis_points,
            )
            logger.info("총평 생성 완료 (correction_id: %s)", correction_id)
            result = CorrectionOutput(
                portfolio_corrections=portfolio_corrections,
                overall_summary=overall_summary,
            )

            result_for_server = self._convert_result_for_server(result)
            await self._correction_client.update_result(
                correction_id,
                result_for_server,
                result.overall_summary,
            )
            logger.info("첨삭 결과 저장 완료 (correction_id: %s)", correction_id)
        except Exception as exc:
            logger.exception("첨삭 생성 실패 (correction_id: %s): %s", correction_id, exc)
            await self._mark_failed(correction_id)

    _FIELD_NAME_MAP: dict[str, str] = {
        "description": "description",
        "contributions": "responsibilities",
        "achievements": "problemSolving",
        "insights": "learnings",
    }

    @staticmethod
    def _normalize_comment_for_server(line_type: str, comment: str | None) -> str | None:
        """메인 서버 전송용 코멘트 포맷을 최소 정규화한다."""
        marker = "수정 예시:"
        if line_type != "emphasize" or not comment or marker not in comment:
            return comment

        marker_index = comment.find(marker)
        if marker_index <= 0 or re.search(r"\n[ \t]*$", comment[:marker_index]):
            return comment

        return f"{comment[:marker_index]}\n{comment[marker_index:]}"

    @staticmethod
    def _convert_result_for_server(result: CorrectionOutput) -> list[dict]:
        """CorrectionOutput을 메인 서버 result 배열 포맷으로 변환"""
        if hasattr(result, "model_dump"):
            result_dict = result.model_dump()
        elif isinstance(result, dict):
            result_dict = result
        else:
            raise ValueError("첨삭 결과 형식이 올바르지 않습니다.")

        converted_results: list[dict] = []
        for portfolio_correction in result_dict.get("portfolio_corrections", []):
            converted_portfolio = {"portfolioId": portfolio_correction["portfolio_id"]}
            for field in portfolio_correction.get("fields", []):
                converted_portfolio[_FIELD_NAME_TO_SERVER[field["field_name"]]] = {
                    "lines": [
                        {
                            "lineNumber": line["line_number"],
                            "originalText": line["original_text"],
                            "type": line["type"],
                            "comment": CorrectionService._normalize_comment_for_server(
                                line["type"], line["comment"]
                            ),
                        }
                        for line in field.get("lines", [])
                    ]
                }
            converted_results.append(converted_portfolio)

        return converted_results

    # ------------------------------------------------------------------
    # 재시도
    # ------------------------------------------------------------------

    async def retry(self, correction_id: int, background_tasks: BackgroundTasks) -> None:
        """
        실패한 첨삭을 재시도한다.

        company_insight 유무와 rag_data 유무에 따라 재시도 경로를 결정:
        - company_insight + rag_data 있음 -> 생성부터 재시도
        - rag_data만 있음 (검색 결과 존재) -> 저장된 검색 결과로 인사이트 재생성
        - 그 외 -> 전체 RAG부터 재시도
        """
        correction = await self._correction_client.get_correction(correction_id)
        status_value = _normalize_status(correction.get("status", ""))

        if status_value != CorrectionStatus.FAILED.value:
            raise ValueError("실패 상태가 아닌 첨삭은 재시도할 수 없습니다.")

        company_insight = correction.get("companyInsight")

        # 1) company_insight + rag_data 모두 있으면 생성부터 재시도
        if company_insight:
            rag_data = await self._correction_client.get_rag_data(correction_id)
            if rag_data:
                await self._correction_client.update_status(
                    correction_id, _to_upper_status(CorrectionStatus.GENERATING)
                )
                background_tasks.add_task(self._run_generation, correction_id)
                return

        # 2) 저장된 RAG 데이터가 있으면 검색 결과로 인사이트만 재생성
        rag_data = await self._correction_client.get_rag_data(correction_id)
        if rag_data:
            search_results = self._extract_search_results(rag_data)
            if search_results:
                keywords = self._extract_keywords(rag_data)
                await self._correction_client.update_status(
                    correction_id, _to_upper_status(CorrectionStatus.DOING_RAG)
                )
                background_tasks.add_task(
                    self._run_rag_from_search_results,
                    correction_id,
                    search_results,
                    keywords,
                )
                return

        # 3) 전체 RAG부터 재시도
        await self._correction_client.update_status(
            correction_id, _to_upper_status(CorrectionStatus.DOING_RAG)
        )
        background_tasks.add_task(self._run_rag, correction_id)

    # ------------------------------------------------------------------
    # 공통 유틸리티
    # ------------------------------------------------------------------

    async def _mark_failed(self, correction_id: int) -> None:
        """실패 상태 업데이트를 시도하고 실패 시 로깅"""
        try:
            await self._correction_client.update_status(
                correction_id, _to_upper_status(CorrectionStatus.FAILED)
            )
        except Exception as exc:
            logger.warning(
                "실패 상태 업데이트를 건너뜁니다 (correction_id: %s): %s",
                correction_id,
                exc,
            )


def get_correction_service() -> CorrectionService:
    """CorrectionService 싱글톤 반환"""
    global _service

    if _service is None:
        _service = CorrectionService(
            correction_client=get_correction_client(),
            portfolio_client=get_portfolio_client(),
            generator=get_correction_generator(),
            rag_pipeline=RAGPipeline(),
        )

    return _service


def init_correction_service(
    correction_client: CorrectionClient,
    portfolio_client: PortfolioClient,
    generator: CorrectionGenerator,
    rag_pipeline: RAGPipeline,
) -> CorrectionService:
    """CorrectionService 싱글톤 초기화 (테스트용)"""
    global _service

    _service = CorrectionService(
        correction_client=correction_client,
        portfolio_client=portfolio_client,
        generator=generator,
        rag_pipeline=rag_pipeline,
    )
    return _service


def reset_correction_service() -> None:
    """CorrectionService 싱글톤 리셋 (테스트용)"""
    global _service
    _service = None


__all__ = [
    "CorrectionService",
    "get_correction_service",
    "init_correction_service",
    "reset_correction_service",
]
