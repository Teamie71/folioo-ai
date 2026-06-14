"""PPTX 템플릿 v2 metadata/reference 컴파일러 기반."""

from dataclasses import dataclass
from pathlib import Path

from .pptx import extract_template_v2_from_pptx
from .v2 import (
    META_JSON_NAME,
    REFERENCE_JSON_NAME,
    TEMPLATE_PPTX_NAME,
    TemplateV2Extraction,
    build_template_v2_payloads,
    json_file_normalized_equal,
    write_json_payload,
)


@dataclass(frozen=True, slots=True)
class TemplateV2CompileResult:
    """v2 compiler 실행 결과."""

    meta_path: Path
    reference_path: Path
    checked: bool
    updated: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """오류가 없으면 True."""
        return not self.errors


def compile_template_v2(
    template_dir: Path | str,
    *,
    output_dir: Path | str | None = None,
    check: bool = False,
    strict: bool = False,
    extraction: TemplateV2Extraction | None = None,
) -> TemplateV2CompileResult:
    """템플릿 디렉터리에서 v2 meta/reference JSON 을 생성하거나 최신성을 확인한다."""
    del strict
    root = Path(template_dir)
    _validate_template_dir(root)

    target_dir = Path(output_dir) if output_dir is not None else root
    source_extraction = extraction or extract_template_v2_from_pptx(root / TEMPLATE_PPTX_NAME)
    meta_path = target_dir / META_JSON_NAME
    reference_path = target_dir / REFERENCE_JSON_NAME
    if source_extraction.errors:
        return TemplateV2CompileResult(
            meta_path=meta_path,
            reference_path=reference_path,
            checked=check,
            updated=False,
            errors=tuple(source_extraction.errors),
            warnings=tuple(source_extraction.warnings),
        )

    payloads = build_template_v2_payloads(root.name, extraction=source_extraction)

    if check:
        errors = tuple(
            error
            for error in (
                _check_json_file(meta_path, payloads.metadata, META_JSON_NAME),
                _check_json_file(reference_path, payloads.reference, REFERENCE_JSON_NAME),
            )
            if error is not None
        )
        return TemplateV2CompileResult(
            meta_path=meta_path,
            reference_path=reference_path,
            checked=True,
            updated=False,
            errors=errors,
            warnings=tuple(source_extraction.warnings),
        )

    write_json_payload(meta_path, payloads.metadata)
    write_json_payload(reference_path, payloads.reference)
    return TemplateV2CompileResult(
        meta_path=meta_path,
        reference_path=reference_path,
        checked=False,
        updated=True,
        warnings=tuple(source_extraction.warnings),
    )


def _validate_template_dir(template_dir: Path) -> None:
    if not template_dir.is_dir():
        raise ValueError(f"템플릿 디렉터리를 찾을 수 없습니다: {template_dir}")
    template_pptx = template_dir / TEMPLATE_PPTX_NAME
    if not template_pptx.is_file():
        raise ValueError(f"template.pptx 파일을 찾을 수 없습니다: {template_pptx}")


def _check_json_file(path: Path, expected_payload: dict, label: str) -> str | None:
    try:
        if json_file_normalized_equal(path, expected_payload):
            return None
    except ValueError as exc:
        return f"{label} normalize 비교 실패: {exc}"
    return f"{label}이 최신 v2 산출물과 다릅니다: {path}"
