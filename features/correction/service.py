"""첨삭 서비스 오케스트레이션 (메인 서버 API 호출 기반)"""

import asyncio
import json
import logging

from fastapi import BackgroundTasks

from common.clients.correction_client import CorrectionClient, get_correction_client
from common.clients.portfolio_client import PortfolioClient, get_portfolio_client

from .generator import CorrectionGenerator, get_correction_generator
from .rag.pipeline import RAGPipeline
from .schemas import CorrectionStatus

logger = logging.getLogger(__name__)

_service: "CorrectionService | None" = None


def _to_upper_status(status: CorrectionStatus) -> str:
    """CorrectionStatus enum을 메인 서버가 기대하는 UPPER_CASE 문자열로 변환"""
    return status.value.upper()


def _normalize_status(raw: str) -> str:
    """메인 서버 응답의 상태값을 내부 CorrectionStatus.value(lower_case)로 정규화"""
    return raw.lower()


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
        await self._correction_client.update_status(
            correction_id, _to_upper_status(CorrectionStatus.DOING_RAG)
        )
        background_tasks.add_task(self._run_rag, correction_id)

    async def _run_rag(self, correction_id: int) -> None:
        """RAG 실행 후 기업 인사이트를 저장"""
        try:
            correction = await self._correction_client.get_correction(correction_id)

            company_name = correction["companyName"]
            job_title = correction["positionName"]
            job_description = correction["jobDescription"]

            company_insight = await self._rag_pipeline.run(
                company_name,
                job_title,
                job_description,
            )

            keywords = await asyncio.to_thread(
                self._rag_pipeline._extract_keywords,
                company_name=company_name,
                job_title=job_title,
                job_description=job_description,
            )
            search_query = keywords[0] if keywords else f"{company_name} {job_title}"
            search_results = await self._rag_pipeline._search(query=search_query)
            await self._correction_client.save_rag_data(
                correction_id,
                search_query,
                search_results,
            )

            await self._correction_client.update_company_insight(correction_id, company_insight)
        except Exception as exc:
            logger.exception("RAG 처리 실패 (correction_id: %s): %s", correction_id, exc)
            await self._mark_failed(correction_id)

    # ------------------------------------------------------------------
    # 생성 단계
    # ------------------------------------------------------------------

    async def start_generation(self, correction_id: int, background_tasks: BackgroundTasks) -> None:
        """첨삭 생성 단계를 시작하고 백그라운드 작업을 등록"""
        await self._correction_client.update_status(
            correction_id, _to_upper_status(CorrectionStatus.GENERATING)
        )
        background_tasks.add_task(self._run_generation, correction_id)

    async def _run_generation(self, correction_id: int) -> None:
        """첨삭 생성기를 호출하고 결과를 저장"""
        try:
            correction = await self._correction_client.get_correction(correction_id)

            company_insight = correction.get("companyInsight") or ""
            if isinstance(company_insight, dict):
                company_insight = json.dumps(company_insight, ensure_ascii=False)

            emphasis_points = correction.get("highlightPoint") or ""
            if isinstance(emphasis_points, dict):
                emphasis_points = json.dumps(emphasis_points, ensure_ascii=False)

            portfolio_ids = correction.get("portfolioIds") or []
            if not portfolio_ids:
                raise ValueError("포트폴리오 ID가 없습니다.")

            portfolio = await self._portfolio_client.get_portfolio(portfolio_ids[0])
            portfolio_output = {
                "description": portfolio.get("description", ""),
                "contributions": portfolio.get("responsibilities", ""),
                "achievements": portfolio.get("problemSolving", ""),
                "insights": portfolio.get("learnings", ""),
            }

            result = await asyncio.to_thread(
                self._generator.generate,
                correction.get("companyName", ""),
                correction.get("positionName", ""),
                correction.get("jobDescription", ""),
                company_insight,
                portfolio_output,
                emphasis_points,
            )

            result_for_server = self._convert_result_for_server(result)
            await self._correction_client.update_result(correction_id, result_for_server)
        except Exception as exc:
            logger.exception("첨삭 생성 실패 (correction_id: %s): %s", correction_id, exc)
            await self._mark_failed(correction_id)

    @staticmethod
    def _convert_result_for_server(result) -> list[dict]:
        """CorrectionOutput을 메인 서버 배열 포맷으로 변환"""
        if hasattr(result, "model_dump"):
            result_dict = result.model_dump()
        elif isinstance(result, dict):
            result_dict = result
        else:
            raise ValueError("첨삭 결과 형식이 올바르지 않습니다.")

        return [result_dict]

    # ------------------------------------------------------------------
    # 재시도
    # ------------------------------------------------------------------

    async def retry(self, correction_id: int, background_tasks: BackgroundTasks) -> None:
        """
        실패한 첨삭을 재시도한다.

        company_insight 유무와 rag_data 유무에 따라 재시도 경로를 결정:
        - company_insight 없음 -> RAG부터 재시도
        - company_insight 있고 rag_data 없음 -> RAG부터 재시도
        - company_insight 있고 rag_data 있음 -> 생성부터 재시도
        """
        correction = await self._correction_client.get_correction(correction_id)
        status_value = _normalize_status(correction.get("status", ""))

        if status_value != CorrectionStatus.FAILED.value:
            raise ValueError("실패 상태가 아닌 첨삭은 재시도할 수 없습니다.")

        company_insight = correction.get("companyInsight")
        if company_insight:
            rag_data = await self._correction_client.get_rag_data(correction_id)
            if rag_data:
                await self._correction_client.update_status(
                    correction_id, _to_upper_status(CorrectionStatus.GENERATING)
                )
                background_tasks.add_task(self._run_generation, correction_id)
                return

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
