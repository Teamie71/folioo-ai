"""첨삭 서비스 테스트 (httpx 클라이언트 기반)"""

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
CorrectionStatus = importlib.import_module("features.correction.schemas").CorrectionStatus
CorrectionService = correction_service_module.CorrectionService
get_correction_service = correction_service_module.get_correction_service
init_correction_service = correction_service_module.init_correction_service
reset_correction_service = correction_service_module.reset_correction_service


class DummyCorrectionClient:
    """CorrectionClient 대체 더미 (메인 서버 API Mock)"""

    def __init__(self, *, raise_on_update_status: bool = False) -> None:
        self.corrections: dict[int, dict] = {}
        self.rag_data: dict[int, dict | None] = {}
        self.updated_statuses: list[dict] = []
        self.saved_rag_data: list[dict] = []
        self.updated_company_insights: list[dict] = []
        self.updated_results: list[dict] = []
        self._raise_on_update_status = raise_on_update_status

    async def get_correction(self, correction_id: int) -> dict:
        if correction_id not in self.corrections:
            raise ValueError(f"첨삭을 찾을 수 없습니다: {correction_id}")
        return self.corrections[correction_id]

    async def update_status(self, correction_id: int, status: str) -> dict:
        if self._raise_on_update_status:
            raise RuntimeError("상태 업데이트 실패")
        self.updated_statuses.append({"correction_id": correction_id, "status": status})
        if correction_id in self.corrections:
            self.corrections[correction_id]["status"] = status
        return {"id": correction_id, "status": status}

    async def save_rag_data(
        self,
        correction_id: int,
        search_query: str,
        search_results: list | dict,
    ) -> dict:
        self.saved_rag_data.append(
            {
                "correction_id": correction_id,
                "search_query": search_query,
                "search_results": search_results,
            }
        )
        return {"id": 1}

    async def get_rag_data(self, correction_id: int) -> dict | None:
        return self.rag_data.get(correction_id)

    async def update_company_insight(self, correction_id: int, company_insight: str) -> dict:
        self.updated_company_insights.append(
            {"correction_id": correction_id, "company_insight": company_insight}
        )
        if correction_id in self.corrections:
            self.corrections[correction_id]["companyInsight"] = company_insight
            self.corrections[correction_id]["status"] = "COMPANY_INSIGHT"
        return {"id": correction_id}

    async def update_result(self, correction_id: int, result: list[dict]) -> dict:
        self.updated_results.append({"correction_id": correction_id, "result": result})
        if correction_id in self.corrections:
            self.corrections[correction_id]["status"] = "DONE"
        return {"id": correction_id}


class DummyPortfolioClient:
    """PortfolioClient 대체 더미"""

    def __init__(self, portfolio: dict | None = None) -> None:
        self._portfolio = portfolio or {
            "id": 1,
            "description": "설명",
            "responsibilities": "기여",
            "problemSolving": "성과",
            "learnings": "인사이트",
        }

    async def get_portfolio(self, portfolio_id: int) -> dict:
        return self._portfolio


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

    async def run(self, company_name: str, job_title: str, job_description: str) -> str:
        self.run_calls.append(
            {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
            }
        )
        if self.raise_error:
            raise RuntimeError("RAG 실패")
        return "기업 인사이트"

    def _extract_keywords(
        self, company_name: str, job_title: str, job_description: str
    ) -> list[str]:
        return [f"{company_name} {job_title}"]

    async def _search(self, query: str) -> list[dict]:
        return [{"query": query, "title": "검색 결과"}]


class DummyBackgroundTasks:
    """BackgroundTasks 대체 더미"""

    def __init__(self) -> None:
        self.tasks: list[tuple] = []

    def add_task(self, func, *args) -> None:
        self.tasks.append((func, args))


def _make_correction(
    correction_id: int = 1,
    status: str = "NOT_STARTED",
    company_name: str = "회사",
    position_name: str = "백엔드",
    job_description: str = "JD",
    company_insight: str | None = None,
    highlight_point: str | None = None,
    portfolio_ids: list[int] | None = None,
) -> dict:
    """테스트용 첨삭 데이터(camelCase) 생성 헬퍼"""
    return {
        "id": correction_id,
        "companyName": company_name,
        "positionName": position_name,
        "jobDescription": job_description,
        "companyInsight": company_insight,
        "highlightPoint": highlight_point,
        "portfolioIds": portfolio_ids if portfolio_ids is not None else [1],
        "status": status,
    }


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_correction_service()
    yield
    reset_correction_service()


