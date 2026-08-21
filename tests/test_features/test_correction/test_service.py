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


def _install_dummy_tavily():
    """테스트용 tavily 더미 모듈 설치"""
    dummy_module = types.ModuleType("tavily")

    class DummyAsyncTavilyClient:  # pragma: no cover - 간단 더미
        def __init__(self, *args, **kwargs):
            pass

    dummy_module.AsyncTavilyClient = DummyAsyncTavilyClient
    sys.modules.setdefault("tavily", dummy_module)


_install_dummy_langchain_openai()
_install_dummy_tavily()

correction_service_module = importlib.import_module("features.correction.service")
RAGRunResult = importlib.import_module("features.correction.rag.pipeline").RAGRunResult
CorrectionStatus = importlib.import_module("features.correction.schemas").CorrectionStatus
CorrectionOutput = importlib.import_module("features.correction.schemas").CorrectionOutput
PortfolioCorrectionResult = importlib.import_module(
    "features.correction.schemas"
).PortfolioCorrectionResult
SingleCorrectionOutput = importlib.import_module(
    "features.correction.schemas"
).SingleCorrectionOutput
MainServerError = importlib.import_module("common.clients.base_client").MainServerError
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
            raise MainServerError(
                status_code=404, detail=f"첨삭을 찾을 수 없습니다: {correction_id}"
            )
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

    async def update_result(
        self,
        correction_id: int,
        result: list[dict],
        overall_review: str,
    ) -> dict:
        self.updated_results.append(
            {
                "correction_id": correction_id,
                "result": result,
                "overall_review": overall_review,
            }
        )
        if correction_id in self.corrections:
            self.corrections[correction_id]["status"] = "DONE"
        return {"id": correction_id}


class DummyPortfolioClient:
    """PortfolioClient 대체 더미"""

    def __init__(self, portfolios: dict[int, dict] | None = None) -> None:
        self._portfolios = portfolios or {
            1: {
                "id": 1,
                "description": "설명",
                "responsibilities": "기여",
                "problemSolving": "성과",
                "learnings": "인사이트",
            },
            2: {
                "id": 2,
                "description": "설명2",
                "responsibilities": "기여2",
                "problemSolving": "성과2",
                "learnings": "인사이트2",
            },
        }

    async def get_portfolio(self, portfolio_id: int) -> dict:
        return self._portfolios[portfolio_id]


class DummyGenerator:
    """CorrectionGenerator 대체 더미"""

    def __init__(
        self,
        raise_error: bool = False,
        raise_overall_summary_error: bool = False,
    ) -> None:
        self.raise_error = raise_error
        self.raise_overall_summary_error = raise_overall_summary_error
        self.calls: list[dict] = []
        self.overall_summary_calls: list[dict] = []

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
        return _make_single_output(portfolio_output)

    def generate_overall_summary(
        self,
        company_name: str,
        job_title: str,
        job_description: str,
        company_insight: str,
        portfolio_corrections: list[PortfolioCorrectionResult],
        emphasis_points: str,
    ) -> str:
        self.overall_summary_calls.append(
            {
                "company_name": company_name,
                "job_title": job_title,
                "job_description": job_description,
                "company_insight": company_insight,
                "portfolio_corrections": portfolio_corrections,
                "emphasis_points": emphasis_points,
            }
        )
        if self.raise_overall_summary_error:
            raise RuntimeError("총평 생성 실패")
        return "통합 총평"


