"""PDF 추출 서비스 테스트"""

import sys
import types

import pytest


def _install_dummy_langchain_openai() -> None:
    """패키지 import 체인 테스트용 langchain_openai 더미 모듈 설치"""
    dummy_module = types.ModuleType("langchain_openai")

    class DummyChatOpenAI:  # pragma: no cover - 간단 더미
        def __init__(self, *args, **kwargs):
            pass

    dummy_module.ChatOpenAI = DummyChatOpenAI
    sys.modules.setdefault("langchain_openai", dummy_module)


_install_dummy_langchain_openai()

from features.portfolio.pdf_extraction import __all__ as pdf_extraction_exports  # noqa: E402
from features.portfolio.pdf_extraction import service as pdf_extraction_service_module  # noqa: E402
from features.portfolio.pdf_extraction.schemas import (  # noqa: E402
    PdfActivity,
    PdfExtractionResult,
    PdfProblemSolvingItem,
)
from features.portfolio.pdf_extraction.service import (  # noqa: E402
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
        self.complete_exception: Exception | None = None

    async def complete_pdf_extraction(
        self,
        correction_id: int,
        activities: list[dict],
        source_type: str,
    ) -> dict:
        if self.complete_exception is not None:
            raise self.complete_exception
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
                    detail=["상세 설명"],
                    responsibility=["담당 업무"],
                    problem_solving=[
                        PdfProblemSolvingItem(
                            no=5,
                            situation="상황",
                            strategy="전략",
                            reason="이유",
                        )
                    ],
                    learning=["배운 점"],
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
                    "detail": ["- 상세 설명"],
                    "responsibility": ["- 담당 업무"],
                    "problem_solving": [
                        {
                            "no": 1,
                            "situation": "상황",
                            "strategy": "전략",
                            "reason": "이유",
                        }
                    ],
                    "learning": ["- 배운 점"],
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


@pytest.mark.asyncio
async def test_background_extraction_complete_callback_failure_does_not_call_fail(monkeypatch):
    """완료 콜백 실패는 fail 콜백으로 전환하지 않는다."""
    client = DummyCorrectionClient()
    client.complete_exception = RuntimeError("callback timeout")
    generator = DummyGenerator()
    service = PdfExtractionService(correction_client=client, generator=generator)

    async def fake_to_thread(fn, *args):
        return fn(*args)

    monkeypatch.setattr(pdf_extraction_service_module.asyncio, "to_thread", fake_to_thread)

    await service._extract_background(123, b"%PDF-1.4", "portfolio.pdf")

    assert client.failed_calls == []


def test_validate_result_truncates_deduplicates_and_reindexes():
    """검증 로직은 앞 5개만 유지하고 중복 제거 후 순번을 재정렬한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    result = PdfExtractionResult.model_construct(
        activities=[
            PdfActivity(
                activity_name="Alpha",
                detail=["상세 1"],
                responsibility=["담당 1"],
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
                learning=["배운 점 1"],
            ),
            PdfActivity(
                activity_name=" Alpha ",
                detail=["상세 2"],
                responsibility=["담당 2"],
                problem_solving=[],
                learning=["배운 점 2"],
            ),
            PdfActivity(
                activity_name="Beta",
                detail=["상세 3"],
                responsibility=["담당 3"],
                problem_solving=[],
                learning=["배운 점 3"],
            ),
            PdfActivity(
                activity_name="Gamma",
                detail=["상세 4"],
                responsibility=["담당 4"],
                problem_solving=[],
                learning=["배운 점 4"],
            ),
            PdfActivity(
                activity_name="Delta",
                detail=["상세 5"],
                responsibility=["담당 5"],
                problem_solving=[],
                learning=["배운 점 5"],
            ),
            PdfActivity(
                activity_name="Epsilon",
                detail=["상세 6"],
                responsibility=["담당 6"],
                problem_solving=[],
                learning=["배운 점 6"],
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


def test_validate_result_skips_blank_activity_names_and_trims_values():
    """공백-only 활동명은 제거하고 남는 활동명은 trim 값으로 정규화한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    result = PdfExtractionResult(
        activities=[
            PdfActivity(
                activity_name="   ",
                detail=["상세 1"],
                responsibility=["담당 1"],
                problem_solving=[],
                learning=["배운 점 1"],
            ),
            PdfActivity(
                activity_name=" Project A ",
                detail=["상세 2"],
                responsibility=["담당 2"],
                problem_solving=[],
                learning=["배운 점 2"],
            ),
        ]
    )

    activities = service._validate_result(result)

    assert [activity.activity_name for activity in activities] == ["Project A"]


def test_validate_result_removes_only_leading_dash_bullets_from_structured_fields():
    """구조화 필드에서는 선행 '- '만 제거하고 다른 마커는 유지한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    result = PdfExtractionResult(
        activities=[
            PdfActivity(
                activity_name=" Project A ",
                detail=["- 상세", "1. 유지", "• 유지"],
                responsibility=["  - 담당 업무", "• 그대로 유지"],
                problem_solving=[
                    PdfProblemSolvingItem(
                        no=9,
                        situation="- 문제 상황",
                        strategy="  - 대응 전략",
                        reason="- 선택 이유",
                    )
                ],
                learning=["- 배운 점", "1. 유지"],
            )
        ]
    )

    activities = service._validate_result(result)

    assert [activity.activity_name for activity in activities] == ["Project A"]
    assert activities[0].detail == ["상세", "1. 유지", "• 유지"]
    assert activities[0].responsibility == ["담당 업무", "• 그대로 유지"]
    assert activities[0].learning == ["배운 점", "1. 유지"]
    assert activities[0].problem_solving == [
        PdfProblemSolvingItem(
            no=1,
            situation="문제 상황",
            strategy="대응 전략",
            reason="선택 이유",
        )
    ]


def test_format_activities_for_callback_adds_single_dash_bullet_to_text_lists():
    """완료 콜백 전송용 텍스트 리스트는 기존 포트폴리오처럼 dash bullet으로 정규화한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    activity = PdfActivity(
        activity_name="Project A",
        detail=["상세", "- 이미 bullet", "  - 공백 bullet"],
        responsibility=["담당 업무"],
        problem_solving=[
            PdfProblemSolvingItem(
                no=1,
                situation="문제 상황",
                strategy="대응 전략",
                reason="선택 이유",
            )
        ],
        learning=["배운 점"],
    )

    formatted = service._format_activities_for_callback([activity])

    assert formatted[0].detail == ["- 상세", "- 이미 bullet", "- 공백 bullet"]
    assert formatted[0].responsibility == ["- 담당 업무"]
    assert formatted[0].learning == ["- 배운 점"]
    assert formatted[0].problem_solving == activity.problem_solving


def test_format_activities_for_callback_removes_empty_text_items():
    """완료 콜백 전송용 텍스트 리스트에서 빈 항목은 제거한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )
    activity = PdfActivity(
        activity_name="Project A",
        detail=["", "   ", "- ", "상세"],
        responsibility=["  - ", "담당 업무"],
        problem_solving=[],
        learning=["", "배운 점"],
    )

    formatted = service._format_activities_for_callback([activity])

    assert formatted[0].detail == ["- 상세"]
    assert formatted[0].responsibility == ["- 담당 업무"]
    assert formatted[0].learning == ["- 배운 점"]


def test_validate_file_allows_pdf_extension_for_wrong_mime_type():
    """MIME이 잘못돼도 .pdf 확장자면 fallback 허용한다."""
    service = PdfExtractionService(
        correction_client=DummyCorrectionClient(),
        generator=DummyGenerator(),
    )

    service._validate_file(
        file_bytes=b"%PDF-1.4",
        filename="portfolio.pdf",
        content_type="text/plain",
    )


def test_pdf_extraction_package_exports_are_sorted():
    """패키지 export 목록은 정렬되어 있다."""
    assert pdf_extraction_exports == sorted(pdf_extraction_exports)


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
