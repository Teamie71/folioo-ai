"""PPTX 템플릿 v2 compiler 기반 테스트."""

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from features.visualization.templates import (
    TemplateV2Extraction,
    build_template_v2_payloads,
    canonical_json_text,
    compile_template_v2,
    extract_template_v2_from_pptx,
    json_normalized_equal,
    read_json_payload,
    write_json_payload,
)
from scripts.templates.compile_template import main as compile_template_main
from scripts.templates.compile_template import parse_args

_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


def test_v2_payload_writer_generates_deterministic_skeleton(tmp_path: Path) -> None:
    """빈 추출 결과에서도 v2 meta/reference skeleton을 deterministic 하게 쓴다."""
    payloads = build_template_v2_payloads("ppt-v3")
    meta_path = write_json_payload(tmp_path / "meta.json", payloads.metadata)
    reference_path = write_json_payload(tmp_path / "reference.json", payloads.reference)

    expected_meta = """{
  "layout_groups": [],
  "runtime_slides": [],
  "schema_version": 2,
  "slots": [],
  "template_id": "ppt-v3"
}
"""
    expected_reference = """{
  "schema_version": 2,
  "shape_matches": [],
  "slide_pairs": [],
  "template_id": "ppt-v3"
}
"""
    assert meta_path.read_text(encoding="utf-8") == expected_meta
    assert reference_path.read_text(encoding="utf-8") == expected_reference
    assert canonical_json_text(payloads.metadata) == expected_meta


def test_v2_payload_writer_enriches_slot_capacity_deterministically() -> None:
    """reference match를 slot capacity 필드로 병합해 deterministic 하게 직렬화한다."""
    payloads = build_template_v2_payloads(
        "ppt-v3",
        extraction=TemplateV2Extraction(
            slots=(
                {
                    "slot_id": "slide2_shape26",
                    "shape_id": "26",
                    "kind": "text",
                    "editable": True,
                    "required": True,
                    "marker_color": "#FF0000",
                    "placeholder_text": "사용 기술",
                    "font_size_pt": 10.0,
                },
            ),
            shape_matches=(
                {
                    "slot_id": "slide2_shape26",
                    "example_text": "OpenAI API",
                    "example_char_count": 10,
                    "example_line_count": 1,
                    "output_text_color": "#000000",
                },
            ),
        ),
    )

    expected_meta = """{
  "layout_groups": [],
  "runtime_slides": [],
  "schema_version": 2,
  "slots": [
    {
      "allowed_actions": [
        "text",
        "remove"
      ],
      "editable": true,
      "example_char_count": 10,
      "example_line_count": 1,
      "example_text": "OpenAI API",
      "fit_policy": "basic_text_area",
      "font_size_pt": 10.0,
      "kind": "text",
      "marker_color": "#FF0000",
      "max_font_pt": 10.0,
      "max_lines": 1,
      "min_font_pt": 10.0,
      "nowrap": true,
      "output_text_color": "#000000",
      "placeholder_text": "사용 기술",
      "required": true,
      "shape_id": "26",
      "slot_id": "slide2_shape26"
    }
  ],
  "template_id": "ppt-v3"
}
"""
    assert canonical_json_text(payloads.metadata) == expected_meta
    assert "role_hint" not in payloads.metadata["slots"][0]


def test_v2_payload_writer_preserves_non_text_allowed_action_defaults() -> None:
    """capacity 확장 후에도 chart/remove/decorative action 계약은 유지한다."""
    payloads = build_template_v2_payloads(
        "ppt-v3",
        extraction=TemplateV2Extraction(
            slots=(
                {
                    "slot_id": "chart_slot",
                    "shape_id": "8",
                    "kind": "chart",
                    "editable": True,
                    "required": True,
                },
                {
                    "slot_id": "decorative_slot",
                    "shape_id": "9",
                    "kind": "decorative",
                    "editable": False,
                    "required": False,
                },
            )
        ),
    )

    assert payloads.metadata["slots"][0]["allowed_actions"] == ["chart"]
    assert payloads.metadata["slots"][1]["allowed_actions"] == []