class DummyRagPipeline:
    """RAGPipeline 대체 더미"""

    def __init__(self, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.run_calls: list[dict] = []
        self.run_from_search_results_calls: list[dict] = []

    def _extract_keywords(
        self, company_name: str, job_title: str, job_description: str
    ) -> list[str]:
        return [f"{company_name} {job_title}"]

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
        keywords = await asyncio.to_thread(
            self._extract_keywords, company_name, job_title, job_description
        )
        search_query = ", ".join(keywords) if keywords else f"{company_name} {job_title}"
        return RAGRunResult(
            keywords=keywords,
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
        keywords: list[str] | None = None,
    ) -> str:
        self.run_from_search_results_calls.append(
            {
                "search_results": search_results,
                "company_name": company_name,
                "job_title": job_title,
                "keywords": keywords,
            }
        )
        if self.raise_error:
            raise RuntimeError("RAG 실패")
        return "재생성 기업 인사이트"


class ErrorRagPipeline(DummyRagPipeline):
    """run 호출 시 지정된 예외를 발생시키는 RAG 파이프라인 더미"""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def run(self, company_name: str, job_title: str, job_description: str) -> str:
        raise self._error


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


def _make_single_output(portfolio_output: dict | None = None) -> SingleCorrectionOutput:
    portfolio_output = portfolio_output or {
        "description": "설명",
        "contributions": "기여",
        "achievements": "성과",
        "insights": "인사이트",
    }
    return SingleCorrectionOutput.model_validate(
        {
            "fields": [
                {
                    "field_name": "description",
                    "lines": [
                        {
                            "line_number": 1,
                            "original_text": portfolio_output["description"],
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
                            "original_text": portfolio_output["contributions"],
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
                            "original_text": portfolio_output["achievements"],
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
                            "original_text": portfolio_output["insights"],
                            "type": "keep",
                            "comment": None,
                        }
                    ],
                },
            ]
        }
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_correction_service()
    yield
    reset_correction_service()


@pytest.fixture
def run_rag_failure_setup():
    def _build(error: Exception) -> tuple[DummyCorrectionClient, CorrectionService]:
        client = DummyCorrectionClient()
        client.corrections[1] = _make_correction(status="DOING_RAG")
        service = CorrectionService(
            client, DummyPortfolioClient(), DummyGenerator(), ErrorRagPipeline(error)
        )
        return client, service

    return _build


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
    """_run_rag 성공 시 RAG 데이터와 기업 인사이트를 저장한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DOING_RAG")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._run_rag(1)

    assert client.saved_rag_data[0]["search_query"] == "회사 백엔드"
    assert client.saved_rag_data[0]["search_results"]["results"][0]["title"] == "검색 결과"
    assert client.updated_company_insights[0]["company_insight"] == "기업 인사이트"


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

    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="DOING_RAG")
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), MultiKeywordRagPipeline()
    )

    await service._run_rag(1)

    assert client.saved_rag_data[0]["search_query"] == "키워드A, 키워드B"
    assert client.saved_rag_data[0]["search_results"]["keywords"] == ["키워드A", "키워드B"]


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
async def test_retry_reuses_saved_rag_data_for_partial_rerun():
    """retry는 저장된 rag_data가 있으면 검색 없이 인사이트만 재생성한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="FAILED")
    client.rag_data[1] = {
        "searchQuery": "회사 백엔드",
        "searchResults": {
            "keywords": ["저장 키워드1", "저장 키워드2"],
            "results": [{"title": "기존 검색 결과", "content": "본문"}],
        },
    }
    rag_pipeline = DummyRagPipeline()
    service = CorrectionService(client, DummyPortfolioClient(), DummyGenerator(), rag_pipeline)
    bg = DummyBackgroundTasks()

    await service.retry(1, bg)  # type: ignore[arg-type]

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "DOING_RAG"}
    assert len(bg.tasks) == 1
    assert bg.tasks[0][0] == service._run_rag_from_search_results


