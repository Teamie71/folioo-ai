"""첨삭 서비스 테스트"""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
import types

import pytest


def _install_dummy_langchain_openai():
    """테스트용 langchain_openai 더미 모듈 설치"""
    dummy_module = types.ModuleType("langchain_openai")

    class DummyChatOpenAI:  # pragma: no cover - 간단 더미
        def __init__(self, *args, **kwargs):
            pass

    dummy_module.ChatOpenAI = DummyChatOpenAI
    sys.modules.setdefault("langchain_openai", dummy_module)


_install_dummy_langchain_openai()

correction_service_module = importlib.import_module("features.correction.service")
RAGRunResult = importlib.import_module("features.correction.rag.pipeline").RAGRunResult
CorrectionStatus = importlib.import_module("features.correction.schemas").CorrectionStatus
CorrectionService = correction_service_module.CorrectionService
get_correction_service = correction_service_module.get_correction_service
init_correction_service = correction_service_module.init_correction_service
reset_correction_service = correction_service_module.reset_correction_service


class DummyRepository:
    """CorrectionRepository 대체 더미"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.rag_data_rows: dict[str, list[dict]] = {}
        self.created_payload: dict | None = None
        self.saved_rag_data: list[dict] = []
        self.updated_result: dict | None = None
        self.updated_statuses: list[dict] = []

    async def create(
        self,
        portfolio_id: str,
        user_id: str,
        company_name: str,
        job_title: str,
        job_description: str,
    ) -> dict:
        self.created_payload = {
            "portfolio_id": portfolio_id,
            "user_id": user_id,
            "company_name": company_name,
            "job_title": job_title,
            "job_description": job_description,
        }
        row = {
            "id": "c-1",
            "portfolio_id": portfolio_id,
            "user_id": user_id,
            "company_name": company_name,
            "job_title": job_title,
            "job_description": job_description,
            "status": CorrectionStatus.NOT_STARTED.value,
            "company_insight": None,
            "emphasis_points": None,
            "result": None,
        }
        self.rows[row["id"]] = row
        return row

    async def get_by_id(self, correction_id: str) -> dict | None:
        return self.rows.get(correction_id)

    async def update_status(self, correction_id: str, status: str) -> None:
        if correction_id not in self.rows:
            raise ValueError(f"존재하지 않는 첨삭 ID입니다: {correction_id}")
        self.rows[correction_id]["status"] = status
        self.updated_statuses.append({"correction_id": correction_id, "status": status})

    async def update_company_insight(self, correction_id: str, company_insight: str) -> None:
        if correction_id not in self.rows:
            raise ValueError(f"존재하지 않는 첨삭 ID입니다: {correction_id}")
        self.rows[correction_id]["company_insight"] = company_insight

    async def update_emphasis_points(self, correction_id: str, emphasis_points: str) -> None:
        if correction_id not in self.rows:
            raise ValueError(f"존재하지 않는 첨삭 ID입니다: {correction_id}")
        self.rows[correction_id]["emphasis_points"] = emphasis_points

    async def update_result(self, correction_id: str, result: dict) -> None:
        if correction_id not in self.rows:
            raise ValueError(f"존재하지 않는 첨삭 ID입니다: {correction_id}")
        self.updated_result = result
        self.rows[correction_id]["result"] = result

    async def delete(self, correction_id: str) -> None:
        self.rows.pop(correction_id, None)

    async def save_rag_data(
        self, correction_id: str, search_query: str, search_results: dict
    ) -> None:
        rag_data = {
            "correction_id": correction_id,
            "search_query": search_query,
            "search_results": search_results,
        }
        self.saved_rag_data.append(rag_data)
        self.rag_data_rows.setdefault(correction_id, []).append(rag_data)

    async def get_rag_data(self, correction_id: str) -> list[dict]:
        return self.rag_data_rows.get(correction_id, [])


class DummyGenerator:
    """CorrectionGenerator 대체 더미"""

    def __init__(self, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def generate(
        self,
        company_name: str,
        job_title: str,
        job_description: str,
        company_insight: str,
        portfolio_output: dict,
        emphasis_points: str,
    ) -> dict:
        self.calls.append(
            {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
                "company_insight": company_insight,
                "portfolio_output": portfolio_output,
                "emphasis_points": emphasis_points,
            }
        )
        if self.raise_error:
            raise RuntimeError("생성 실패")
        return {"fields": [], "overall_summary": "완료"}


class DummyRagPipeline:
    """RAGPipeline 대체 더미"""

    def __init__(self, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.run_calls: list[dict] = []
        self.run_from_search_results_calls: list[dict] = []

    async def run(self, company_name: str, job_title: str, job_description: str) -> RAGRunResult:
        self.run_calls.append(
            {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
            }
        )
        if self.raise_error:
            raise RuntimeError("RAG 실패")
        search_query = f"{company_name} {job_title}"
        return RAGRunResult(
            keywords=[search_query],
            search_results=[
                {
                    "query": search_query,
                    "title": "검색 결과",
                }
            ],
            insight="기업 인사이트",
        )

    async def run_from_search_results(
        self,
        search_results: list[dict],
        company_name: str,
        job_title: str,
    ) -> str:
        self.run_from_search_results_calls.append(
            {
                "search_results": search_results,
                "company_name": company_name,
                "job_title": job_title,
            }
        )
        if self.raise_error:
            raise RuntimeError("RAG 실패")
        return "재생성 기업 인사이트"


class DummyBackgroundTasks:
    """BackgroundTasks 대체 더미"""

    def __init__(self) -> None:
        self.tasks: list[tuple] = []

    def add_task(self, func, *args) -> None:
        self.tasks.append((func, args))


class DummyPortfolioOutput:
    """포트폴리오 output 더미"""

    def model_dump(self) -> dict:
        return {
            "description": "설명",
            "contributions": "기여",
            "achievements": "성과",
            "insights": "인사이트",
        }


class DummyPortfolioResult:
    """포트폴리오 결과 더미"""

    def __init__(self, output: DummyPortfolioOutput | None) -> None:
        self.output = output


class DummyPortfolioService:
    """PortfolioService 대체 더미"""

    def __init__(self, result: DummyPortfolioResult | None) -> None:
        self.result = result

    async def get_result(self, _portfolio_id: str):
        return self.result


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_correction_service()
    yield
    reset_correction_service()


@pytest.mark.asyncio
async def test_create_correction_returns_repository_result():
    """create_correction은 repository.create 결과를 그대로 반환한다."""
    repository = DummyRepository()
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())

    row = await service.create_correction("p-1", "u-1", "회사", "백엔드", "JD")

    assert row["id"] == "c-1"
    assert repository.created_payload == {
        "portfolio_id": "p-1",
        "user_id": "u-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
    }


@pytest.mark.asyncio
async def test_start_rag_updates_status_and_registers_task():
    """start_rag는 doing_rag로 상태 변경 후 백그라운드 작업을 등록한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.NOT_STARTED.value,
    }
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())
    background_tasks = DummyBackgroundTasks()

    await service.start_rag("c-1", background_tasks)  # type: ignore[arg-type]

    assert repository.rows["c-1"]["status"] == CorrectionStatus.DOING_RAG.value
    assert len(background_tasks.tasks) == 1
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_rag
    assert task_args == ("c-1",)


