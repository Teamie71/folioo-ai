"""템플릿 등록 파이프라인 테스트."""

import copy
import json
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

import pytest

from features.visualization.templates import (
    BuildMetaOptions,
    SlideDraft,
    SlideDraftInput,
    SlideText,
    TextExtractionResult,
    build_template_metadata,
    compile_template_v2,
    count_pptx_slides,
    extract_slide_texts,
    read_json_payload,
    validate_template_directory,
)
from features.visualization.templates.thumbnail import PillowThumbnailBuilder
from features.visualization.text_fit import EMU_PER_PT
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
    """build 흐름은 배포 산출물과 중간 산출물을 분리해 작성한다."""
    template_dir = tmp_path / "blue"
    pptx_path = template_dir / "template.pptx"
    _make_template_pptx(pptx_path, slide_texts=["Cover", "Overview", "Second cover"])
    renderer = FakeRenderer(slide_count=3)
    thumbnail_builder = FakeThumbnailBuilder()
    draft_generator = FakeDraftGenerator(
        [
            SlideDraft(category="Cover", description="중앙 표지", best_for="짧은 프로젝트명"),
            SlideDraft(category="overview", description="카드형 개요", best_for="역할과 기간"),
            SlideDraft(category="cover ", description="이미지 표지", best_for="시각적 첫인상"),
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
    assert result.thumbnail_path == template_dir / "thumbnail.jpg"
    assert result.thumbnail_path.read_bytes() == b"thumbnail"
    assert "### Slide 2" in result.text_output_path.read_text(encoding="utf-8")
    assert not result.text_output_path.is_relative_to(template_dir)
    assert all(not path.is_relative_to(template_dir) for path in result.slide_image_paths)
    assert not (template_dir / "slides").exists()
    assert not (template_dir / "slide_text.md").exists()
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
            "non_string_category",
            lambda slides: slides[0].update(category=123),
            "slides[0].category는 비어 있지 않은 문자열",
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


@pytest.mark.parametrize(
    "template_file",
    ["ppt-for-test.pptx", "../shared.pptx", "/tmp/shared.pptx", "nested\\template.pptx"],
)
def test_validate_template_directory_rejects_invalid_template_file(
    tmp_path: Path,
    template_file: str,
) -> None:
    """검증기는 template_file을 경로 없는 template.pptx로 강제한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    _make_template_pptx(tmp_path / "shared.pptx", slide_texts=["Cover", "Overview"])
    _write_meta(template_dir, slides=_valid_slides(), template_file=template_file)

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("template_file은 경로 없이 template.pptx" in error for error in result.errors)


@pytest.mark.parametrize(
    ("mutator", "expected_error"),
    [
        (lambda metadata: metadata.update(template_id=123), "template_id는 비어 있지 않은 문자열"),
        (
            lambda metadata: metadata["theme"].update(primary_color=False),
            "theme.primary_color는 비어 있지 않은 문자열",
        ),
    ],
)
def test_validate_template_directory_rejects_non_string_metadata_fields(
    tmp_path: Path,
    mutator,
    expected_error: str,
) -> None:
    """검증기는 필수 문자열 필드의 타입 오류를 실패 처리한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    _write_meta(template_dir, slides=_valid_slides())
    metadata = json.loads((template_dir / "meta.json").read_text(encoding="utf-8"))
    mutator(metadata)
    (template_dir / "meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any(expected_error in error for error in result.errors)


@pytest.mark.parametrize(
    ("thumbnail_content", "expected_error"),
    [
        (None, "thumbnail.jpg 파일을 찾을 수 없습니다"),
        (b"", "thumbnail.jpg 파일이 비어 있습니다"),
    ],
)
def test_validate_template_directory_rejects_missing_or_empty_thumbnail(
    tmp_path: Path,
    thumbnail_content: bytes | None,
    expected_error: str,
) -> None:
    """검증기는 배포 산출물 thumbnail.jpg 누락과 빈 파일을 실패 처리한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    _write_meta(template_dir, slides=_valid_slides(), thumbnail_content=thumbnail_content)

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any(expected_error in error for error in result.errors)


def test_count_pptx_slides_rejects_missing_presentation_relationships(tmp_path: Path) -> None:
    """PPTX slide relationship이 없으면 파일명 순서 fallback 없이 실패한다."""
    pptx_path = tmp_path / "template.pptx"
    _make_template_pptx(pptx_path, slide_texts=["Cover"], include_relationships=False)

    with pytest.raises(ValueError, match="presentation relationship"):
        count_pptx_slides(pptx_path)


def test_count_pptx_slides_rejects_relationship_target_outside_slide_parts(
    tmp_path: Path,
) -> None:
    """PPTX slide relationship target이 ppt/slides/*.xml 밖이면 실패한다."""
    pptx_path = tmp_path / "template.pptx"
    _make_template_pptx(
        pptx_path,
        slide_texts=["Cover"],
        relationship_targets=["../media/image1.xml"],
    )

    with pytest.raises(ValueError, match=r"ppt/slides/\*\.xml"):
        count_pptx_slides(pptx_path)


def test_validate_template_directory_rejects_missing_slide_relationship_target(
    tmp_path: Path,
) -> None:
    """검증기는 relationship이 가리키는 슬라이드 XML 누락을 실패 처리한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(
        template_dir / "template.pptx",
        slide_texts=["Cover"],
        relationship_targets=["slides/missing.xml"],
    )
    _write_meta(template_dir, slides=[_valid_slides()[0]])

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("PPTX 슬라이드 XML을 찾을 수 없습니다" in error for error in result.errors)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cell_width": 0},
        {"cell_height": 0},
        {"gap": -1},
        {"max_columns": 0},
    ],
)
def test_pillow_thumbnail_builder_rejects_invalid_dimensions(kwargs: dict[str, int]) -> None:
    """썸네일 생성기는 잘못된 치수 옵션을 즉시 실패 처리한다."""
    with pytest.raises(ValueError):
        PillowThumbnailBuilder(**kwargs)


def test_validate_template_cli_returns_nonzero_on_validation_errors(tmp_path: Path) -> None:
    """validate_template.py CLI는 검증 실패 시 non-zero를 반환한다."""
    template_dir = tmp_path / "blue"
    _make_template_pptx(template_dir / "template.pptx", slide_texts=["Cover", "Overview"])
    slides = _valid_slides()
    slides[0]["category"] = "unknown"
    _write_meta(template_dir, slides=slides)

    assert validate_template_main([str(template_dir)]) == 1


def test_validate_template_directory_accepts_valid_v2_metadata(tmp_path: Path) -> None:
    """검증기는 v2 meta/reference 계약과 기존 thumbnail 검증을 함께 통과시킨다."""
    template_dir = _make_v2_template_dir(tmp_path)

    result = validate_template_directory(template_dir)

    assert result.ok is True
    assert result.errors == ()


def test_validate_template_directory_accepts_ppt_v3_chip_acceptance_fixture(
    tmp_path: Path,
) -> None:
    """ppt-v3 acceptance fixture는 marker, chip group, output color 계약을 만족한다."""
    template_dir = _make_v2_chip_acceptance_template_dir(tmp_path)

    result = validate_template_directory(template_dir, strict=True)

    assert result.ok is True
    assert result.errors == ()
    assert not any("output_text_color를 찾지 못해" in warning for warning in result.warnings)

    metadata = read_json_payload(template_dir / "meta.json")
    reference = read_json_payload(template_dir / "reference.json")
    assert {slot["marker_color"] for slot in metadata["slots"]} == {"#FF0000"}
    assert {slot["output_text_color"] for slot in metadata["slots"]} == {"#123456"}
    assert len(metadata["layout_groups"]) == 1
    group = metadata["layout_groups"][0]
    assert group["layout_type"] == "inline_label_group"
    assert group["item_shape_ids"] == ["20", "22", "24"]
    assert group["linked_background_by_item"] == {
        "20": {
            "slot_id": "slide2_shape20",
            "background_shape_id": "19",
            "background_shape_name": "Python chip background",
            "match_score": group["linked_background_by_item"]["20"]["match_score"],
            "resize_linked": True,
        },
        "22": {
            "slot_id": "slide2_shape22",
            "background_shape_id": "21",
            "background_shape_name": "FastAPI chip background",
            "match_score": group["linked_background_by_item"]["22"]["match_score"],
            "resize_linked": True,
        },
        "24": {
            "slot_id": "slide2_shape24",
            "background_shape_id": "23",
            "background_shape_name": "Postgres chip background",
            "match_score": group["linked_background_by_item"]["24"]["match_score"],
            "resize_linked": True,
        },
    }
    assert all(
        linked["match_score"] >= 0.72 for linked in group["linked_background_by_item"].values()
    )
    assert {match["output_text_color"] for match in reference["shape_matches"]} == {"#123456"}


def test_validate_template_directory_rejects_v2_missing_thumbnail(tmp_path: Path) -> None:
    """v2 metadata 경로에서도 기존 thumbnail 검증을 유지한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    (template_dir / "thumbnail.jpg").unlink()

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("thumbnail.jpg 파일을 찾을 수 없습니다" in error for error in result.errors)


def test_validate_template_cli_strict_promotes_unknown_layout_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """editable unknown layout은 기본 모드 warning, strict 모드 실패로 처리한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    metadata = read_json_payload(template_dir / "meta.json")
    metadata["slots"][0]["fit_policy"] = "unknown"
    _write_json(template_dir / "meta.json", metadata)

    assert validate_template_main([str(template_dir)]) == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "unknown" in captured.err

    assert validate_template_main([str(template_dir), "--strict"]) == 1
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert "unknown" in captured.err


def test_validate_template_directory_strict_promotes_low_confidence_layout_group(
    tmp_path: Path,
) -> None:
    """linked background 신뢰도가 낮은 inline_label_group은 strict 모드에서 실패한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    metadata = read_json_payload(template_dir / "meta.json")
    metadata["layout_groups"] = [
        {
            "group_id": "slide2_inline_label_group1",
            "slide_index": 1,
            "slide_number": 2,
            "layout_type": "inline_label_group",
            "item_shape_ids": ["10"],
            "linked_background_by_item": {},
        }
    ]
    _write_json(template_dir / "meta.json", metadata)

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert any(
        "linked background 신뢰도가 낮습니다" in warning for warning in default_result.warnings
    )
    assert strict_result.ok is False
    assert any("linked background 신뢰도가 낮습니다" in error for error in strict_result.errors)


def test_validate_template_directory_strict_promotes_low_match_score_layout_group(
    tmp_path: Path,
) -> None:
    """linked background match_score가 낮으면 strict 모드에서 실패한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    metadata = read_json_payload(template_dir / "meta.json")
    metadata["layout_groups"] = [
        {
            "group_id": "slide2_inline_label_group1",
            "slide_index": 1,
            "slide_number": 2,
            "layout_type": "inline_label_group",
            "item_shape_ids": ["10"],
            "linked_background_by_item": {
                "10": {
                    "slot_id": "slide2_shape10",
                    "background_shape_id": "9",
                    "background_shape_name": "Weak chip background",
                    "match_score": 0.1,
                    "resize_linked": True,
                }
            },
        }
    ]
    _write_json(template_dir / "meta.json", metadata)

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert any(
        "linked background 신뢰도가 낮습니다" in warning for warning in default_result.warnings
    )
    assert strict_result.ok is False
    assert any("linked background 신뢰도가 낮습니다" in error for error in strict_result.errors)


def test_validate_template_directory_strict_promotes_low_confidence_extraction_warning(
    tmp_path: Path,
) -> None:
    """PPTX 재추출 중 발견한 낮은 신뢰도 inline_label_group 후보도 strict 실패로 본다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    chip_specs = (
        (19, 20, "Python", 90, 100, 240),
        (21, 22, "FastAPI", 360, 370, 260),
        (23, 24, "Postgres", 650, 660, 280),
        (None, 25, "LangGraph", 960, 970, 300),
    )
    runtime_shapes: list[str] = []
    example_shapes: list[str] = []
    for index, (background_id, shape_id, text, background_x, text_x, width) in enumerate(
        chip_specs,
        start=1,
    ):
        if background_id is not None:
            runtime_shapes.append(
                _v2_shape_without_text_xml(
                    background_id,
                    f"{text} chip background",
                    x=background_x,
                    y=790,
                    width=width + 20,
                    height=120,
                )
            )
        runtime_shapes.append(
            _v2_shape_xml(
                shape_id,
                f"{text} chip",
                (_v2_run_xml(text, color="FF0000"),),
                x=text_x,
                y=800,
                width=width,
                height=100,
            )
        )
        example_shapes.append(
            _v2_shape_xml(
                30 + index,
                f"{text} example",
                (_v2_run_xml(text, color="123456"),),
                x=text_x,
                y=800,
                width=width,
                height=100,
            )
        )
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(2, "".join(runtime_shapes)),
            _v2_slide_xml(3, "".join(example_shapes)),
        ),
    )
    compile_result = compile_template_v2(template_dir)
    assert compile_result.ok is True
    assert any("background 신뢰도가 부족" in warning for warning in compile_result.warnings)
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert any("background 신뢰도가 부족" in warning for warning in default_result.warnings)
    assert strict_result.ok is False
    assert any("background 신뢰도가 부족" in error for error in strict_result.errors)


def test_validate_template_directory_warns_for_output_color_fallback_without_strict_failure(
    tmp_path: Path,
) -> None:
    """output_text_color fallback은 warning으로 남고 strict failure로 승격하지 않는다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(
                2,
                _v2_shape_xml(
                    20,
                    "Marker",
                    (_v2_run_xml("경험명", color="FF0000"),),
                    x=_pt(100),
                    y=_pt(100),
                    width=_pt(80),
                    height=_pt(24),
                ),
            ),
            _v2_slide_xml(
                3,
                _v2_shape_xml(
                    30,
                    "Uncolored example",
                    (_v2_run_xml("색상 없는 예시"),),
                    x=_pt(100),
                    y=_pt(100),
                    width=_pt(80),
                    height=_pt(24),
                ),
            ),
        ),
    )
    compile_result = compile_template_v2(template_dir)
    assert compile_result.ok is True
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert strict_result.ok is True
    assert any(
        "output_text_color를 찾지 못해 #000000" in warning for warning in default_result.warnings
    )
    assert any(
        "output_text_color를 찾지 못해 #000000" in warning for warning in strict_result.warnings
    )


def test_validate_template_directory_strict_promotes_narrow_editable_slot_warning(
    tmp_path: Path,
) -> None:
    """좁은 editable slot은 기본 warning, strict failure로 보고된다."""
    template_dir = _make_v2_template_dir(tmp_path)
    metadata = read_json_payload(template_dir / "meta.json")
    metadata["slots"][0]["w_emu"] = _pt(8)
    metadata["slots"][0]["h_emu"] = _pt(8)
    _write_json(template_dir / "meta.json", metadata)

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert any("editable slot이 좁습니다" in warning for warning in default_result.warnings)
    assert strict_result.ok is False
    assert any("editable slot이 좁습니다" in error for error in strict_result.errors)


def test_validate_template_directory_strict_promotes_invalid_editable_slot_geometry(
    tmp_path: Path,
) -> None:
    """0 이하 geometry는 좁은 slot보다 더 명확한 품질 위험으로 보고한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    metadata = read_json_payload(template_dir / "meta.json")
    metadata["slots"][0]["w_emu"] = 0
    metadata["slots"][0]["h_emu"] = -1
    _write_json(template_dir / "meta.json", metadata)

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert any(
        "editable slot geometry가 유효하지 않습니다" in warning
        for warning in default_result.warnings
    )
    assert strict_result.ok is False
    assert any(
        "editable slot geometry가 유효하지 않습니다" in error for error in strict_result.errors
    )


def test_validate_template_directory_strict_promotes_placeholder_residue_warning(
    tmp_path: Path,
) -> None:
    """example text가 placeholder와 같으면 잔존 위험을 strict failure로 승격한다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(
                2,
                _v2_shape_xml(
                    20,
                    "Marker",
                    (_v2_run_xml("여기에 프로젝트명", color="FF0000"),),
                    x=_pt(100),
                    y=_pt(100),
                    width=_pt(100),
                    height=_pt(24),
                ),
            ),
            _v2_slide_xml(
                3,
                _v2_shape_xml(
                    30,
                    "Placeholder example",
                    (_v2_run_xml("여기에 프로젝트명", color="123456"),),
                    x=_pt(100),
                    y=_pt(100),
                    width=_pt(100),
                    height=_pt(24),
                ),
            ),
        ),
    )
    compile_result = compile_template_v2(template_dir)
    assert compile_result.ok is True
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

    default_result = validate_template_directory(template_dir)
    strict_result = validate_template_directory(template_dir, strict=True)

    assert default_result.ok is True
    assert any("placeholder 잔존 위험" in warning for warning in default_result.warnings)
    assert strict_result.ok is False
    assert any("placeholder 잔존 위험" in error for error in strict_result.errors)


def test_validate_template_directory_does_not_flag_normal_example_text_as_placeholder_residue(
    tmp_path: Path,
) -> None:
    """정상 예시의 '입력/작성' 같은 일반 단어는 placeholder 잔존으로 보지 않는다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(
                2,
                _v2_shape_xml(
                    20,
                    "Marker",
                    (_v2_run_xml("프로젝트명", color="FF0000"),),
                    x=_pt(100),
                    y=_pt(100),
                    width=_pt(120),
                    height=_pt(24),
                ),
            ),
            _v2_slide_xml(
                3,
                _v2_shape_xml(
                    30,
                    "Legitimate example",
                    (_v2_run_xml("데이터 입력 자동화 작성 시스템", color="123456"),),
                    x=_pt(100),
                    y=_pt(100),
                    width=_pt(120),
                    height=_pt(24),
                ),
            ),
        ),
    )
    compile_result = compile_template_v2(template_dir)
    assert compile_result.ok is True
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")

    strict_result = validate_template_directory(template_dir, strict=True)

    assert strict_result.ok is True
    assert not any("placeholder 잔존 위험" in warning for warning in strict_result.warnings)


def test_validate_template_directory_rejects_stale_reference_shape_match(
    tmp_path: Path,
) -> None:
    """reference.json shape match 핵심 필드는 template.pptx 재추출 결과와 일치해야 한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    reference = read_json_payload(template_dir / "reference.json")
    reference["shape_matches"][0]["example_text"] = "오래된 예시 텍스트"
    _write_json(template_dir / "reference.json", reference)

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any(
        "reference.json.shape_matches[0].example_text" in error
        and "template.pptx 추출 결과와 일치하지 않습니다" in error
        for error in result.errors
    )


def test_validate_template_directory_rejects_example_slide_in_runtime_slides(
    tmp_path: Path,
) -> None:
    """예시 슬라이드가 runtime 후보에 포함된 v2 metadata는 실패한다."""
    template_dir = _make_v2_template_dir(tmp_path)
    metadata = read_json_payload(template_dir / "meta.json")
    metadata["runtime_slides"].append(
        {
            "slide_index": 2,
            "slide_number": 3,
            "slide_filename": "slide3.xml",
            "slide_part": "ppt/slides/slide3.xml",
        }
    )
    _write_json(template_dir / "meta.json", metadata)

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("example slide" in error for error in result.errors)


def test_validate_template_directory_rejects_mixed_color_run(tmp_path: Path) -> None:
    """red run과 non-red run이 섞인 runtime shape는 기본 모드에서도 실패한다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(
                2,
                _v2_shape_xml(
                    20,
                    "Mixed marker",
                    (
                        _v2_run_xml("경험명", color="FF0000"),
                        _v2_run_xml(" - 고정 문구"),
                    ),
                ),
            ),
            _v2_slide_xml(3, _v2_shape_xml(30, "Example", (_v2_run_xml("경험명 예시"),))),
        ),
    )
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")
    _write_json(
        template_dir / "meta.json",
        {
            "schema_version": 2,
            "template_id": "ppt-v3",
            "runtime_slides": [
                {
                    "slide_index": 1,
                    "slide_number": 2,
                    "slide_filename": "slide2.xml",
                    "slide_part": "ppt/slides/slide2.xml",
                }
            ],
            "slots": [],
            "layout_groups": [],
        },
    )

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("non-red run이 섞여 있습니다" in error for error in result.errors)


def test_validate_template_directory_rejects_runtime_slide_without_exact_marker(
    tmp_path: Path,
) -> None:
    """정확한 #FF0000 marker가 없는 runtime slide는 기본 모드에서도 실패한다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(
                2,
                _v2_shape_xml(20, "Fixed non-red", (_v2_run_xml("고정 문구", color="222222"),)),
            ),
            _v2_slide_xml(3, _v2_shape_xml(30, "Example", (_v2_run_xml("예시"),))),
        ),
    )
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")
    _write_minimal_v2_meta(template_dir, slots=[])

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("정확한 #FF0000 editable marker가 없습니다" in error for error in result.errors)


@pytest.mark.parametrize(
    ("text", "color", "scheme_color"),
    [
        pytest.param("거의 빨강", "FE0000", None, id="almost-red"),
        pytest.param("테마 빨강", None, "accent2", id="theme-red"),
    ],
)
def test_validate_template_directory_rejects_non_exact_red_markers(
    tmp_path: Path,
    text: str,
    color: str | None,
    scheme_color: str | None,
) -> None:
    """#FE0000/theme red는 #FF0000 editable marker로 인정하지 않는다."""
    run_xml = _v2_run_xml(text, color=color, scheme_color=scheme_color)
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(2, _v2_shape_xml(20, "Non exact marker", (run_xml,))),
            _v2_slide_xml(3, _v2_shape_xml(30, "Example", (_v2_run_xml("예시"),))),
        ),
    )
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")
    _write_minimal_v2_meta(template_dir, slots=[])

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("정확한 #FF0000 editable marker가 없습니다" in error for error in result.errors)


def test_validate_template_directory_rejects_reference_match_failure(tmp_path: Path) -> None:
    """editable slot의 example shape 매칭 실패는 기본 모드에서도 실패한다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(2, _v2_shape_xml(20, "Marker", (_v2_run_xml("경험명", color="FF0000"),))),
            _v2_slide_xml(
                3,
                _v2_shape_xml(
                    30,
                    "Far example",
                    (_v2_run_xml("너무 먼 예시", color="123456"),),
                    x=1000,
                    y=1000,
                ),
            ),
        ),
    )
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")
    _write_minimal_v2_meta(
        template_dir,
        slots=[
            {
                "slot_id": "slide2_shape20",
                "slide_index": 1,
                "slide_number": 2,
                "slide_filename": "slide2.xml",
                "slide_part": "ppt/slides/slide2.xml",
                "shape_id": "20",
                "shape_name": "Marker",
                "x_emu": 0,
                "y_emu": 0,
                "w_emu": 100,
                "h_emu": 100,
                "placeholder_text": "경험명",
                "marker_color": "#FF0000",
                "kind": "text",
                "editable": True,
                "required": True,
                "allowed_actions": ["text", "remove"],
            }
        ],
    )

    result = validate_template_directory(template_dir)

    assert result.ok is False
    assert any("example shape 매칭에 실패했습니다" in error for error in result.errors)


def _valid_slides() -> list[dict[str, object]]:
    """유효한 meta.json slides fixture를 만든다.

    Returns:
        list[dict[str, object]]: 검증을 통과하는 Source Slide 메타데이터 목록.
    """
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


def _write_meta(
    template_dir: Path,
    *,
    slides: list[dict[str, object]],
    template_file: str = "template.pptx",
    thumbnail_content: bytes | None = b"thumbnail",
) -> None:
    """테스트용 meta.json 파일을 작성한다.

    Args:
        template_dir: meta.json을 생성할 템플릿 디렉터리.
        slides: meta.json의 slides 배열에 넣을 Source Slide 메타데이터.
        template_file: meta.json의 template_file 값.
        thumbnail_content: thumbnail.jpg에 쓸 바이트. None이면 파일을 만들지 않는다.

    Returns:
        None: 파일 생성 부작용만 수행한다.
    """
    template_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "_draft_notice": "운영자 검토 필요",
        "template_id": "blue",
        "template_file": template_file,
        "theme": {"primary_color": "#4A6CF7", "name": "블루 클린"},
        "slides": copy.deepcopy(slides),
    }
    (template_dir / "meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if thumbnail_content is not None:
        (template_dir / "thumbnail.jpg").write_bytes(thumbnail_content)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """테스트용 JSON 파일을 작성한다."""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_minimal_v2_meta(template_dir: Path, *, slots: list[dict[str, object]]) -> None:
    """테스트용 최소 v2 meta.json 파일을 작성한다."""
    _write_json(
        template_dir / "meta.json",
        {
            "schema_version": 2,
            "template_id": "ppt-v3",
            "runtime_slides": [
                {
                    "slide_index": 1,
                    "slide_number": 2,
                    "slide_filename": "slide2.xml",
                    "slide_part": "ppt/slides/slide2.xml",
                }
            ],
            "slots": slots,
            "layout_groups": [],
        },
    )


def _make_template_pptx(
    path: Path,
    *,
    slide_texts: list[str],
    include_relationships: bool = True,
    relationship_targets: list[str] | None = None,
) -> None:
    """테스트용 최소 PPTX 패키지를 생성한다.

    Args:
        path: 생성할 PPTX 파일 경로.
        slide_texts: 각 슬라이드 XML에 넣을 텍스트 목록.
        include_relationships: presentation relationship 파일 포함 여부.
        relationship_targets: 각 슬라이드 relationship Target 값. 생략하면 기본 slideN.xml을 쓴다.

    Returns:
        None: PPTX ZIP 파일 생성 부작용만 수행한다.
    """
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
    }
    if include_relationships:
        entries["ppt/_rels/presentation.xml.rels"] = _presentation_rels(
            slide_count,
            relationship_targets=relationship_targets,
        )
    for index, slide_text in enumerate(slide_texts, start=1):
        entries[f"ppt/slides/slide{index}.xml"] = _slide_xml(index, slide_text)
        entries[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_PACKAGE_RELATIONSHIPS_NS}"/>'
        )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _make_v2_chip_acceptance_template_dir(tmp_path: Path) -> Path:
    """chip group/output color 기준을 만족하는 ppt-v3 acceptance fixture를 만든다."""
    template_dir = tmp_path / "ppt-v3-acceptance"
    template_dir.mkdir()
    chips = (
        (19, 20, "Python", "Python 3.12", 90, 100, 50),
        (21, 22, "FastAPI", "FastAPI 운영", 160, 170, 60),
        (23, 24, "Postgres", "Postgres RDS", 240, 250, 70),
    )
    runtime_shapes: list[str] = []
    example_shapes: list[str] = []
    for index, (
        background_id,
        shape_id,
        placeholder,
        example,
        background_x,
        text_x,
        width,
    ) in enumerate(chips, start=1):
        runtime_shapes.append(
            _v2_shape_without_text_xml(
                background_id,
                f"{placeholder} chip background",
                x=_pt(background_x),
                y=_pt(95),
                width=_pt(width + 8),
                height=_pt(26),
            )
        )
        runtime_shapes.append(
            _v2_shape_xml(
                shape_id,
                f"{placeholder} chip",
                (_v2_run_xml(placeholder, color="FF0000"),),
                x=_pt(text_x),
                y=_pt(100),
                width=_pt(width),
                height=_pt(18),
            )
        )
        example_shapes.append(
            _v2_shape_xml(
                30 + index,
                f"{placeholder} example",
                (_v2_run_xml(example, color="123456"),),
                x=_pt(text_x),
                y=_pt(100),
                width=_pt(width),
                height=_pt(18),
            )
        )
    _make_v2_template_pptx(
        template_dir / "template.pptx",
        (
            _v2_slide_xml(1, ""),
            _v2_slide_xml(2, "".join(runtime_shapes)),
            _v2_slide_xml(3, "".join(example_shapes)),
        ),
    )
    result = compile_template_v2(template_dir)
    assert result.ok is True
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")
    return template_dir


def _make_v2_template_dir(tmp_path: Path) -> Path:
    """검증 가능한 v2 template dir fixture를 만든다."""
    template_dir = tmp_path / "ppt-v3"
    template_dir.mkdir()
    _make_v2_template_pptx(template_dir / "template.pptx", _valid_v2_slide_xmls())
    result = compile_template_v2(template_dir)
    assert result.ok is True
    (template_dir / "thumbnail.jpg").write_bytes(b"thumbnail")
    return template_dir


def _valid_v2_slide_xmls() -> tuple[str, ...]:
    """기본 v2 validator 테스트용 slide pair fixture를 만든다."""
    return (
        _v2_slide_xml(1, ""),
        _v2_slide_xml(
            2,
            _v2_shape_xml(
                10,
                "Exact red marker",
                (_v2_run_xml("프로젝트명", color="FF0000", font_size=1800),),
                x=100,
                y=200,
                width=300,
                height=400,
            ),
        ),
        _v2_slide_xml(
            3,
            _v2_shape_xml(
                30,
                "Example text",
                (_v2_run_xml("실제 프로젝트명", color="123456"),),
                x=100,
                y=200,
                width=300,
                height=400,
            ),
        ),
    )


def _make_v2_template_pptx(path: Path, slide_xmls: tuple[str, ...]) -> None:
    """테스트용 최소 v2 PPTX 패키지를 생성한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    slide_count = len(slide_xmls)
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
    for index, slide_xml in enumerate(slide_xmls, start=1):
        entries[f"ppt/slides/slide{index}.xml"] = slide_xml
        entries[f"ppt/slides/_rels/slide{index}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Relationships xmlns="{_PACKAGE_RELATIONSHIPS_NS}"/>'
        )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _v2_slide_xml(index: int, shapes_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:sld xmlns:p="{_PRESENTATION_NS}" xmlns:a="{_DRAWINGML_NS}" '
        f'xmlns:r="{_RELATIONSHIPS_NS}">'
        "<p:cSld><p:spTree>"
        f'<p:nvGrpSpPr><p:cNvPr id="{index}" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        "</p:nvGrpSpPr><p:grpSpPr/>"
        f"{shapes_xml}"
        "</p:spTree></p:cSld></p:sld>"
    )


def _v2_shape_xml(
    shape_id: int,
    name: str,
    runs_xml: tuple[str, ...],
    *,
    x: int = 0,
    y: int = 0,
    width: int = 100,
    height: int = 100,
) -> str:
    return (
        "<p:sp>"
        "<p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr/>'
        "</p:nvSpPr>"
        "<p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</p:spPr>"
        "<p:txBody><a:bodyPr/><a:lstStyle/><a:p>"
        f"{''.join(runs_xml)}"
        "</a:p></p:txBody>"
        "</p:sp>"
    )


def _v2_shape_without_text_xml(
    shape_id: int,
    name: str,
    *,
    x: int = 0,
    y: int = 0,
    width: int = 100,
    height: int = 100,
) -> str:
    return (
        "<p:sp>"
        "<p:nvSpPr>"
        f'<p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
        "<p:cNvSpPr/><p:nvPr/>"
        "</p:nvSpPr>"
        "<p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</p:spPr>"
        "</p:sp>"
    )


def _v2_run_xml(
    text: str,
    *,
    color: str | None = None,
    scheme_color: str | None = None,
    font_size: int = 1200,
) -> str:
    if color:
        fill_xml = f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
    elif scheme_color:
        fill_xml = f'<a:solidFill><a:schemeClr val="{scheme_color}"/></a:solidFill>'
    else:
        fill_xml = ""
    return f'<a:r><a:rPr sz="{font_size}">{fill_xml}</a:rPr><a:t>{escape(text)}</a:t></a:r>'


def _pt(value: int | float) -> int:
    return int(value * EMU_PER_PT)


def _content_types(slide_count: int) -> str:
    """PPTX Content_Types XML을 만든다.

    Args:
        slide_count: 포함할 슬라이드 개수.

    Returns:
        str: `[Content_Types].xml`에 쓸 XML 문자열.
    """
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
    """presentation.xml 내용을 만든다.

    Args:
        slide_count: 포함할 슬라이드 개수.

    Returns:
        str: `ppt/presentation.xml`에 쓸 XML 문자열.
    """
    slide_ids = "".join(
        f'<p:sldId id="{255 + index}" r:id="rId{index}"/>' for index in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<p:presentation xmlns:p="{_PRESENTATION_NS}" xmlns:r="{_RELATIONSHIPS_NS}">'
        f"<p:sldIdLst>{slide_ids}</p:sldIdLst>"
        "</p:presentation>"
    )


def _presentation_rels(
    slide_count: int,
    *,
    relationship_targets: list[str] | None = None,
) -> str:
    """presentation.xml.rels 내용을 만든다.

    Args:
        slide_count: 포함할 슬라이드 relationship 개수.
        relationship_targets: 각 relationship의 Target 값.

    Returns:
        str: `ppt/_rels/presentation.xml.rels`에 쓸 XML 문자열.
    """
    targets = relationship_targets or [
        f"slides/slide{index}.xml" for index in range(1, slide_count + 1)
    ]
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="{_SLIDE_RELATIONSHIP_TYPE}" '
        f'Target="{targets[index - 1]}"/>'
        for index in range(1, slide_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{_PACKAGE_RELATIONSHIPS_NS}">{relationships}</Relationships>'
    )


def _slide_xml(index: int, text: str) -> str:
    """단일 슬라이드 XML 내용을 만든다.

    Args:
        index: 1부터 시작하는 슬라이드 번호.
        text: 슬라이드 텍스트 도형에 넣을 텍스트.

    Returns:
        str: `ppt/slides/slideN.xml`에 쓸 XML 문자열.
    """
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
