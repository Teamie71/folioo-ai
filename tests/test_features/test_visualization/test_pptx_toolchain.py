"""PPTX 패키지 도구 체인 어댑터 테스트."""

import subprocess
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path

import defusedxml.minidom
import pytest

from features.visualization.pptx import PptxToolchain, PptxToolchainError

_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """Anthropic PPTX 스킬 스크립트 구조 fixture."""
    root = tmp_path / "pptx-skill"
    for rel_path in (
        "scripts/office/unpack.py",
        "scripts/clean.py",
        "scripts/office/pack.py",
        "scripts/office/validate.py",
    ):
        script = root / rel_path
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# test stub\n", encoding="utf-8")
    return root


class FakePptxSkillRunner:
    """테스트용 Anthropic PPTX 스크립트 대역."""

    def __init__(
        self,
        validate_returncodes: list[int] | None = None,
        repair_returncode: int = 0,
    ) -> None:
        self.commands: list[list[str]] = []
        self.validate_returncodes = validate_returncodes or [0]
        self.repair_returncode = repair_returncode

    def __call__(
        self,
        command: Sequence[str],
        cwd: Path | None,
    ) -> subprocess.CompletedProcess[str]:
        del cwd
        command_list = list(command)
        self.commands.append(command_list)
        script_name = Path(command_list[1]).name

        if script_name == "unpack.py":
            _unzip(Path(command_list[2]), Path(command_list[3]))
            return _completed(command_list)
        if script_name == "clean.py":
            _clean_unreferenced_slides(Path(command_list[2]))
            return _completed(command_list)
        if script_name == "pack.py":
            _zip_dir(Path(command_list[2]), Path(command_list[3]))
            return _completed(command_list)
        if script_name == "validate.py":
            if "--auto-repair" in command_list:
                return _completed(
                    command_list,
                    returncode=self.repair_returncode,
                    stdout="Auto-repaired 1 issue(s)" if self.repair_returncode == 0 else "",
                    stderr="repair crashed" if self.repair_returncode != 0 else "",
                )
            if not self.validate_returncodes:
                raise AssertionError("validate_returncodes 리스트가 소진되었습니다.")
            returncode = self.validate_returncodes.pop(0)
            stdout = "All validations PASSED!" if returncode == 0 else "FAILED - schema error"
            return _completed(command_list, returncode=returncode, stdout=stdout)

        raise AssertionError(f"unexpected script: {script_name}")

    def names(self) -> list[str]:
        return [Path(command[1]).name for command in self.commands]

    def count(self, script_name: str) -> int:
        return self.names().count(script_name)


def test_build_selected_deck_removes_unselected_slides_and_cleans_tmp(
    tmp_path: Path,
    skill_dir: Path,
):
    template = tmp_path / "template.pptx"
    output = tmp_path / "selected.pptx"
    _make_template_pptx(template, slide_count=3)

    before_tmp_dirs = _toolchain_tmp_dirs()
    runner = FakePptxSkillRunner()
    toolchain = PptxToolchain(skill_dir, runner=runner)

    result = toolchain.build_selected_deck(
        template,
        output,
        selected_slide_filenames=["slide1.xml", "slide3.xml"],
    )

    assert result.output_pptx == output
    assert result.selected_slide_filenames == ("slide1.xml", "slide3.xml")
    assert result.remaining_slide_filenames == ("slide1.xml", "slide3.xml")
    assert result.repaired is False
    assert runner.names() == ["unpack.py", "clean.py", "pack.py", "validate.py"]
    assert _slides_in_pptx(output) == ["slide1.xml", "slide3.xml"]

    with zipfile.ZipFile(output, "r") as zf:
        names = set(zf.namelist())
    assert "ppt/slides/slide1.xml" in names
    assert "ppt/slides/slide2.xml" not in names
    assert "ppt/slides/slide3.xml" in names
    assert _toolchain_tmp_dirs() == before_tmp_dirs


def test_build_selected_deck_reorders_slides_by_selected_sequence(
    tmp_path: Path,
    skill_dir: Path,
):
    """선택 슬라이드 순서가 최종 presentation.xml 순서가 된다."""
    template = tmp_path / "template.pptx"
    output = tmp_path / "selected.pptx"
    _make_template_pptx(template, slide_count=3)

    runner = FakePptxSkillRunner()
    toolchain = PptxToolchain(skill_dir, runner=runner)

    result = toolchain.build_selected_deck(
        template,
        output,
        selected_slide_filenames=["slide3.xml", "slide1.xml"],
    )

    assert result.remaining_slide_filenames == ("slide3.xml", "slide1.xml")
    assert _slides_in_pptx(output) == ["slide3.xml", "slide1.xml"]


def test_validate_failure_runs_repair_then_revalidates(
    tmp_path: Path,
    skill_dir: Path,
):
    template = tmp_path / "template.pptx"
    output = tmp_path / "selected.pptx"
    _make_template_pptx(template, slide_count=2)

    runner = FakePptxSkillRunner(validate_returncodes=[1, 0])
    toolchain = PptxToolchain(skill_dir, runner=runner)

    result = toolchain.build_selected_deck(
        template,
        output,
        selected_slide_filenames=["slide2.xml"],
    )

    validate_commands = [
        command for command in runner.commands if Path(command[1]).name == "validate.py"
    ]
    assert result.repaired is True
    assert runner.count("pack.py") == 2
    assert len(validate_commands) == 3
    assert "--auto-repair" not in validate_commands[0]
    assert "--auto-repair" in validate_commands[1]
    assert "--auto-repair" not in validate_commands[2]
    assert _slides_in_pptx(output) == ["slide2.xml"]


