"""템플릿 PPTX에서 Source Slide meta.json 초안을 생성하는 운영자용 CLI."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "pptx-worker"))
_DEFAULT_CATEGORY_SCHEMA_PATH = _REPO_ROOT / "templates" / "_schema" / "categories.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(
        description="template.pptx에서 슬라이드 이미지, thumbnail.jpg, meta.json 초안을 생성합니다."
    )
    parser.add_argument("--pptx", type=Path, required=True, help="입력 template.pptx 경로")
    parser.add_argument("--template-id", required=True, help="템플릿 ID")
    parser.add_argument("--primary-color", required=True, help="테마 대표 색상")
    parser.add_argument("--output", type=Path, required=True, help="출력 meta.json 경로")
    parser.add_argument("--theme-name", help="테마 표시 이름. 생략 시 template-id 사용")
    parser.add_argument(
        "--categories",
        type=Path,
        default=_DEFAULT_CATEGORY_SCHEMA_PATH,
        help="표준 카테고리 스키마 JSON 경로",
    )
    parser.add_argument("--slides-dir", type=Path, help="슬라이드 JPG 출력 디렉터리")
    parser.add_argument("--thumbnail", type=Path, help="그리드 thumbnail.jpg 출력 경로")
    parser.add_argument("--text-output", type=Path, help="임시 텍스트 markdown 출력 경로")
    parser.add_argument("--model", help="LLM 모델명 override")
    parser.add_argument("--temperature", type=float, default=0.1, help="LLM temperature")
    parser.add_argument("--soffice-bin", default="soffice", help="LibreOffice 실행 파일")
    parser.add_argument("--pdftoppm-bin", default="pdftoppm", help="pdftoppm 실행 파일")
    parser.add_argument("--timeout-seconds", type=float, default=60.0, help="외부 명령 타임아웃")
    parser.add_argument("--dpi", type=int, default=150, help="슬라이드 JPG 렌더링 DPI")
    parser.add_argument("--tmp-root", type=Path, default=Path("/tmp"), help="렌더링 임시 디렉터리")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """스크립트 실행 진입점."""
    from features.visualization.pptx import PptxRenderer, RenderOptions
    from features.visualization.templates import (
        BuildMetaOptions,
        LlmSlideDraftGenerator,
        build_template_metadata,
    )

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    renderer = PptxRenderer(
        options=RenderOptions(
            soffice_bin=args.soffice_bin,
            pdftoppm_bin=args.pdftoppm_bin,
            timeout_seconds=args.timeout_seconds,
            dpi=args.dpi,
            tmp_root=args.tmp_root,
        )
    )
    result = build_template_metadata(
        BuildMetaOptions(
            pptx_path=args.pptx,
            template_id=args.template_id,
            primary_color=args.primary_color,
            output_path=args.output,
            theme_name=args.theme_name,
            category_schema_path=args.categories,
            slides_dir=args.slides_dir,
            thumbnail_path=args.thumbnail,
            text_output_path=args.text_output,
        ),
        renderer=renderer,
        draft_generator=LlmSlideDraftGenerator(model=args.model, temperature=args.temperature),
    )

    print("meta.json 초안 생성 완료")
    print(f"- meta: {result.meta_path}")
    print(f"- thumbnail: {result.thumbnail_path}")
    print(f"- text: {result.text_output_path}")
    print(f"- slide images: {len(result.slide_image_paths)}개")
    print("주의: category, description, best_for, id는 운영자 검토 후 확정해야 합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