def test_json_normalized_equal_ignores_key_order_and_detects_semantic_changes() -> None:
    """JSON normalize 비교는 key 순서 차이만 무시하고 의미 차이는 감지한다."""
    left = {"b": 1, "a": [{"z": "value", "y": 2}]}
    right = {"a": [{"y": 2, "z": "value"}], "b": 1}
    changed = {"a": [{"y": 3, "z": "value"}], "b": 1}

    assert json_normalized_equal(left, right) is True
    assert json_normalized_equal(left, changed) is False


def test_compile_template_v2_writes_meta_and_reference_json(tmp_path: Path) -> None:
    """v2 compiler는 template_id 정책에 맞춰 meta/reference skeleton을 생성한다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")

    result = compile_template_v2(template_dir, extraction=TemplateV2Extraction())

    assert result.ok is True
    assert result.updated is True
    assert result.meta_path == template_dir / "meta.json"
    assert result.reference_path == template_dir / "reference.json"
    assert read_json_payload(result.meta_path) == {
        "schema_version": 2,
        "template_id": "ppt-v3",
        "runtime_slides": [],
        "slots": [],
        "layout_groups": [],
    }
    assert read_json_payload(result.reference_path) == {
        "schema_version": 2,
        "template_id": "ppt-v3",
        "slide_pairs": [],
        "shape_matches": [],
    }


def test_extract_template_v2_from_pptx_uses_even_runtime_odd_example_pairs(
    tmp_path: Path,
) -> None:
    """1-based 짝수 슬라이드만 runtime 후보로 삼고 바로 뒤 홀수 슬라이드를 example로 묶는다."""
    pptx_path = tmp_path / "template.pptx"
    _make_template_pptx(pptx_path, _valid_v2_slide_xmls())

    extraction = extract_template_v2_from_pptx(pptx_path)

    assert extraction.errors == ()
    assert extraction.runtime_slides == (
        {
            "slide_index": 1,
            "slide_number": 2,
            "slide_filename": "slide2.xml",
            "slide_part": "ppt/slides/slide2.xml",
        },
    )
    assert extraction.slide_pairs == (
        {
            "runtime_slide_index": 1,
            "runtime_slide_number": 2,
            "runtime_slide_filename": "slide2.xml",
            "runtime_slide_part": "ppt/slides/slide2.xml",
            "example_slide_index": 2,
            "example_slide_number": 3,
            "example_slide_filename": "slide3.xml",
            "example_slide_part": "ppt/slides/slide3.xml",
        },
    )


def test_compile_template_v2_extracts_only_exact_red_marker_slots(tmp_path: Path) -> None:
    """정확한 #FF0000 텍스트 shape만 editable slot으로 만들고 non-red text는 제외한다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")

    result = compile_template_v2(template_dir)

    assert result.ok is True
    metadata = read_json_payload(result.meta_path)
    reference = read_json_payload(result.reference_path)
    assert metadata["runtime_slides"] == [
        {
            "slide_filename": "slide2.xml",
            "slide_index": 1,
            "slide_number": 2,
            "slide_part": "ppt/slides/slide2.xml",
        }
    ]
    assert reference["slide_pairs"][0]["runtime_slide_number"] == 2
    assert reference["slide_pairs"][0]["example_slide_number"] == 3
    assert reference["shape_matches"] == [
        {
            "example_char_count": 8,
            "example_line_count": 1,
            "example_shape_id": "30",
            "example_shape_name": "Example text",
            "example_slide_filename": "slide3.xml",
            "example_slide_index": 2,
            "example_slide_number": 3,
            "example_slide_part": "ppt/slides/slide3.xml",
            "example_text": "실제 프로젝트명",
            "match_confidence": "high",
            "match_score": 1.0,
            "output_text_color": "#123456",
            "runtime_shape_id": "10",
            "runtime_slide_index": 1,
            "runtime_slide_number": 2,
            "slot_id": "slide2_shape10",
        }
    ]

    assert len(metadata["slots"]) == 1
    slot = metadata["slots"][0]
    assert slot == {
        "allowed_actions": ["text", "remove"],
        "editable": True,
        "example_char_count": 8,
        "example_line_count": 1,
        "example_text": "실제 프로젝트명",
        "fit_policy": "basic_text_area",
        "font_size_pt": 18.0,
        "h_emu": 400,
        "kind": "text",
        "marker_color": "#FF0000",
        "max_font_pt": 18.0,
        "max_lines": 1,
        "min_font_pt": 10.8,
        "nowrap": True,
        "output_text_color": "#123456",
        "placeholder_text": "프로젝트명",
        "required": True,
        "shape_id": "10",
        "shape_name": "Exact red marker",
        "slide_filename": "slide2.xml",
        "slide_index": 1,
        "slide_number": 2,
        "slide_part": "ppt/slides/slide2.xml",
        "slot_id": "slide2_shape10",
        "w_emu": 300,
        "x_emu": 100,
        "y_emu": 200,
    }