@pytest.mark.asyncio
async def test_run_rag_success_saves_company_insight_and_rag_data():
    """_run_rag 성공 시 인사이트/RAG 데이터 저장 후 상태를 company_insight로 변경한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.DOING_RAG.value,
        "company_insight": None,
    }
    rag_pipeline = DummyRagPipeline()
    service = CorrectionService(repository, DummyGenerator(), rag_pipeline)

    await service._run_rag("c-1")

    assert repository.rows["c-1"]["company_insight"] == "기업 인사이트"
    assert repository.saved_rag_data[0]["search_query"] == "회사 백엔드"
    assert repository.saved_rag_data[0]["search_results"]["keywords"] == ["회사 백엔드"]
    assert repository.saved_rag_data[0]["search_results"]["results"][0]["title"] == "검색 결과"
    assert len(rag_pipeline.run_calls) == 1
    assert repository.rows["c-1"]["status"] == CorrectionStatus.COMPANY_INSIGHT.value


@pytest.mark.asyncio
async def test_run_rag_stores_joined_search_query_for_multiple_keywords():
    """_run_rag는 다중 키워드일 때 search_query를 콤마로 join하여 저장한다."""

    class MultiKeywordRagPipeline(DummyRagPipeline):
        async def run(
            self,
            company_name: str,
            job_title: str,
            job_description: str,
        ) -> RAGRunResult:
            return RAGRunResult(
                keywords=["키워드A", "키워드B"],
                search_results=[{"title": "검색 결과"}],
                insight="기업 인사이트",
            )

    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.DOING_RAG.value,
        "company_insight": None,
    }
    service = CorrectionService(repository, DummyGenerator(), MultiKeywordRagPipeline())

    await service._run_rag("c-1")

    assert repository.saved_rag_data[0]["search_query"] == "키워드A, 키워드B"
    assert repository.saved_rag_data[0]["search_results"]["keywords"] == ["키워드A", "키워드B"]


@pytest.mark.asyncio
async def test_run_rag_does_not_block_event_loop():
    """_run_rag의 LLM 호출은 이벤트 루프를 블로킹하지 않는다."""

    class SlowRagPipeline(DummyRagPipeline):
        async def run(self, company_name: str, job_title: str, job_description: str) -> str:
            await asyncio.sleep(0.4)
            return await super().run(company_name, job_title, job_description)

    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.DOING_RAG.value,
    }
    service = CorrectionService(repository, DummyGenerator(), SlowRagPipeline())

    rag_task = asyncio.create_task(service._run_rag("c-1"))
    await asyncio.sleep(0.01)

    start = time.perf_counter()
    status = await service.get_status("c-1")
    elapsed = time.perf_counter() - start

    assert status == CorrectionStatus.DOING_RAG
    assert elapsed < 0.3

    await rag_task
    assert repository.rows["c-1"]["status"] == CorrectionStatus.COMPANY_INSIGHT.value


@pytest.mark.asyncio
async def test_retry_reuses_saved_rag_data_for_partial_rerun():
    """retry는 저장된 rag_data가 있으면 검색 없이 인사이트만 재생성한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.FAILED.value,
        "company_insight": None,
    }
    repository.rag_data_rows["c-1"] = [
        {
            "correction_id": "c-1",
            "search_query": "회사 백엔드",
            "search_results": {"results": [{"title": "기존 검색 결과", "content": "본문"}]},
        }
    ]
    rag_pipeline = DummyRagPipeline()
    service = CorrectionService(repository, DummyGenerator(), rag_pipeline)
    background_tasks = DummyBackgroundTasks()

    await service.retry("c-1", background_tasks)  # type: ignore[arg-type]

    assert repository.rows["c-1"]["status"] == CorrectionStatus.DOING_RAG.value
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_rag_from_search_results

    await task_func(*task_args)

    assert repository.rows["c-1"]["company_insight"] == "재생성 기업 인사이트"
    assert repository.rows["c-1"]["status"] == CorrectionStatus.COMPANY_INSIGHT.value
    assert len(rag_pipeline.run_calls) == 0
    assert len(rag_pipeline.run_from_search_results_calls) == 1


