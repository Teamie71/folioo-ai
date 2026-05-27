"""템플릿 meta.json 무결성을 검증하는 운영자/CI용 CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
_DEFAULT_CATEGORY_SCHEMA_PATH = _REPO_ROOT / "templates" / "_schema" / "categories.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(description="템플릿 디렉터리의 meta.json을 검증합니다.")
    parser.add_argument("template_dir", type=Path, help="template.pptx와 meta.json이 있는 디렉터리")
    parser.add_argument(
        "--categories",
        type=Path,
        default=_DEFAULT_CATEGORY_SCHEMA_PATH,
        help="표준 카테고리 스키마 JSON 경로",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """스크립트 실행 진입점."""
    from features.visualization.templates import validate_template_directory

    args = parse_args(argv)
    result = validate_template_directory(args.template_dir, category_schema_path=args.categories)

    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if not result.ok:
        return 1

    print("템플릿 검증 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