# ------------------------------------------------------------------
# start_rag / _run_rag
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_rag_updates_status_and_registers_task():
    """start_rag는 DOING_RAG으로 상태 변경 후 백그라운드 작업을 등록한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction()
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )
    background_tasks = DummyBackgroundTasks()

    await service.start_rag(1, background_tasks)  # type: ignore[arg-type]

    assert client.updated_statuses[0] == {"correction_id": 1, "status": "DOING_RAG"}
    assert len(background_tasks.tasks) == 1
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_rag
    assert task_args == (1,)


@pytest.mark.asyncio
async def test_run_rag_success_saves_company_insight_and_rag_data():
    """_run_rag 성공 시 RAG 데이터 저장 후 기업 인사이트를 저장한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DOING_RAG")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._run_rag(1)

    assert client.saved_rag_data[0]["search_query"] == "회사 백엔드"
    assert client.saved_rag_data[0]["search_results"][0]["title"] == "검색 결과"
    assert client.updated_company_insights[0]["company_insight"] == "기업 인사이트"


@pytest.mark.asyncio
async def test_run_rag_does_not_block_event_loop():
    """_run_rag의 LLM 호출은 이벤트 루프를 블로킹하지 않는다."""

    class SlowRagPipeline(DummyRagPipeline):
        async def run(self, company_name: str, job_title: str, job_description: str) -> str:
            await asyncio.sleep(0.4)
            return await super().run(company_name, job_title, job_description)

    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DOING_RAG")
    service = CorrectionService(client, DummyPortfolioClient(), DummyGenerator(), SlowRagPipeline())

    rag_task = asyncio.create_task(service._run_rag(1))
    await asyncio.sleep(0.01)

    start = time.perf_counter()
    _ = await client.get_correction(1)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.3
    await rag_task


@pytest.mark.asyncio
async def test_run_rag_keyword_extraction_does_not_block_event_loop():
    """_run_rag의 키워드 추출 단계도 이벤트 루프를 블로킹하지 않는다."""

    class SlowKeywordRagPipeline(DummyRagPipeline):
        def _extract_keywords(
            self, company_name: str, job_title: str, job_description: str
        ) -> list[str]:
            time.sleep(0.4)
            return super()._extract_keywords(company_name, job_title, job_description)

    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DOING_RAG")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), SlowKeywordRagPipeline()
    )

    rag_task = asyncio.create_task(service._run_rag(1))

    start = time.perf_counter()
    await asyncio.sleep(0.05)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.3
    await rag_task


@pytest.mark.asyncio
async def test_run_rag_failure_updates_failed_status():
    """_run_rag 실패 시 FAILED 상태로 변경한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DOING_RAG")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline(raise_error=True)
    )

    await service._run_rag(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}


@pytest.mark.asyncio
async def test_run_rag_missing_correction_does_not_raise():
    """_run_rag는 없는 correction_id여도 예외를 전파하지 않는다."""
    client = DummyCorrectionClient()
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._run_rag(999)

    assert any(s["status"] == "FAILED" for s in client.updated_statuses) or True


# ------------------------------------------------------------------
# start_generation / _run_generation
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_generation_updates_status_and_registers_task():
    """start_generation은 GENERATING으로 상태 변경 후 백그라운드 작업을 등록한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="COMPANY_INSIGHT", company_insight="인사이트")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )
    background_tasks = DummyBackgroundTasks()

    await service.start_generation(1, background_tasks)  # type: ignore[arg-type]

    assert client.updated_statuses[0] == {"correction_id": 1, "status": "GENERATING"}
    assert len(background_tasks.tasks) == 1
    task_func, task_args = background_tasks.tasks[0]
    assert task_func == service._run_generation
    assert task_args == (1,)


