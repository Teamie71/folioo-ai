"""템플릿 등록 파이프라인 테스트."""

import copy
import json
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from features.visualization.templates import (
    BuildMetaOptions,
    SlideDraft,
    SlideDraftInput,
    SlideText,
    TextExtractionResult,
    build_template_metadata,
    count_pptx_slides,
    extract_slide_texts,
    validate_template_directory,
)
from scripts.templates.validate_template import main as validate_template_main

_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


@dataclass(frozen=True)
class FakeRenderResult:
    """테스트용 렌더 결과."""

    image_paths: tuple[Path, ...]


class FakeRenderer:
    """슬라이드 JPG 산출물을 만드는 렌더러 대역."""

    def __init__(self, slide_count: int) -> None:
        self.slide_count = slide_count
        self.calls: list[tuple[Path, Path]] = []

    def render(self, pptx_path: Path | str, output_dir: Path | str) -> FakeRenderResult:
        source = Path(pptx_path)
        target = Path(output_dir)
        self.calls.append((source, target))
        target.mkdir(parents=True, exist_ok=True)
        paths = []
        for index in range(1, self.slide_count + 1):
            image_path = target / f"slide-{index:02d}.jpg"
            image_path.write_bytes(f"jpg-{index}".encode())
            paths.append(image_path)
        return FakeRenderResult(image_paths=tuple(paths))


class FakeThumbnailBuilder:
    """그리드 thumbnail.jpg 산출물을 만드는 대역."""

    def __init__(self) -> None:
        self.slide_images: tuple[Path, ...] = ()

    def build(self, slide_images: Sequence[Path], output_path: Path) -> Path:
        self.slide_images = tuple(slide_images)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"thumbnail")
        return output_path


class FakeTextExtractor:
    """임시 텍스트 추출기 대역."""

    def __init__(self, slide_texts: tuple[SlideText, ...]) -> None:
        self.slide_texts = slide_texts

    def extract(self, pptx_path: Path) -> TextExtractionResult:
        return TextExtractionResult(
            deck_text=f"deck text: {pptx_path.name}", slide_texts=self.slide_texts
        )


class FakeDraftGenerator:
    """Source Slide 의미 필드 초안 생성 대역."""

    def __init__(self, drafts: list[SlideDraft]) -> None:
        self.drafts = drafts
        self.inputs: list[SlideDraftInput] = []

    def generate(self, slide: SlideDraftInput, categories) -> SlideDraft:
        assert "cover" in categories.key_set
        self.inputs.append(slide)
        return self.drafts[slide.slide_index]


def test_count_pptx_slides_and_extract_slide_texts(tmp_path: Path) -> None:
    """PPTX 슬라이드 수와 슬라이드별 텍스트를 덱 순서대로 추출한다."""
    pptx_path = tmp_path / "template.pptx"
    _make_template_pptx(pptx_path, slide_texts=["표지 제목", "개요\n역할"])

    assert count_pptx_slides(pptx_path) == 2
    assert extract_slide_texts(pptx_path) == (
        SlideText(slide_index=0, text="표지 제목"),
        SlideText(slide_index=1, text="개요\n역할"),
    )


def test_build_template_metadata_writes_assets_and_meta_draft(tmp_path: Path) -> None:
    """build 흐름은 슬라이드 JPG, 썸네일, 임시 텍스트, meta.json 초안을 작성한다."""
    template_dir = tmp_path / "blue"
    pptx_path = template_dir / "template.pptx"
    _make_template_pptx(pptx_path, slide_texts=["Cover", "Overview", "Second cover"])
    renderer = FakeRenderer(slide_count=3)
    thumbnail_builder = FakeThumbnailBuilder()
    draft_generator = FakeDraftGenerator(
        [
            SlideDraft(category="cover", description="중앙 표지", best_for="짧은 프로젝트명"),
            SlideDraft(category="overview", description="카드형 개요", best_for="역할과 기간"),
            SlideDraft(category="cover", description="이미지 표지", best_for="시각적 첫인상"),
        ]
    )

    result = build_template_metadata(
        BuildMetaOptions(
            pptx_path=pptx_path,
            template_id="blue",
            primary_color="#4A6CF7",
            output_path=template_dir / "meta.json",
            theme_name="블루 클린",
        ),
        renderer=renderer,
        thumbnail_builder=thumbnail_builder,
        text_extractor=FakeTextExtractor(
            (
                SlideText(slide_index=0, text="Cover"),
                SlideText(slide_index=1, text="Overview"),
                SlideText(slide_index=2, text="Second cover"),
            )
        ),
        draft_generator=draft_generator,
    )

    metadata = json.loads(result.meta_path.read_text(encoding="utf-8"))
    assert metadata["_draft_notice"]
    assert metadata["template_id"] == "blue"
    assert metadata["template_file"] == "template.pptx"
    assert metadata["theme"] == {"primary_color": "#4A6CF7", "name": "블루 클린"}
    assert [slide["slide_index"] for slide in metadata["slides"]] == [0, 1, 2]
    assert [slide["id"] for slide in metadata["slides"]] == [
        "cover_A",
        "overview_A",
        "cover_B",
    ]
    assert [slide["category"] for slide in metadata["slides"]] == [
        "cover",
        "overview",
        "cover",
    ]
    assert [path.name for path in result.slide_image_paths] == [
        "slide-01.jpg",
        "slide-02.jpg",
        "slide-03.jpg",
    ]
    assert result.thumbnail_path.read_bytes() == b"thumbnail"
    assert "### Slide 2" in result.text_output_path.read_text(encoding="utf-8")
    assert thumbnail_builder.slide_images == result.slide_image_paths
    assert [slide.text for slide in draft_generator.inputs] == ["Cover", "Overview", "Second cover"]