@pytest.mark.asyncio
async def test_retry_runs_full_rag_when_saved_rag_data_missing():
    """retry는 rag_data가 없으면 전체 RAG를 다시 실행한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.FAILED.value,
        "company_insight": None,
    }
    rag_pipeline = DummyRagPipeline()
    service = CorrectionService(repository, DummyGenerator(), rag_pipeline)
    background_tasks = DummyBackgroundTasks()

    await service.retry("c-1", background_tasks)  # type: ignore[arg-type]

    assert repository.rows["c-1"]["status"] == CorrectionStatus.DOING_RAG.value
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_rag

    await task_func(*task_args)

    assert len(rag_pipeline.run_calls) == 1
    assert len(rag_pipeline.run_from_search_results_calls) == 0
    assert repository.rows["c-1"]["status"] == CorrectionStatus.COMPANY_INSIGHT.value


@pytest.mark.asyncio
async def test_retry_restarts_generation_when_company_insight_exists():
    """retry는 company_insight가 있으면 생성 단계부터 재시작한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.FAILED.value,
        "company_insight": "이미 생성된 인사이트",
    }
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())
    background_tasks = DummyBackgroundTasks()

    await service.retry("c-1", background_tasks)  # type: ignore[arg-type]

    assert repository.rows["c-1"]["status"] == CorrectionStatus.GENERATING.value
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_generation
    assert task_args == ("c-1",)