def test_compile_template_v2_reports_mixed_color_run_as_contract_error(
    tmp_path: Path,
) -> None:
    """red/non-red mixed run은 보정하지 않고 fail 대상 오류로 보고한다."""
    template_dir = _make_template_dir(
        tmp_path,
        "ppt-v3",
        slide_xmls=(
            _slide_xml(1, ""),
            _slide_xml(
                2,
                _shape_xml(
                    20,
                    "Mixed marker",
                    (
                        _run_xml("경험명", color="FF0000"),
                        _run_xml(" - 고정 문구", color=None),
                    ),
                ),
            ),
            _slide_xml(3, _shape_xml(30, "Example", (_run_xml("경험명 예시"),))),
        ),
    )

    result = compile_template_v2(template_dir)

    assert result.ok is False
    assert result.updated is False
    assert not (template_dir / "meta.json").exists()
    assert any("non-red run이 섞여 있습니다" in error for error in result.errors)


def test_compile_template_v2_reports_reference_match_failure(tmp_path: Path) -> None:
    """editable slot의 예시 shape 매칭 실패는 계약 오류로 보고한다."""
    template_dir = _make_template_dir(
        tmp_path,
        "ppt-v3",
        slide_xmls=(
            _slide_xml(1, ""),
            _slide_xml(
                2,
                _shape_xml(20, "Marker", (_run_xml("경험명", color="FF0000"),)),
            ),
            _slide_xml(
                3,
                _shape_xml(
                    30,
                    "Far example",
                    (_run_xml("너무 먼 예시", color="123456"),),
                    x=1000,
                    y=1000,
                ),
            ),
        ),
    )

    result = compile_template_v2(template_dir)

    assert result.ok is False
    assert result.updated is False
    assert not (template_dir / "reference.json").exists()
    assert any("example shape 매칭에 실패했습니다" in error for error in result.errors)


def test_compile_template_v2_warns_on_low_confidence_reference_match(
    tmp_path: Path,
) -> None:
    """낮은 신뢰도의 example shape 매칭은 warning으로 남기고 reference에는 기록한다."""
    template_dir = _make_template_dir(
        tmp_path,
        "ppt-v3",
        slide_xmls=(
            _slide_xml(1, ""),
            _slide_xml(
                2,
                _shape_xml(20, "Marker", (_run_xml("경험명", color="FF0000"),)),
            ),
            _slide_xml(
                3,
                _shape_xml(
                    30,
                    "Shifted example",
                    (_run_xml("살짝 밀린 예시", color="123456"),),
                    x=50,
                ),
            ),
        ),
    )

    result = compile_template_v2(template_dir)

    assert result.ok is True
    assert any("매칭 신뢰도가 낮습니다" in warning for warning in result.warnings)
    reference = read_json_payload(result.reference_path)
    assert reference["shape_matches"][0]["match_confidence"] == "low"
    assert reference["shape_matches"][0]["example_text"] == "살짝 밀린 예시"


