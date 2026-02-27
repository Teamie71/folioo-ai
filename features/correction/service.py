"""첨삭 서비스 오케스트레이션"""

import logging

from fastapi import BackgroundTasks

from .generator import CorrectionGenerator, get_correction_generator
from .rag.pipeline import RAGPipeline
from .repository import CorrectionRepository, get_correction_repository
from .schemas import CorrectionStatus

logger = logging.getLogger(__name__)

_service: "CorrectionService | None" = None


class CorrectionService:
    """첨삭 생성 전 과정을 조율하는 서비스"""

    def __init__(
        self,
        repository: CorrectionRepository,
        generator: CorrectionGenerator,
        rag_pipeline: RAGPipeline,
    ) -> None:
        self._repository = repository
        self._generator = generator
        self._rag_pipeline = rag_pipeline

    async def create_correction(
        self,
        portfolio_id: str,
        user_id: str,
        company_name: str,
        job_title: str,
        job_description: str,
    ) -> dict:
        """첨삭 레코드를 생성하고 생성된 row를 반환"""
        return await self._repository.create(
            portfolio_id=portfolio_id,
            user_id=user_id,
            company_name=company_name,
            job_title=job_title,
            job_description=job_description,
        )

    async def start_rag(self, correction_id: str, background_tasks: BackgroundTasks) -> None:
        """RAG 단계를 시작하고 백그라운드 작업을 등록"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

        if correction.get("status") != CorrectionStatus.NOT_STARTED.value:
            return

        await self._repository.update_status(correction_id, CorrectionStatus.DOING_RAG.value)
        background_tasks.add_task(self._run_rag, correction_id)

    async def _run_rag(self, correction_id: str) -> None:
        """RAG 실행 후 기업 인사이트를 저장"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

        try:
            company_name = correction["company_name"]
            job_title = correction["job_title"]
            job_description = correction["job_description"]

            company_insight = self._rag_pipeline.run(company_name, job_title, job_description)
            await self._repository.update_company_insight(correction_id, company_insight)

            if hasattr(self._rag_pipeline, "_extract_keywords") and hasattr(self._rag_pipeline, "_search"):
                keywords = self._rag_pipeline._extract_keywords(
                    company_name=company_name,
                    job_title=job_title,
                    job_description=job_description,
                )
                search_query = keywords[0] if keywords else f"{company_name} {job_title}"
                search_results = self._rag_pipeline._search(query=search_query)
                await self._repository.save_rag_data(
                    correction_id,
                    search_query,
                    {"results": search_results},
                )

            await self._repository.update_status(correction_id, CorrectionStatus.COMPANY_INSIGHT.value)
        except Exception as exc:
            logger.exception("RAG 처리 실패 (correction_id: %s): %s", correction_id, exc)
            await self._repository.update_status(correction_id, CorrectionStatus.FAILED.value)

    async def start_generation(self, correction_id: str, background_tasks: BackgroundTasks) -> None:
        """첨삭 생성 단계를 시작하고 백그라운드 작업을 등록"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

        if correction.get("status") != CorrectionStatus.COMPANY_INSIGHT.value:
            return

        await self._repository.update_status(correction_id, CorrectionStatus.GENERATING.value)
        background_tasks.add_task(self._run_generation, correction_id)

    async def _run_generation(self, correction_id: str) -> None:
        """첨삭 생성기를 호출하고 결과를 저장"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

        try:
            from features.portfolio import get_portfolio_service

            portfolio_result = await get_portfolio_service().get_result(correction["portfolio_id"])
            if portfolio_result is None or portfolio_result.output is None:
                raise ValueError("포트폴리오 결과를 찾을 수 없습니다.")

            if hasattr(portfolio_result.output, "model_dump"):
                portfolio_output = portfolio_result.output.model_dump()
            elif isinstance(portfolio_result.output, dict):
                portfolio_output = portfolio_result.output
            else:
                raise ValueError("포트폴리오 출력 형식이 올바르지 않습니다.")

            result = self._generator.generate(
                correction["company_name"],
                correction["job_title"],
                correction["job_description"],
                correction.get("company_insight") or "",
                portfolio_output,
                correction.get("emphasis_points") or "",
            )
            if hasattr(result, "model_dump"):
                result_dict = result.model_dump()
            elif isinstance(result, dict):
                result_dict = result
            else:
                raise ValueError("첨삭 결과 형식이 올바르지 않습니다.")
            await self._repository.update_result(correction_id, result_dict)
        except Exception as exc:
            logger.exception("첨삭 생성 실패 (correction_id: %s): %s", correction_id, exc)
            await self._repository.update_status(correction_id, CorrectionStatus.FAILED.value)

    async def get_correction(self, correction_id: str) -> dict | None:
        """첨삭 전체 데이터 조회"""
        return await self._repository.get_by_id(correction_id)

    async def get_status(self, correction_id: str) -> CorrectionStatus:
        """첨삭 상태 조회"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")
        return CorrectionStatus(correction["status"])

    async def get_company_insight(self, correction_id: str) -> str | None:
        """기업 인사이트 조회"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")
        return correction.get("company_insight")

    async def update_company_insight(self, correction_id: str, company_insight: str) -> None:
        """기업 인사이트 수정"""
        await self._repository.update_company_insight(correction_id, company_insight)

    async def update_emphasis_points(self, correction_id: str, emphasis_points: str) -> None:
        """강조 포인트 수정"""
        await self._repository.update_emphasis_points(correction_id, emphasis_points)

    async def delete_correction(self, correction_id: str) -> None:
        """첨삭 삭제"""
        await self._repository.delete(correction_id)


def get_correction_service() -> CorrectionService:
    """CorrectionService 싱글톤 반환"""
    global _service

    if _service is None:
        _service = CorrectionService(
            repository=get_correction_repository(),
            generator=get_correction_generator(),
            rag_pipeline=RAGPipeline(),
        )

    return _service


def init_correction_service(
    repository: CorrectionRepository,
    generator: CorrectionGenerator,
    rag_pipeline: RAGPipeline,
) -> CorrectionService:
    """CorrectionService 싱글톤 초기화"""
    global _service

    _service = CorrectionService(
        repository=repository,
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