@pytest.mark.asyncio
async def test_retry_extracts_keywords_from_search_query_when_keywords_missing():
    """retry는 저장 키워드가 없으면 search_query를 분해해 키워드로 사용한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(status="FAILED")
    client.rag_data[1] = {
        "searchQuery": "키워드A, 키워드B",
        "searchResults": {"results": [{"title": "기존 검색 결과", "content": "본문"}]},
    }
    rag_pipeline = DummyRagPipeline()
    service = CorrectionService(client, DummyPortfolioClient(), DummyGenerator(), rag_pipeline)
    bg = DummyBackgroundTasks()

    await service.retry(1, bg)  # type: ignore[arg-type]

    assert len(bg.tasks) == 1
    assert bg.tasks[0][0] == service._run_rag_from_search_results


@pytest.mark.asyncio
async def test_retry_runs_full_rag_when_saved_rag_data_missing():
    """retry는 rag_data가 없으면 전체 RAG를 다시 실행한다."""
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
async def test_retry_restarts_generation_when_company_insight_and_rag_data_exist():
    """retry는 company_insight와 rag_data가 모두 있으면 생성부터 재시작한다."""
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
async def test_run_rag_failure_updates_failed_status(run_rag_failure_setup):
    """_run_rag 실패 시 FAILED 상태로 변경한다."""
    client, service = run_rag_failure_setup(RuntimeError("RAG 실패"))

    await service._run_rag(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raised_error", "expected_log_message"),
    [
        (
            correction_service_module.RAGKeywordExtractionError("키워드 추출 실패"),
            "RAG 키워드 추출 실패",
        ),
        (
            correction_service_module.RAGSearchError("검색 실패"),
            "RAG 검색 실패",
        ),
        (
            correction_service_module.RAGInsightGenerationError("인사이트 실패"),
            "RAG 인사이트 생성 실패",
        ),
        (
            RuntimeError("알 수 없는 실패"),
            "RAG 처리 실패",
        ),
    ],
)
async def test_run_rag_failure_logs_by_exception_type(
    raised_error: Exception,
    expected_log_message: str,
    caplog: pytest.LogCaptureFixture,
    run_rag_failure_setup,
):
    """_run_rag 실패 로그는 예외 타입별로 분기된다."""
    _, service = run_rag_failure_setup(raised_error)

    caplog.set_level("ERROR", logger="features.correction.service")

    await service._run_rag(1)

    assert expected_log_message in caplog.text


@pytest.mark.asyncio
async def test_run_rag_missing_correction_does_not_raise():
    """_run_rag는 없는 correction_id여도 예외를 전파하지 않는다."""
    client = DummyCorrectionClient()
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._run_rag(999)

    assert any(s["status"] == "FAILED" for s in client.updated_statuses)


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
    """_run_generation 성공 시 다중 포트폴리오 결과와 총평을 저장한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        highlight_point="강조",
        portfolio_ids=[1, 2],
    )
    generator = DummyGenerator()
    service = CorrectionService(client, DummyPortfolioClient(), generator, DummyRagPipeline())

    await service._run_generation(1)

    assert len(generator.calls) == 2
    actual_portfolio_outputs = {
        call["portfolio_output"]["description"]: call["portfolio_output"]
        for call in generator.calls
    }
    assert actual_portfolio_outputs == {
        "설명": {
            "description": "설명",
            "contributions": "기여",
            "achievements": "성과",
            "insights": "인사이트",
        },
        "설명2": {
            "description": "설명2",
            "contributions": "기여2",
            "achievements": "성과2",
            "insights": "인사이트2",
        },
    }
    assert len(generator.overall_summary_calls) == 1
    assert len(generator.overall_summary_calls[0]["portfolio_corrections"]) == 2
    assert len(client.updated_results) == 1
    assert client.updated_results[0]["overall_review"] == "통합 총평"
    assert client.updated_results[0]["result"] == [
        {
            "portfolioId": 1,
            "description": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "설명",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
            "responsibilities": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "기여",
                        "type": "emphasize",
                        "comment": "강조하세요.",
                    }
                ]
            },
            "problemSolving": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "성과",
                        "type": "reduce",
                        "comment": "줄이세요.",
                    }
                ]
            },
            "learnings": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "인사이트",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
        },
        {
            "portfolioId": 2,
            "description": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "설명2",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
            "responsibilities": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "기여2",
                        "type": "emphasize",
                        "comment": "강조하세요.",
                    }
                ]
            },
            "problemSolving": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "성과2",
                        "type": "reduce",
                        "comment": "줄이세요.",
                    }
                ]
            },
            "learnings": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "인사이트2",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
        },
    ]