def test_compile_template_v2_falls_back_when_reference_color_missing(
    tmp_path: Path,
) -> None:
    """example run 색상을 못 가져오면 검정 fallback과 warning을 기록한다."""
    template_dir = _make_template_dir(
        tmp_path,
        "ppt-v3",
        slide_xmls=(
            _slide_xml(1, ""),
            _slide_xml(
                2,
                _shape_xml(20, "Marker", (_run_xml("경험명", color="FF0000"),)),
            ),
            _slide_xml(
                3,
                _shape_xml(30, "Uncolored example", (_run_xml("색상 없는 예시"),)),
            ),
        ),
    )

    result = compile_template_v2(template_dir)

    assert result.ok is True
    assert any("output_text_color를 찾지 못해 #000000" in warning for warning in result.warnings)
    reference = read_json_payload(result.reference_path)
    assert reference["shape_matches"][0]["output_text_color"] == "#000000"


def test_compile_template_v2_does_not_overtrust_large_containing_example_shape(
    tmp_path: Path,
) -> None:
    """slot을 포함하는 큰 example shape는 high confidence로 보지 않는다."""
    template_dir = _make_template_dir(
        tmp_path,
        "ppt-v3",
        slide_xmls=(
            _slide_xml(1, ""),
            _slide_xml(
                2,
                _shape_xml(20, "Marker", (_run_xml("경험명", color="FF0000"),)),
            ),
            _slide_xml(
                3,
                _shape_xml(
                    30,
                    "Huge example",
                    (_run_xml("큰 예시", color="123456"),),
                    x=-450,
                    y=-450,
                    width=1000,
                    height=1000,
                ),
            ),
        ),
    )

    result = compile_template_v2(template_dir)

    assert result.ok is True
    reference = read_json_payload(result.reference_path)
    assert reference["shape_matches"][0]["match_confidence"] == "low"
    assert reference["shape_matches"][0]["match_score"] < 0.75


def test_compile_template_v2_preserves_marker_soft_line_breaks(tmp_path: Path) -> None:
    """marker shape 내부 soft line break는 placeholder_text에 줄바꿈으로 남긴다."""
    template_dir = _make_template_dir(
        tmp_path,
        "ppt-v3",
        slide_xmls=(
            _slide_xml(1, ""),
            _slide_xml(
                2,
                _shape_xml(
                    20,
                    "Multiline marker",
                    (
                        _run_xml("첫 줄", color="FF0000"),
                        _break_xml(),
                        _run_xml("둘째 줄", color="FF0000"),
                    ),
                ),
            ),
            _slide_xml(3, _shape_xml(30, "Example", (_run_xml("예시"),))),
        ),
    )

    result = compile_template_v2(template_dir)

    assert result.ok is True
    metadata = read_json_payload(result.meta_path)
    assert metadata["slots"][0]["placeholder_text"] == "첫 줄\n둘째 줄"


