"""PDF 추출 서비스 테스트"""

import pytest

from features.portfolio.pdf_extraction import service as pdf_extraction_service_module
from features.portfolio.pdf_extraction.schemas import (
    PdfActivity,
    PdfExtractionResult,
    PdfProblemSolvingItem,
)
from features.portfolio.pdf_extraction.service import (
    PdfExtractionService,
    get_pdf_extraction_service,
    init_pdf_extraction_service,
    reset_pdf_extraction_service,
)


class DummyBackgroundTasks:
    """BackgroundTasks 대체 테스트 더블"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def add_task(self, fn, *args) -> None:
        self.calls.append({"fn": fn, "args": args})


class DummyCorrectionClient:
    """PDF 추출 콜백 테스트용 클라이언트"""

    def __init__(self) -> None:
        self.completed_calls: list[tuple[int, list[dict], str]] = []
        self.failed_calls: list[tuple[int, str]] = []

    async def complete_pdf_extraction(
        self,
        correction_id: int,
        activities: list[dict],
        source_type: str,
    ) -> dict:
        self.completed_calls.append((correction_id, activities, source_type))
        return {"id": correction_id}

    async def fail_pdf_extraction(self, correction_id: int, error_message: str) -> dict:
        self.failed_calls.append((correction_id, error_message))
        return {"id": correction_id}


class DummyGenerator:
    """PDF 추출 generator 테스트 더블"""

    def __init__(
        self,
        result: PdfExtractionResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.result = result or PdfExtractionResult(
            activities=[
                PdfActivity(
                    activity_name="프로젝트 A",
                    detail="상세 설명",
                    responsibility="담당 업무",
                    problem_solving=[
                        PdfProblemSolvingItem(
                            no=5,
                            situation="상황",
                            strategy="전략",
                            reason="이유",
                        )
                    ],
                    learning="배운 점",
                )
            ]
        )
        self.exc = exc

    def extract(self, _file_bytes: bytes, _filename: str) -> PdfExtractionResult:
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_start_extraction_registers_background_task():
    """유효한 PDF 요청이면 background task를 등록한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    tasks = DummyBackgroundTasks()

    await service.start_extraction(
        correction_id=123,
        file_bytes=b"%PDF-1.4",
        filename="portfolio.pdf",
        content_type="application/pdf",
        background_tasks=tasks,
    )

    assert len(tasks.calls) == 1
    assert tasks.calls[0]["fn"] == service._extract_background
    assert tasks.calls[0]["args"] == (123, b"%PDF-1.4", "portfolio.pdf")


@pytest.mark.asyncio
async def test_background_extraction_success_calls_complete_callback(monkeypatch):
    """추출 성공 시 complete 콜백을 호출한다."""
    client = DummyCorrectionClient()
    generator = DummyGenerator()
    service = PdfExtractionService(correction_client=client, generator=generator)
    to_thread_calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_to_thread(fn, *args):
        to_thread_calls.append((fn, args))
        return fn(*args)

    monkeypatch.setattr(pdf_extraction_service_module.asyncio, "to_thread", fake_to_thread)

    await service._extract_background(123, b"%PDF-1.4", "portfolio.pdf")

    assert to_thread_calls == [(generator.extract, (b"%PDF-1.4", "portfolio.pdf"))]
    assert client.completed_calls == [
        (
            123,
            [
                {
                    "activity_name": "프로젝트 A",
                    "detail": "상세 설명",
                    "responsibility": "담당 업무",
                    "problem_solving": [
                        {
                            "no": 1,
                            "situation": "상황",
                            "strategy": "전략",
                            "reason": "이유",
                        }
                    ],
                    "learning": "배운 점",
                }
            ],
            "EXTERNAL",
        )
    ]
    assert client.failed_calls == []


@pytest.mark.asyncio
async def test_background_extraction_failure_calls_fail_callback(monkeypatch):
    """추출 실패 시 fail 콜백을 호출한다."""
    client = DummyCorrectionClient()
    generator = DummyGenerator(exc=RuntimeError("PDF 파싱 실패"))
    service = PdfExtractionService(correction_client=client, generator=generator)

    async def fake_to_thread(fn, *args):
        return fn(*args)

    monkeypatch.setattr(pdf_extraction_service_module.asyncio, "to_thread", fake_to_thread)

    await service._extract_background(123, b"%PDF-1.4", "portfolio.pdf")

    assert client.completed_calls == []
    assert client.failed_calls == [(123, "PDF 파싱 실패")]


def test_validate_result_truncates_deduplicates_and_reindexes():
    """검증 로직은 앞 5개만 유지하고 중복 제거 후 순번을 재정렬한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    result = PdfExtractionResult(
        activities=[
            PdfActivity(
                activity_name="Alpha",
                detail="상세 1",
                responsibility="담당 1",
                problem_solving=[
                    PdfProblemSolvingItem(
                        no=9,
                        situation="상황 1",
                        strategy="전략 1",
                        reason="이유 1",
                    ),
                    PdfProblemSolvingItem(
                        no=4,
                        situation="상황 2",
                        strategy="전략 2",
                        reason="이유 2",
                    ),
                ],
                learning="배운 점 1",
            ),
            PdfActivity(
                activity_name=" Alpha ",
                detail="상세 2",
                responsibility="담당 2",
                problem_solving=[],
                learning="배운 점 2",
            ),
            PdfActivity(
                activity_name="Beta",
                detail="상세 3",
                responsibility="담당 3",
                problem_solving=[],
                learning="배운 점 3",
            ),
            PdfActivity(
                activity_name="Gamma",
                detail="상세 4",
                responsibility="담당 4",
                problem_solving=[],
                learning="배운 점 4",
            ),
            PdfActivity(
                activity_name="Delta",
                detail="상세 5",
                responsibility="담당 5",
                problem_solving=[],
                learning="배운 점 5",
            ),
            PdfActivity(
                activity_name="Epsilon",
                detail="상세 6",
                responsibility="담당 6",
                problem_solving=[],
                learning="배운 점 6",
            ),
        ]
    )

    activities = service._validate_result(result)

    assert [activity.activity_name for activity in activities] == [
        "Alpha",
        "Beta",
        "Gamma",
        "Delta",
    ]
    assert [item.no for item in activities[0].problem_solving] == [1, 2]


def test_validate_result_raises_for_empty_activities():
    """활동이 비어 있으면 검증이 실패한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    result = PdfExtractionResult.model_construct(activities=[])

    with pytest.raises(ValueError, match="추출된 활동이 없습니다"):
        service._validate_result(result)


def test_pdf_extraction_service_singleton_get_init_reset(monkeypatch: pytest.MonkeyPatch):
    """get/init/reset 싱글톤 동작을 확인한다."""
    reset_pdf_extraction_service()

    client_a = DummyCorrectionClient()
    generator_a = DummyGenerator()
    monkeypatch.setattr(
        pdf_extraction_service_module,
        "get_correction_client",
        lambda: client_a,
    )
    monkeypatch.setattr(
        pdf_extraction_service_module,
        "_create_default_generator",
        lambda: generator_a,
    )

    first = get_pdf_extraction_service()
    second = get_pdf_extraction_service()
    assert first is second

    client_b = DummyCorrectionClient()
    generator_b = DummyGenerator()
    initialized = init_pdf_extraction_service(client_b, generator_b)

    assert initialized is get_pdf_extraction_service()
    assert initialized is not first

    reset_pdf_extraction_service()
    third = get_pdf_extraction_service()
    assert third is not initialized