@pytest.mark.asyncio
async def test_run_rag_failure_updates_failed_status():
    """_run_rag 실패 시 status를 failed로 변경한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.DOING_RAG.value,
    }
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline(raise_error=True))

    await service._run_rag("c-1")

    assert repository.rows["c-1"]["status"] == CorrectionStatus.FAILED.value


@pytest.mark.asyncio
async def test_run_rag_missing_correction_does_not_raise():
    """_run_rag는 없는 correction_id여도 예외를 전파하지 않는다."""
    repository = DummyRepository()
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())

    await service._run_rag("missing-id")

    assert repository.updated_statuses == []


@pytest.mark.asyncio
async def test_start_generation_updates_status_and_registers_task():
    """start_generation은 generating으로 상태 변경 후 백그라운드 작업을 등록한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "company_insight": "인사이트",
        "status": CorrectionStatus.COMPANY_INSIGHT.value,
    }
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())
    background_tasks = DummyBackgroundTasks()

    await service.start_generation("c-1", background_tasks)  # type: ignore[arg-type]

    assert repository.rows["c-1"]["status"] == CorrectionStatus.GENERATING.value
    assert len(background_tasks.tasks) == 1
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_generation
    assert task_args == ("c-1",)


@pytest.mark.asyncio
async def test_run_generation_success_calls_generator_and_saves_result(
    monkeypatch: pytest.MonkeyPatch,
):
    """_run_generation 성공 시 생성기 호출/결과 저장 후 done 상태가 된다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "company_insight": "인사이트",
        "emphasis_points": "강조",
        "status": CorrectionStatus.GENERATING.value,
    }
    generator = DummyGenerator()
    service = CorrectionService(repository, generator, DummyRagPipeline())

    dummy_portfolio_module = types.ModuleType("features.portfolio")
    dummy_portfolio_module.get_portfolio_service = lambda: DummyPortfolioService(
        DummyPortfolioResult(DummyPortfolioOutput())
    )
    monkeypatch.setitem(sys.modules, "features.portfolio", dummy_portfolio_module)

    await service._run_generation("c-1")

    assert len(generator.calls) == 1
    assert generator.calls[0]["portfolio_output"]["description"] == "설명"
    assert repository.updated_result == {"fields": [], "overall_summary": "완료"}
    assert repository.rows["c-1"]["status"] == CorrectionStatus.DONE.value
    assert repository.updated_statuses[-1] == {
        "correction_id": "c-1",
        "status": CorrectionStatus.DONE.value,
    }


@pytest.mark.asyncio
async def test_run_generation_does_not_block_event_loop(monkeypatch: pytest.MonkeyPatch):
    """_run_generation의 LLM 호출은 이벤트 루프를 블로킹하지 않는다."""

    class SlowGenerator(DummyGenerator):
        def generate(
            self,
            company_name: str,
            job_title: str,
            job_description: str,
            company_insight: str,
            portfolio_output: dict,
            emphasis_points: str,
        ) -> dict:
            time.sleep(0.4)
            return super().generate(
                company_name,
                job_title,
                job_description,
                company_insight,
                portfolio_output,
                emphasis_points,
            )

    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "company_insight": "인사이트",
        "emphasis_points": "강조",
        "status": CorrectionStatus.GENERATING.value,
    }
    service = CorrectionService(repository, SlowGenerator(), DummyRagPipeline())

    dummy_portfolio_module = types.ModuleType("features.portfolio")
    dummy_portfolio_module.get_portfolio_service = lambda: DummyPortfolioService(
        DummyPortfolioResult(DummyPortfolioOutput())
    )
    monkeypatch.setitem(sys.modules, "features.portfolio", dummy_portfolio_module)

    generation_task = asyncio.create_task(service._run_generation("c-1"))
    await asyncio.sleep(0.01)

    start = time.perf_counter()
    status = await service.get_status("c-1")
    elapsed = time.perf_counter() - start

    assert status == CorrectionStatus.GENERATING
    assert elapsed < 0.3

    await generation_task
    assert repository.rows["c-1"]["status"] == CorrectionStatus.DONE.value


@pytest.mark.asyncio
async def test_run_generation_failure_updates_failed_status(monkeypatch: pytest.MonkeyPatch):
    """_run_generation 실패 시 status를 failed로 변경한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "status": CorrectionStatus.GENERATING.value,
    }
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())

    dummy_portfolio_module = types.ModuleType("features.portfolio")
    dummy_portfolio_module.get_portfolio_service = lambda: DummyPortfolioService(None)
    monkeypatch.setitem(sys.modules, "features.portfolio", dummy_portfolio_module)

    await service._run_generation("c-1")

    assert repository.rows["c-1"]["status"] == CorrectionStatus.FAILED.value


