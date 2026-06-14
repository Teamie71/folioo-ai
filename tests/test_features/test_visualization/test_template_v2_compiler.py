"""PPTX 템플릿 v2 compiler 기반 테스트."""

import json
from pathlib import Path

import pytest

from features.visualization.templates import (
    build_template_v2_payloads,
    canonical_json_text,
    compile_template_v2,
    json_normalized_equal,
    read_json_payload,
    write_json_payload,
)
from scripts.templates.compile_template import main as compile_template_main
from scripts.templates.compile_template import parse_args


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

    result = compile_template_v2(template_dir)

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


def test_compile_template_cli_check_uses_normalized_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--check 는 key 순서 차이를 무시하고 의미 차이가 있으면 non-zero를 반환한다."""
    template_dir = _make_template_dir(tmp_path, "ppt-v3")
    assert compile_template_main([str(template_dir)]) == 0
    capsys.readouterr()

    reordered_meta = {
        "template_id": "ppt-v3",
        "slots": [],
        "schema_version": 2,
        "runtime_slides": [],
        "layout_groups": [],
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


def _make_template_dir(tmp_path: Path, template_id: str) -> Path:
    """테스트용 템플릿 디렉터리를 만든다."""
    template_dir = tmp_path / template_id
    template_dir.mkdir()
    (template_dir / "template.pptx").write_bytes(b"pptx")
    return template_dir
