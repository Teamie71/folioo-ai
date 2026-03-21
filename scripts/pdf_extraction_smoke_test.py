"""실환경 PDF 추출 generator smoke test 스크립트.

사용법:
    uv run python scripts/pdf_extraction_smoke_test.py --pdf sample.pdf
    uv run python scripts/pdf_extraction_smoke_test.py --pdf sample.pdf --dump-json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시 모듈 탐색용)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features.portfolio.pdf_extraction.generator import PdfExtractionGenerator

load_dotenv()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(
        description="OpenRouter/Gemini 기반 PDF 추출 generator smoke test를 실행합니다."
    )
    parser.add_argument("--pdf", type=Path, required=True, help="테스트할 PDF 파일 경로")
    parser.add_argument(
        "--filename",
        help="모델 입력에 사용할 파일명 override (기본값: --pdf 파일명)",
    )
    parser.add_argument(
        "--model",
        help="사용할 모델명 override (기본값: PDF_EXTRACTION_MODEL_NAME 또는 내장 기본값)",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="결과를 전체 JSON으로 출력",
    )
    return parser.parse_args(argv)


def run_smoke_test(
    pdf_path: Path,
    filename: str | None = None,
    model_name: str | None = None,
    generator: PdfExtractionGenerator | None = None,
) -> dict[str, object]:
    """PDF 파일을 읽어 generator smoke test를 수행한다."""
    if not pdf_path.exists() or not pdf_path.is_file():
        raise ValueError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    file_bytes = pdf_path.read_bytes()
    target_filename = filename or pdf_path.name
    extraction_generator = generator or PdfExtractionGenerator(model_name=model_name)
    result = extraction_generator.extract(file_bytes, target_filename)

    return {
        "pdf_path": str(pdf_path),
        "filename": target_filename,
        "model_name": model_name or extraction_generator._model_name,
        "activity_count": len(result.activities),
        "activities": result.model_dump()["activities"],
    }


def _print_human_summary(summary: dict[str, object]) -> None:
    """사람이 읽기 쉬운 smoke 결과 요약 출력"""
    print()
    print("=" * 70)
    print("  PDF 추출 스모크 테스트")
    print("=" * 70)
    print(f"  PDF 경로: {summary['pdf_path']}")
    print(f"  파일명: {summary['filename']}")
    print(f"  모델: {summary['model_name']}")
    print(f"  활동 수: {summary['activity_count']}")

    for index, activity in enumerate(summary["activities"], start=1):
        print(f"  [{index}] {activity['activity_name']}")

    print()


def main(argv: list[str] | None = None) -> int:
    """스크립트 실행 진입점"""
    args = parse_args(argv)
    summary = run_smoke_test(
        pdf_path=args.pdf,
        filename=args.filename,
        model_name=args.model,
    )

    if args.dump_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_human_summary(summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