@pytest.mark.asyncio
async def test_run_generation_missing_correction_does_not_raise():
    """_run_generation은 없는 correction_id여도 예외를 전파하지 않는다."""
    repository = DummyRepository()
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())

    await service._run_generation("missing-id")

    assert repository.updated_statuses == []


@pytest.mark.asyncio
async def test_get_update_delete_methods_work():
    """조회/수정/삭제 메서드가 repository 동작을 위임한다."""
    repository = DummyRepository()
    repository.rows["c-1"] = {
        "id": "c-1",
        "portfolio_id": "p-1",
        "company_name": "회사",
        "job_title": "백엔드",
        "job_description": "JD",
        "company_insight": "기존 인사이트",
        "emphasis_points": "기존 포인트",
        "status": CorrectionStatus.COMPANY_INSIGHT.value,
    }
    service = CorrectionService(repository, DummyGenerator(), DummyRagPipeline())

    assert (await service.get_status("c-1")) == CorrectionStatus.COMPANY_INSIGHT
    assert (await service.get_company_insight("c-1")) == "기존 인사이트"

    await service.update_company_insight("c-1", "새 인사이트")
    await service.update_emphasis_points("c-1", "새 포인트")
    correction = await service.get_correction("c-1")
    assert correction is not None
    assert correction["company_insight"] == "새 인사이트"
    assert correction["emphasis_points"] == "새 포인트"

    await service.delete_correction("c-1")
    assert await service.get_correction("c-1") is None


def test_correction_service_singleton_get_init_reset(monkeypatch: pytest.MonkeyPatch):
    """get/init/reset 싱글톤 동작을 확인한다."""

    class DummyRagPipelineForSingleton:
        pass

    repository_a = DummyRepository()
    generator_a = DummyGenerator()

    monkeypatch.setattr(
        correction_service_module, "get_correction_repository", lambda: repository_a
    )
    monkeypatch.setattr(correction_service_module, "get_correction_generator", lambda: generator_a)
    monkeypatch.setattr(correction_service_module, "RAGPipeline", DummyRagPipelineForSingleton)

    first = get_correction_service()
    second = get_correction_service()
    assert first is second

    repository_b = DummyRepository()
    generator_b = DummyGenerator()
    rag_b = DummyRagPipeline()
    initialized = init_correction_service(repository_b, generator_b, rag_b)

    assert initialized is get_correction_service()
    assert initialized is not first

    reset_correction_service()
    third = get_correction_service()
    assert third is not initialized