def test_compile_template_cli_help_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """compile_template.py --help 가 argparse help를 출력하고 0으로 종료한다."""
    with pytest.raises(SystemExit) as exc_info:
        compile_template_main(["--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "template_dir" in captured.out
    assert "--check" in captured.out
    assert "--strict" in captured.out


def test_compile_template_cli_supports_out_check_and_strict_args(tmp_path: Path) -> None:
    """CLI 골격은 template_dir, --out, --check, --strict 인자를 파싱한다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")
    output_dir = tmp_path / "compiled"

    args = parse_args([str(template_dir), "--out", str(output_dir), "--check", "--strict"])

    assert args.template_dir == template_dir
    assert args.out == output_dir
    assert args.check is True
    assert args.strict is True


def test_compile_template_cli_out_writes_separate_output_dir(tmp_path: Path) -> None:
    """--out 실행은 입력 템플릿 디렉터리 대신 지정한 출력 디렉터리에 쓴다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")
    output_dir = tmp_path / "compiled"

    assert compile_template_main([str(template_dir), "--out", str(output_dir), "--strict"]) == 0

    assert (output_dir / "meta.json").is_file()
    assert (output_dir / "reference.json").is_file()
    assert not (template_dir / "meta.json").exists()
    assert not (template_dir / "reference.json").exists()


def test_compile_template_cli_out_rejects_template_dir_descendant(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--out 은 원본 template dir 내부에 산출물을 쓰지 못한다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")
    output_dir = template_dir / "compiled"

    assert compile_template_main([str(template_dir), "--out", str(output_dir)]) == 1

    captured = capsys.readouterr()
    assert "--out 출력 디렉터리" in captured.err
    assert not output_dir.exists()
    assert not (template_dir / "meta.json").exists()
    assert not (template_dir / "reference.json").exists()


def test_compile_template_cli_check_uses_normalized_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--check 는 key 순서 차이를 무시하고 의미 차이가 있으면 non-zero를 반환한다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")
    assert compile_template_main([str(template_dir)]) == 0
    capsys.readouterr()

    current_meta = read_json_payload(template_dir / "meta.json")
    reordered_meta = {
        "template_id": current_meta["template_id"],
        "slots": current_meta["slots"],
        "schema_version": current_meta["schema_version"],
        "runtime_slides": current_meta["runtime_slides"],
        "layout_groups": current_meta["layout_groups"],
    }
    (template_dir / "meta.json").write_text(
        json.dumps(reordered_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert compile_template_main([str(template_dir), "--check"]) == 0
    capsys.readouterr()

    reordered_meta["template_id"] = "changed"
    (template_dir / "meta.json").write_text(
        json.dumps(reordered_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert compile_template_main([str(template_dir), "--check"]) == 1
    captured = capsys.readouterr()
    assert "meta.json" in captured.err
    assert "최신 v2 산출물과 다릅니다" in captured.err


def _make_template_dir(
    tmp_path: Path,
    template_id: str,
    *,
    slide_xmls: tuple[str, ...] | None = None,
) -> Path:
    """테스트용 템플릿 디렉터리를 만든다."""
    template_dir = tmp_path / template_id
    template_dir.mkdir()
    _make_template_pptx(template_dir / "template.pptx", slide_xmls or _valid_v2_slide_xmls())
    return template_dir


def _valid_v2_slide_xmls() -> tuple[str, ...]:
    """기본 v2 compiler 테스트용 slide pair fixture를 만든다."""
    return (
        _slide_xml(1, ""),
        _slide_xml(
            2,
            "".join(
                (
                    _shape_xml(
                        10,
                        "Exact red marker",
                        (_run_xml("프로젝트명", color="FF0000", font_size=1800),),
                        x=100,
                        y=200,
                        width=300,
                        height=400,
                    ),
                    _shape_xml(
                        11,
                        "Fixed non red",
                        (_run_xml("고정 문구", color="222222"),),
                    ),
                    _shape_xml(
                        12,
                        "Almost red",
                        (_run_xml("거의 빨강", color="FE0000"),),
                    ),
                    _shape_xml(
                        13,
                        "Theme red",
                        (_run_xml("테마 빨강", scheme_color="accent2"),),
                    ),
                    _shape_xml(
                        14,
                        "Shaded exact red",
                        (
                            _run_xml(
                                "음영 빨강",
                                color="FF0000",
                                color_transform_xml='<a:shade val="50000"/>',
                            ),
                        ),
                    ),
                )
            ),
        ),
        _slide_xml(
            3,
            _shape_xml(
                30,
                "Example text",
                (_run_xml("실제 프로젝트명", color="123456"),),
                x=100,
                y=200,
                width=300,
                height=400,
            ),
        ),
    )


def _make_template_pptx(path: Path, slide_xmls: tuple[str, ...]) -> None:
    """테스트용 최소 PPTX 패키지를 생성한다."""
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


def _slide_xml(index: int, shapes_xml: str) -> str:
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


def _shape_xml(
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


def _run_xml(
    text: str,
    *,
    color: str | None = None,
    scheme_color: str | None = None,
    color_transform_xml: str = "",
    font_size: int = 1200,
) -> str:
    if color is not None:
        fill_xml = (
            f'<a:solidFill><a:srgbClr val="{color}">{color_transform_xml}</a:srgbClr></a:solidFill>'
        )
    elif scheme_color is not None:
        fill_xml = f'<a:solidFill><a:schemeClr val="{scheme_color}"/></a:solidFill>'
    else:
        fill_xml = ""
    return f'<a:r><a:rPr sz="{font_size}">{fill_xml}</a:rPr><a:t>{escape(text)}</a:t></a:r>'


def _break_xml() -> str:
    return "<a:br/>"