@pytest.mark.asyncio
async def test_run_generation_supports_external_snake_case_portfolio_fields():
    """외부 PDF 포트폴리오의 snake_case 필드명도 첨삭 입력으로 사용한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        highlight_point="강조",
        portfolio_ids=[77],
    )
    generator = DummyGenerator()
    portfolio_client = DummyPortfolioClient(
        portfolios={
            77: {
                "id": 77,
                "description": "상세",
                "responsibility": "담당 업무",
                "problem_solving": "문제 해결",
                "learning": "배운 점",
            }
        }
    )
    service = CorrectionService(client, portfolio_client, generator, DummyRagPipeline())

    await service._run_generation(1)

    assert generator.calls[0]["portfolio_output"] == {
        "description": "상세",
        "contributions": "담당 업무",
        "achievements": "문제 해결",
        "insights": "배운 점",
    }
    assert client.updated_results


@pytest.mark.asyncio
async def test_run_generation_skips_empty_placeholders_and_stringifies_fields():
    """빈 값과 '0' placeholder는 건너뛰고 선택한 필드는 문자열로 정규화한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        highlight_point="강조",
        portfolio_ids=[77],
    )
    generator = DummyGenerator()
    portfolio_client = DummyPortfolioClient(
        portfolios={
            77: {
                "id": 77,
                "description": 123,
                "responsibilities": "0",
                "responsibility": " 담당 업무 ",
                "problemSolving": "",
                "problem_solving": "문제 해결",
                "learnings": None,
                "learning": "배운 점",
            }
        }
    )
    service = CorrectionService(client, portfolio_client, generator, DummyRagPipeline())

    await service._run_generation(1)

    assert generator.calls[0]["portfolio_output"] == {
        "description": "123",
        "contributions": "담당 업무",
        "achievements": "문제 해결",
        "insights": "배운 점",
    }
    assert client.updated_results


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
    """포트폴리오별 생성 실패 시 FAILED 상태로 변경한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        portfolio_ids=[1, 2],
    )
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(raise_error=True), DummyRagPipeline()
    )

    await service._run_generation(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}
    assert client.updated_results == []


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


@pytest.mark.asyncio
async def test_run_generation_rejects_more_than_four_portfolios():
    """_run_generation은 포트폴리오가 4개를 초과하면 FAILED 상태가 된다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        portfolio_ids=[1, 2, 3, 4, 5],
    )
    service = CorrectionService(
        client, DummyPortfolioClient(), DummyGenerator(), DummyRagPipeline()
    )

    await service._run_generation(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}
    assert client.updated_results == []


@pytest.mark.asyncio
async def test_run_generation_failure_when_overall_summary_fails():
    """총평 생성 실패 시 전체 첨삭을 FAILED 처리한다."""
    client = DummyCorrectionClient()
    client.corrections[1] = _make_correction(
        status="GENERATING",
        company_insight="인사이트",
        portfolio_ids=[1, 2],
    )
    service = CorrectionService(
        client,
        DummyPortfolioClient(),
        DummyGenerator(raise_overall_summary_error=True),
        DummyRagPipeline(),
    )

    await service._run_generation(1)

    assert client.updated_statuses[-1] == {"correction_id": 1, "status": "FAILED"}


def test_convert_result_for_server_converts_multi_portfolio_format():
    """_convert_result_for_server는 다중 포트폴리오 결과를 메인 서버 포맷으로 변환한다."""
    result = CorrectionOutput(
        portfolio_corrections=[
            PortfolioCorrectionResult(portfolio_id=11, fields=_make_single_output().fields),
            PortfolioCorrectionResult(
                portfolio_id=22,
                fields=_make_single_output(
                    {
                        "description": "설명B",
                        "contributions": "기여B",
                        "achievements": "성과B",
                        "insights": "인사이트B",
                    }
                ).fields,
            ),
        ],
        overall_summary="총평",
    )

    converted = CorrectionService._convert_result_for_server(result)

    assert converted == [
        {
            "portfolioId": 11,
            "description": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "설명",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
            "responsibilities": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "기여",
                        "type": "emphasize",
                        "comment": "강조하세요.",
                    }
                ]
            },
            "problemSolving": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "성과",
                        "type": "reduce",
                        "comment": "줄이세요.",
                    }
                ]
            },
            "learnings": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "인사이트",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
        },
        {
            "portfolioId": 22,
            "description": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "설명B",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
            "responsibilities": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "기여B",
                        "type": "emphasize",
                        "comment": "강조하세요.",
                    }
                ]
            },
            "problemSolving": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "성과B",
                        "type": "reduce",
                        "comment": "줄이세요.",
                    }
                ]
            },
            "learnings": {
                "lines": [
                    {
                        "lineNumber": 1,
                        "originalText": "인사이트B",
                        "type": "keep",
                        "comment": None,
                    }
                ]
            },
        },
    ]


