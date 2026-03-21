"""PDF extraction smoke script tests."""

import json
from pathlib import Path

from features.portfolio.pdf_extraction.schemas import PdfActivity, PdfExtractionResult
from scripts import pdf_extraction_smoke_test


class DummyGenerator:
    """Smoke script generator double."""

    def __init__(self, result: PdfExtractionResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def extract(self, file_bytes: bytes, filename: str) -> PdfExtractionResult:
        self.calls.append({"file_bytes": file_bytes, "filename": filename})
        return self.result


def _sample_result() -> PdfExtractionResult:
    return PdfExtractionResult(
        activities=[
            PdfActivity(
                activity_name="프로젝트 A",
                detail=["상세 설명"],
                responsibility=["담당 업무"],
                problem_solving=[],
                learning=["배운 점"],
            )
        ]
    )


def test_parse_args_accepts_pdf_path_and_options():
    """Smoke script는 PDF 경로와 옵션 인자를 파싱한다."""
    args = pdf_extraction_smoke_test.parse_args(
        [
            "--pdf",
            "sample.pdf",
            "--filename",
            "resume.pdf",
            "--model",
            "google/gemini-3.1-pro",
            "--dump-json",
        ]
    )

    assert args.pdf == Path("sample.pdf")
    assert args.filename == "resume.pdf"
    assert args.model == "google/gemini-3.1-pro"
    assert args.dump_json is True


def test_run_smoke_test_reads_pdf_and_invokes_generator(tmp_path):
    """Smoke script는 PDF bytes를 읽고 generator.extract를 호출한다."""
    pdf_path = tmp_path / "portfolio.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    generator = DummyGenerator(_sample_result())

    summary = pdf_extraction_smoke_test.run_smoke_test(
        pdf_path=pdf_path,
        filename="custom.pdf",
        model_name="openai/gpt-4.1-mini",
        generator=generator,
    )

    assert generator.calls == [{"file_bytes": b"%PDF-1.4", "filename": "custom.pdf"}]
    assert summary["pdf_path"] == str(pdf_path)
    assert summary["filename"] == "custom.pdf"
    assert summary["model_name"] == "openai/gpt-4.1-mini"
    assert summary["activity_count"] == 1
    assert summary["activities"][0]["activity_name"] == "프로젝트 A"


def test_main_dump_json_prints_smoke_summary(tmp_path, monkeypatch, capsys):
    """--dump-json 옵션은 smoke 결과를 JSON으로 출력한다."""
    pdf_path = tmp_path / "portfolio.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(
        pdf_extraction_smoke_test,
        "run_smoke_test",
        lambda **_: {
            "pdf_path": str(pdf_path),
            "filename": "portfolio.pdf",
            "model_name": "google/gemini-3.1-pro",
            "activity_count": 1,
            "activities": [{"activity_name": "프로젝트 A"}],
        },
    )

    exit_code = pdf_extraction_smoke_test.main(["--pdf", str(pdf_path), "--dump-json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["activity_count"] == 1
    assert payload["activities"][0]["activity_name"] == "프로젝트 A"
