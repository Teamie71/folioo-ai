"""PPTX 템플릿 v2 metadata/reference compiler CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자를 파싱한다."""
    parser = argparse.ArgumentParser(
        description="template.pptx에서 v2 meta.json/reference.json skeleton을 생성합니다."
    )
    parser.add_argument("template_dir", type=Path, help="template.pptx가 있는 템플릿 디렉터리")
    parser.add_argument("--out", type=Path, help="meta.json/reference.json을 쓸 별도 출력 디렉터리")
    parser.add_argument(
        "--check",
        action="store_true",
        help="현재 JSON 파일과 새 산출물을 normalize 비교하고 파일은 쓰지 않습니다.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="품질 warning을 실패로 승격합니다. 후속 validator 연동을 위한 옵션입니다.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """스크립트 실행 진입점."""
    from features.visualization.templates import compile_template_v2

    args = parse_args(argv)
    try:
        result = compile_template_v2(
            args.template_dir,
            output_dir=args.out,
            check=args.check,
            strict=args.strict,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if not result.ok:
        return 1

    if result.checked:
        print("v2 템플릿 JSON 최신성 확인 완료")
    else:
        print("v2 템플릿 JSON 생성 완료")
        print(f"- meta: {result.meta_path}")
        print(f"- reference: {result.reference_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