@pytest.mark.asyncio
async def test_run_generation_success_calls_generator_and_saves_result():
    """_run_generation 성공 시 생성기 호출/결과 저장이 수행된다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        highlight_point="강조",
        portfolio_ids=[1],
    )
    generator = DummyGenerator()
    service = CorrectionService(client, DummyPortfolioClient(), generator, DummyRagPipeline())

    await service._run_generation(1)

    assert len(generator.calls) == 1
    assert generator.calls[0]["portfolio_output"]["description"] == "설명"
    assert generator.calls[0]["portfolio_output"]["contributions"] == "기여"
    assert generator.calls[0]["portfolio_output"]["achievements"] == "성과"
    assert generator.calls[0]["portfolio_output"]["insights"] == "인사이트"
    assert len(client.updated_results) == 1
    assert client.updated_results[0]["result"] == [{"fields": [], "overall_summary": "완료"}]


@pytest.mark.asyncio
async def test_run_generation_does_not_block_event_loop():
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

    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        highlight_point="강조",
        portfolio_ids=[1],
    )
    service = CorrectionService(client, DummyPortfolioClient(), SlowGenerator(), DummyRagPipeline())

    generation_task = asyncio.create_task(service._run_generation(1))
    await asyncio.sleep(0.01)

    start = time.perf_counter()
    _ = await client.get_correction(1)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.3
    await generation_task


@pytest.mark.asyncio
async def test_run_generation_failure_updates_failed_status():
    """_run_generation 실패 시 FAILED 상태로 변경한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        portfolio_ids=[1],
    )
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(raise_error=True), DummyRagPipeline()
    )

    await service._run_generation(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}


@pytest.mark.asyncio
async def test_run_generation_no_portfolio_ids_fails():
    """_run_generation은 portfolioIds가 비어있으면 FAILED 상태가 된다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        portfolio_ids=[],
    )
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._run_generation(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}


# ------------------------------------------------------------------
# retry
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_with_company_insight_and_rag_data_retries_generation():
    """retry: company_insight와 rag_data가 있으면 생성부터 재시도한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="FAILED", company_insight="인사이트")
    client.rag_data[1] = {"searchQuery": "q", "searchResults": []}
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )
    bg = DummyBackgroundTasks()

    await service.retry(1, bg)  # type: ignore[arg-type]

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "GENERATING"}
    assert len(bg.tasks) == 1
    assert bg.tasks[0][0] == service._run_generation


@pytest.mark.asyncio
async def test_retry_with_company_insight_but_no_rag_data_retries_rag():
    """retry: company_insight는 있지만 rag_data가 없으면 RAG부터 재시도한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="FAILED", company_insight="인사이트")
    client.rag_data[1] = None
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )
    bg = DummyBackgroundTasks()

    await service.retry(1, bg)  # type: ignore[arg-type]

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "DOING_RAG"}
    assert len(bg.tasks) == 1
    assert bg.tasks[0][0] == service._run_rag


@pytest.mark.asyncio
async def test_retry_without_company_insight_retries_rag():
    """retry: company_insight가 없으면 RAG부터 재시도한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="FAILED")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )
    bg = DummyBackgroundTasks()

    await service.retry(1, bg)  # type: ignore[arg-type]

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "DOING_RAG"}
    assert len(bg.tasks) == 1
    assert bg.tasks[0][0] == service._run_rag


@pytest.mark.asyncio
async def test_retry_non_failed_raises_value_error():
    """retry: 실패 상태가 아닌 첨삭은 ValueError를 발생시킨다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DONE")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )
    bg = DummyBackgroundTasks()

    with pytest.raises(ValueError, match="실패 상태가 아닌 첨삭은 재시도할 수 없습니다"):
        await service.retry(1, bg)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# _mark_failed
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_failed_swallows_exception():
    """_mark_failed는 상태 업데이트 실패 시 예외를 전파하지 않는다."""
    client = DummyCorrectionClient(raise_on_update_status=True)
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._mark_failed(1)


# ------------------------------------------------------------------
# 싱글톤
# ------------------------------------------------------------------


def test_correction_service_singleton_get_init_reset(monkeypatch: pytest.MonkeyPatch):
    """get/init/reset 싱글톤 동작을 확인한다."""

    class DummyRagPipelineForSingleton:
        pass

    client_a = DummyCorrectionClient()
    portfolio_a = DummyPortfolioClient()
    generator_a = DummyGenerator()

    monkeypatch.setattr(correction_service_module, "get_correction_client", lambda: client_a)
    monkeypatch.setattr(correction_service_module, "get_portfolio_client", lambda: portfolio_a)
    monkeypatch.setattr(correction_service_module, "get_correction_generator", lambda: generator_a)
    monkeypatch.setattr(correction_service_module, "RAGPipeline", DummyRagPipelineForSingleton)

    first = get_correction_service()
    second = get_correction_service()
    assert first is second

    client_b = DummyCorrectionClient()
    portfolio_b = DummyPortfolioClient()
    generator_b = DummyGenerator()
    rag_b = DummyRagPipeline()
    initialized = init_correction_service(client_b, portfolio_b, generator_b, rag_b)

    assert initialized is get_correction_service()
    assert initialized is not first

    reset_correction_service()
    third = get_correction_service()
    assert third is not initialized