def test_validate_template_directory_accepts_valid_meta_with_distribution_warnings(
    tmp_path: Path,
) -> None:
    """검증기는 유효한 meta.json을 통과시키고 카테고리 분포는 warning만 낸다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    _write_meta(template_dir, slides=_valid_slides())

    result = validate_template_directory(template_dir)

    assert result.ok is True
    assert result.errors == ()
    assert result.warnings


@pytest.mark.parametrize(
    ("case_name", "mutator", "expected_error"),
    [
        (
            "required",
            lambda slides: slides[0].pop("description"),
            "필수 필드 누락: slides[0].description",
        ),
        (
            "invalid_category",
            lambda slides: slides[0].update(category="intro"),
            "표준 Enum 밖",
        ),
        (
            "unknown_category",
            lambda slides: slides[0].update(category="unknown"),
            "unknown일 수 없습니다",
        ),
        (
            "slide_index_gap",
            lambda slides: slides[1].update(slide_index=3),
            "slide_index는 slides 배열 순서대로",
        ),
        (
            "slide_index_order",
            lambda slides: (
                slides[0].update(slide_index=1),
                slides[1].update(slide_index=0),
            ),
            "배열 순서대로",
        ),
        (
            "duplicate_id",
            lambda slides: slides[1].update(id="cover_A"),
            "id 중복",
        ),
        (
            "new_category_without_enum_extension",
            lambda slides: slides[1].update(category="appendix"),
            "표준 Enum 밖",
        ),
    ],
)
def test_validate_template_directory_rejects_invalid_meta(
    tmp_path: Path,
    case_name: str,
    mutator,
    expected_error: str,
) -> None:
    """검증기는 필수 누락, Enum 밖 category, unknown, index 불일치, id 중복을 실패 처리한다."""
    del case_name
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    slides = _valid_slides()
    mutator(slides)
    _write_meta(template_dir, slides=slides)

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any(expected_error in error for error in result.errors)


def test_validate_template_directory_rejects_pptx_slide_count_mismatch(
    tmp_path: Path,
) -> None:
    """meta.json slides 수가 실제 PPTX 슬라이드 수와 다르면 실패한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview", "Extra"])
    _write_meta(template_dir, slides=_valid_slides())

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("PPTX 슬라이드 수와 일치하지 않습니다" in error for error in result.errors)


def test_validate_template_cli_returns_nonzero_on_validation_errors(tmp_path: Path) -> None:
    """validate_template.py CLI는 검증 실패 시 non-zero를 반환한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    slides = _valid_slides()
    slides[0]["category"] = "unknown"
    _write_meta(template_dir, slides=slides)

    assert validate_template_main([str(template_dir)]) == 1


def _valid_slides() -> list[dict[str, object]]:
    return [
        {
            "slide_index": 0,
            "id": "cover_A",
            "category": "cover",
            "description": "표지",
            "best_for": "첫 페이지",
        },
        {
            "slide_index": 1,
            "id": "overview_A",
            "category": "overview",
            "description": "개요",
            "best_for": "요약",
        },
    ]


def _write_meta(template_dir: Path, *, slides: list[dict[str, object]]) -> None:
    template_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "_draft_notice": "운영자 검토 필요",
        "template_id": "blue",
        "template_file": "template.pptx",
        "theme": {"primary_color": "#4A6CF7", "name": "블루 클린"},
        "slides": copy.deepcopy(slides),
    }
    (template_dir / "meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_template_pptx(path: Path, *, slide_texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slide_count = len(slide_texts)
    entries: dict[str, str] = {
        "[Content_Types].xml": _content_types(slide_count),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{_PACKAGE_RELATIONSHIPS_NS}">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/>'
            "</Relationships>"
        ),
        "ppt/presentation.xml": _presentation(slide_count),
        "ppt/_rels/presentation.xml.rels": _presentation_rels(slide_count),
    }
    for index, slide_text in enumerate(slide_texts, start=1):
        entries[f"ppt/slides/slide{index}.xml"] = _slide_xml(index, slide_text)
        entries[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_PACKAGE_RELATIONSHIPS_NS}"/>'
        )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _content_types(slide_count: int) -> str:
    overrides = [
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
    ]
    overrides.extend(
        f'<Override PartName="/ppt/slides/slide{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for index in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f"{''.join(overrides)}</Types>"
    )


def _presentation(slide_count: int) -> str:
    slide_ids = "".join(
        f'<p:sldId id="{255 + index}" r:id="rId{index}"/>' for index in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:presentation xmlns:p="{_PRESENTATION_NS}" xmlns:r="{_RELATIONSHIPS_NS}">'
        f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
        "</p:presentation>"
    )


def _presentation_rels(slide_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="{_SLIDE_RELATIONSHIP_TYPE}" '
        f'Target="slides/slide{index}.xml"/>'
        for index in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{_PACKAGE_RELATIONSHIPS_NS}">{relationships}</Relationships>'
    )


def _slide_xml(index: int, text: str) -> str:
    paragraphs = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in text.split("\n"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:sld xmlns:p="{_PRESENTATION_NS}" xmlns:a="{_DRAWINGML_NS}">'
        "<p:cSld><p:spTree>"
        f'<p:sp><p:nvSpPr><p:cNvPr id="{index}" name="Text {index}"/>'
        "</p:nvSpPr><p:txBody>"
        f"{paragraphs}"
        "</p:txBody></p:sp>"
        "</p:spTree></p:cSld>"
        "</p:sld>"
    )