def test_repair_failure_raises_after_revalidation(
    tmp_path: Path,
    skill_dir: Path,
):
    template = tmp_path / "template.pptx"
    output = tmp_path / "selected.pptx"
    _make_template_pptx(template, slide_count=1)

    runner = FakePptxSkillRunner(validate_returncodes=[1, 1])
    toolchain = PptxToolchain(skill_dir, runner=runner)
    before_tmp_dirs = _toolchain_tmp_dirs()

    with pytest.raises(PptxToolchainError, match="repair 이후에도 실패"):
        toolchain.build_selected_deck(template, output, selected_slide_filenames=["slide1.xml"])

    assert runner.count("pack.py") == 2
    assert _toolchain_tmp_dirs() == before_tmp_dirs


def test_auto_repair_command_failure_raises_before_repack(
    tmp_path: Path,
    skill_dir: Path,
):
    template = tmp_path / "template.pptx"
    output = tmp_path / "selected.pptx"
    _make_template_pptx(template, slide_count=1)

    runner = FakePptxSkillRunner(validate_returncodes=[1], repair_returncode=1)
    toolchain = PptxToolchain(skill_dir, runner=runner)

    with pytest.raises(PptxToolchainError, match="auto-repair 실행이 실패"):
        toolchain.build_selected_deck(template, output, selected_slide_filenames=["slide1.xml"])

    assert runner.count("pack.py") == 1


def test_selected_slide_must_exist(tmp_path: Path, skill_dir: Path):
    template = tmp_path / "template.pptx"
    output = tmp_path / "selected.pptx"
    _make_template_pptx(template, slide_count=1)

    toolchain = PptxToolchain(skill_dir, runner=FakePptxSkillRunner())

    with pytest.raises(ValueError, match="선택 슬라이드를 찾을 수 없습니다"):
        toolchain.build_selected_deck(template, output, selected_slide_filenames=["slide9.xml"])


def test_skill_scripts_must_be_available(tmp_path: Path):
    toolchain = PptxToolchain(tmp_path / "missing-skill", runner=FakePptxSkillRunner())

    with pytest.raises(PptxToolchainError, match="스크립트를 찾을 수 없습니다"):
        toolchain.ensure_available()


def _make_template_pptx(path: Path, *, slide_count: int) -> None:
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
    for index in range(1, slide_count + 1):
        entries[f"ppt/slides/slide{index}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<p:sld xmlns:p="{_PRESENTATION_NS}">'
            f'<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="{index}" name=""/>'
            "</p:nvGrpSpPr></p:spTree></p:cSld></p:sld>"
        )
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


def _unzip(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source, "r") as zf:
        zf.extractall(destination)


def _zip_dir(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(path for path in source.rglob("*") if path.is_file()):
            zf.write(file_path, file_path.relative_to(source))


def _clean_unreferenced_slides(unpacked_dir: Path) -> None:
    referenced_slides = set(_slides_in_unpacked_presentation(unpacked_dir))
    slides_dir = unpacked_dir / "ppt" / "slides"
    for slide_file in slides_dir.glob("slide*.xml"):
        if slide_file.name in referenced_slides:
            continue
        slide_file.unlink()
        rels_file = slides_dir / "_rels" / f"{slide_file.name}.rels"
        if rels_file.exists():
            rels_file.unlink()

    rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"
    rels_dom = defusedxml.minidom.parse(str(rels_path))
    for rel in list(rels_dom.getElementsByTagNameNS(_PACKAGE_RELATIONSHIPS_NS, "Relationship")):
        target = rel.getAttribute("Target")
        if target.startswith("slides/") and target.removeprefix("slides/") not in referenced_slides:
            rel.parentNode.removeChild(rel)
            rel.unlink()
    rels_path.write_bytes(rels_dom.toxml(encoding="UTF-8"))

    content_types_path = unpacked_dir / "[Content_Types].xml"
    content_types_dom = defusedxml.minidom.parse(str(content_types_path))
    for override in list(content_types_dom.getElementsByTagName("Override")):
        part_name = override.getAttribute("PartName")
        if not part_name.startswith("/ppt/slides/"):
            continue
        if Path(part_name).name not in referenced_slides:
            override.parentNode.removeChild(override)
            override.unlink()
    content_types_path.write_bytes(content_types_dom.toxml(encoding="UTF-8"))


def _slides_in_pptx(pptx_path: Path) -> list[str]:
    with zipfile.ZipFile(pptx_path, "r") as zf:
        with tempfile.TemporaryDirectory(
            prefix="folioo-pptx-test-extract-",
        ) as temp_dir:
            unpacked = Path(temp_dir)
            zf.extractall(unpacked)
            return _slides_in_unpacked_presentation(unpacked)


def _slides_in_unpacked_presentation(unpacked_dir: Path) -> list[str]:
    presentation_path = unpacked_dir / "ppt" / "presentation.xml"
    rels_path = unpacked_dir / "ppt" / "_rels" / "presentation.xml.rels"
    rels_dom = defusedxml.minidom.parse(str(rels_path))
    rid_to_slide = {
        rel.getAttribute("Id"): rel.getAttribute("Target").removeprefix("slides/")
        for rel in rels_dom.getElementsByTagNameNS(_PACKAGE_RELATIONSHIPS_NS, "Relationship")
        if rel.getAttribute("Type") == _SLIDE_RELATIONSHIP_TYPE
    }
    presentation_dom = defusedxml.minidom.parse(str(presentation_path))
    slide_ids = presentation_dom.getElementsByTagNameNS(_PRESENTATION_NS, "sldId")
    return [
        rid_to_slide[slide_id.getAttributeNS(_RELATIONSHIPS_NS, "id")] for slide_id in slide_ids
    ]


def _toolchain_tmp_dirs() -> set[Path]:
    return set(Path("/tmp").glob("folioo-pptx-toolchain-*"))


def _completed(
    command: list[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)