def _make_conversion_result_with_comments(
    *,
    emphasize_comment: str,
    reduce_comment: str = "줄이세요.",
    keep_comment: str | None = None,
) -> CorrectionOutput:
    """서버 변환 테스트용 댓글 값을 가진 CorrectionOutput 생성."""
    single_output = _make_single_output().model_dump()
    single_output["fields"][0]["lines"][0]["comment"] = keep_comment
    single_output["fields"][1]["lines"][0]["comment"] = emphasize_comment
    single_output["fields"][2]["lines"][0]["comment"] = reduce_comment

    return CorrectionOutput(
        portfolio_corrections=[
            PortfolioCorrectionResult(
                portfolio_id=11,
                fields=SingleCorrectionOutput.model_validate(single_output).fields,
            )
        ],
        overall_summary="총평",
    )


def test_convert_result_for_server_breaks_emphasize_comment_before_example():
    """emphasize 코멘트의 inline 수정 예시는 서버 변환 시 줄바꿈으로 정규화한다."""
    result = _make_conversion_result_with_comments(
        emphasize_comment="강조하세요. 수정 예시: 이렇게 작성하세요.",
    )

    converted = CorrectionService._convert_result_for_server(result)

    assert converted[0]["responsibilities"]["lines"][0]["comment"] == (
        "강조하세요. \n수정 예시: 이렇게 작성하세요."
    )


def test_convert_result_for_server_leaves_reduce_and_keep_comments_unchanged():
    """reduce/keep 코멘트는 수정 예시 문구가 있어도 그대로 유지한다."""
    result = _make_conversion_result_with_comments(
        emphasize_comment="강조하세요.",
        reduce_comment="줄이세요. 수정 예시: 더 짧게 작성하세요.",
        keep_comment="유지하세요. 수정 예시: 현재 표현을 유지하세요.",
    )

    converted = CorrectionService._convert_result_for_server(result)

    assert converted[0]["problemSolving"]["lines"][0]["comment"] == (
        "줄이세요. 수정 예시: 더 짧게 작성하세요."
    )
    assert converted[0]["description"]["lines"][0]["comment"] == (
        "유지하세요. 수정 예시: 현재 표현을 유지하세요."
    )


def test_convert_result_for_server_keeps_already_formatted_emphasize_comment():
    """이미 줄바꿈된 emphasize 수정 예시는 그대로 유지한다."""
    result = _make_conversion_result_with_comments(
        emphasize_comment="강조하세요.\n수정 예시: 이렇게 작성하세요.",
    )

    converted = CorrectionService._convert_result_for_server(result)

    assert converted[0]["responsibilities"]["lines"][0]["comment"] == (
        "강조하세요.\n수정 예시: 이렇게 작성하세요."
    )


def test_convert_result_for_server_preserves_spacing_before_inserted_newline():
    """inline 수정 예시 앞 공백은 유지하고 줄바꿈만 추가한다."""
    result = _make_conversion_result_with_comments(
        emphasize_comment="강조하세요.  수정 예시: 이렇게 작성하세요.",
    )

    converted = CorrectionService._convert_result_for_server(result)

    assert converted[0]["responsibilities"]["lines"][0]["comment"] == (
        "강조하세요.  \n수정 예시: 이렇게 작성하세요."
    )


def test_convert_result_for_server_keeps_indented_already_formatted_example():
    """개행 뒤 공백이 있는 수정 예시는 이미 포맷된 것으로 유지한다."""
    result = _make_conversion_result_with_comments(
        emphasize_comment="강조하세요.\n 수정 예시: 이렇게 작성하세요.",
    )

    converted = CorrectionService._convert_result_for_server(result)

    assert converted[0]["responsibilities"]["lines"][0]["comment"] == (
        "강조하세요.\n 수정 예시: 이렇게 작성하세요."
    )


# ------------------------------------------------------------------
# retry
# ------------------------------------------------------------------


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
