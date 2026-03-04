"""첨삭 서비스 오케스트레이션"""

import asyncio
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
        try:
            correction = await self._repository.get_by_id(correction_id)
            if correction is None:
                raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

            company_name = correction["company_name"]
            job_title = correction["job_title"]
            job_description = correction["job_description"]

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
            await self._repository.save_rag_data(
                correction_id,
                search_query,
                {
                    "keywords": rag_result.keywords,
                    "results": rag_result.search_results,
                },
            )
            await self._repository.update_company_insight(correction_id, rag_result.insight)

            await self._repository.update_status(
                correction_id, CorrectionStatus.COMPANY_INSIGHT.value
            )
        except Exception as exc:
            logger.exception("RAG 처리 실패 (correction_id: %s): %s", correction_id, exc)
            await self._mark_failed(correction_id)

    @staticmethod
    def _extract_search_results(rag_data: dict) -> list[dict]:
        """rag_data row에서 검색 결과 목록 추출"""
        stored_search_results = rag_data.get("search_results")

        if isinstance(stored_search_results, dict):
            results = stored_search_results.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
            return []

        if isinstance(stored_search_results, list):
            return [item for item in stored_search_results if isinstance(item, dict)]

        return []

    @staticmethod
    def _extract_keywords(rag_data: dict) -> list[str]:
        """rag_data row에서 키워드 목록 추출"""
        stored_search_results = rag_data.get("search_results")
        if isinstance(stored_search_results, dict):
            keywords = stored_search_results.get("keywords")
            if isinstance(keywords, list):
                normalized_keywords: list[str] = []
                for item in keywords:
                    keyword = str(item).strip()
                    if keyword:
                        normalized_keywords.append(keyword)
                if normalized_keywords:
                    return normalized_keywords

        search_query = rag_data.get("search_query")
        if isinstance(search_query, str):
            return [keyword.strip() for keyword in search_query.split(",") if keyword.strip()]

        return []

    async def _run_rag_from_search_results(
        self,
        correction_id: str,
        search_results: list[dict],
        keywords: list[str],
    ) -> None:
        """저장된 RAG 검색 결과로 인사이트만 재생성"""
        try:
            correction = await self._repository.get_by_id(correction_id)
            if correction is None:
                raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

            insight = await self._rag_pipeline.run_from_search_results(
                search_results=search_results,
                company_name=correction["company_name"],
                job_title=correction["job_title"],
                keywords=keywords,
            )
            await self._repository.update_company_insight(correction_id, insight)
            await self._repository.update_status(
                correction_id, CorrectionStatus.COMPANY_INSIGHT.value
            )
        except Exception as exc:
            logger.exception(
                "저장된 검색 결과 기반 RAG 재시도 실패 (correction_id: %s): %s",
                correction_id,
                exc,
            )
            await self._mark_failed(correction_id)

    async def retry(self, correction_id: str, background_tasks: BackgroundTasks) -> None:
        """실패 상태의 첨삭을 단계에 맞게 재시도"""
        correction = await self._repository.get_by_id(correction_id)
        if correction is None:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

        if correction.get("status") != CorrectionStatus.FAILED.value:
            raise ValueError("현재 상태에서는 재시도를 시작할 수 없습니다.")

        if correction.get("company_insight") is not None:
            await self._repository.update_status(
                correction_id, CorrectionStatus.COMPANY_INSIGHT.value
            )
            await self.start_generation(correction_id, background_tasks)
            return

        rag_data_rows = await self._repository.get_rag_data(correction_id)
        if rag_data_rows:
            latest_rag_data = rag_data_rows[-1]
            search_results = self._extract_search_results(latest_rag_data)
            if search_results:
                keywords = self._extract_keywords(latest_rag_data)
                await self._repository.update_status(
                    correction_id, CorrectionStatus.DOING_RAG.value
                )
                background_tasks.add_task(
                    self._run_rag_from_search_results,
                    correction_id,
                    search_results,
                    keywords,
                )
                return

        await self._repository.update_status(correction_id, CorrectionStatus.NOT_STARTED.value)
        await self.start_rag(correction_id, background_tasks)

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
        try:
            correction = await self._repository.get_by_id(correction_id)
            if correction is None:
                raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")

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

            result = await asyncio.to_thread(
                self._generator.generate,
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
            await self._repository.update_status(correction_id, CorrectionStatus.DONE.value)
        except Exception as exc:
            logger.exception("첨삭 생성 실패 (correction_id: %s): %s", correction_id, exc)
            await self._mark_failed(correction_id)

    async def _mark_failed(self, correction_id: str) -> None:
        """실패 상태 업데이트를 시도하고 실패 시 로깅"""
        try:
            await self._repository.update_status(correction_id, CorrectionStatus.FAILED.value)
        except Exception as exc:
            logger.warning(
                "실패 상태 업데이트를 건너뜁니다 (correction_id: %s): %s",
                correction_id,
                exc,
            )

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
