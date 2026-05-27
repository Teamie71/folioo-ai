"""Anthropic PPTX 스킬 기반 패키지 도구 체인 어댑터."""

import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.dom.minidom import Element

import defusedxml.minidom

ANTHROPIC_PPTX_SKILL_ENV = "ANTHROPIC_PPTX_SKILL_DIR"
_TMP_ROOT = Path("/tmp").resolve()
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SLIDE_RELATIONSHIP_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)

CommandRunner = Callable[[Sequence[str], Path | None], subprocess.CompletedProcess[str]]


class PptxToolchainError(RuntimeError):
    """PPTX 도구 체인 실행 실패."""


@dataclass(frozen=True)
class PptxToolchainResult:
    """선택 슬라이드 패키징 결과."""

    output_pptx: Path
    selected_slide_filenames: tuple[str, ...]
    remaining_slide_filenames: tuple[str, ...]
    repaired: bool


@dataclass(frozen=True)
class ValidationResult:
    """PPTX 검증 또는 repair 스크립트 실행 결과."""

    success: bool
    stdout: str
    stderr: str


class PptxToolchain:
    """
    Anthropic PPTX 스킬 스크립트를 래핑하는 패키지 수준 어댑터.

    모든 중간 파일은 `/tmp` 하위 임시 디렉터리에서 처리하고, 완료 또는 실패 시
    `TemporaryDirectory` 컨텍스트로 정리한다.
    """

    def __init__(
        self,
        skill_dir: str | Path,
        *,
        python_executable: str | Path | None = None,
        tmp_root: str | Path = _TMP_ROOT,
        runner: CommandRunner | None = None,
    ) -> None:
        self.skill_dir = Path(skill_dir)
        self.python_executable = str(python_executable or sys.executable)
        self.tmp_root = Path(tmp_root)
        self.runner = runner
        self._assert_tmp_root()

    @classmethod
    def from_env(
        cls,
        env_var: str = ANTHROPIC_PPTX_SKILL_ENV,
        *,
        python_executable: str | Path | None = None,
        tmp_root: str | Path = _TMP_ROOT,
        runner: CommandRunner | None = None,
    ) -> "PptxToolchain":
        """환경변수에 설정된 Anthropic PPTX 스킬 디렉터리로 어댑터 생성."""
        skill_dir = os.getenv(env_var)
        if not skill_dir:
            raise PptxToolchainError(f"{env_var} 환경변수가 설정되지 않았습니다.")
        return cls(
            skill_dir,
            python_executable=python_executable,
            tmp_root=tmp_root,
            runner=runner,
        )

    def ensure_available(self) -> None:
        """필수 Anthropic PPTX 스킬 스크립트가 있는지 확인."""
        missing = [
            str(path.relative_to(self.skill_dir))
            for path in self._required_scripts()
            if not path.is_file()
        ]
        if missing:
            joined = ", ".join(missing)
            raise PptxToolchainError(f"Anthropic PPTX 스킬 스크립트를 찾을 수 없습니다: {joined}")

    def build_selected_deck(
        self,
        template_pptx: str | Path,
        output_pptx: str | Path,
        selected_slide_filenames: Sequence[str],
    ) -> PptxToolchainResult:
        """
        템플릿 PPTX 에서 선택 슬라이드만 남긴 유효한 PPTX 를 생성.

        흐름: `/tmp` 작업 디렉터리 복사 → unpack → 미선택 sldId 제거 → clean →
        pack → validate. 첫 validate 실패 시 repair 후 pack/validate 를 다시 수행한다.
        """
        self.ensure_available()
        selected = tuple(_normalize_slide_filename(name) for name in selected_slide_filenames)
        if not selected:
            raise ValueError("선택된 슬라이드가 없습니다.")

        source = Path(template_pptx)
        if not source.is_file():
            raise FileNotFoundError(f"템플릿 PPTX 파일을 찾을 수 없습니다: {source}")

        destination = Path(output_pptx)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tmp_root()

        with tempfile.TemporaryDirectory(
            prefix="folioo-pptx-toolchain-",
            dir=self.tmp_root,
        ) as temp_dir:
            work_dir = Path(temp_dir)
            tmp_template = work_dir / "template.pptx"
            unpacked_dir = work_dir / "unpacked"
            packed_pptx = work_dir / "output.pptx"
            shutil.copy2(source, tmp_template)

            self.unpack(tmp_template, unpacked_dir)
            remaining = self.remove_unselected_slides(unpacked_dir, selected)
            self.clean(unpacked_dir)
            self.pack(unpacked_dir, packed_pptx, original_pptx=tmp_template)

            validation = self.validate(unpacked_dir, original_pptx=tmp_template)
            repaired = False
            if not validation.success:
                repaired = True
                repair_result = self.repair(unpacked_dir, original_pptx=tmp_template)
                if not repair_result.success:
                    raise PptxToolchainError(
                        "PPTX auto-repair 실행이 실패했습니다.\n"
                        f"stdout:\n{repair_result.stdout}\n"
                        f"stderr:\n{repair_result.stderr}"
                    )
                self.pack(unpacked_dir, packed_pptx, original_pptx=tmp_template)
                validation = self.validate(unpacked_dir, original_pptx=tmp_template)
                if not validation.success:
                    raise PptxToolchainError(
                        "PPTX 검증이 repair 이후에도 실패했습니다.\n"
                        f"stdout:\n{validation.stdout}\n"
                        f"stderr:\n{validation.stderr}"
                    )

            shutil.copy2(packed_pptx, destination)

        return PptxToolchainResult(
            output_pptx=destination,
            selected_slide_filenames=selected,
            remaining_slide_filenames=remaining,
            repaired=repaired,
        )

    def unpack(self, input_pptx: Path, output_dir: Path) -> None:
        """PPTX 패키지 해제."""
        self._run_checked(
            "unpack",
            [
                self.python_executable,
                str(self._unpack_script),
                str(input_pptx),
                str(output_dir),
            ],
        )

    def clean(self, unpacked_dir: Path) -> None:
        """미참조 PPTX 파트 정리."""
        self._run_checked(
            "clean",
            [
                self.python_executable,
                str(self._clean_script),
                str(unpacked_dir),
            ],
        )

    def pack(self, unpacked_dir: Path, output_pptx: Path, *, original_pptx: Path) -> None:
        """PPTX 패키징. 검증은 별도 validate 단계에서 수행한다."""
        self._run_checked(
            "pack",
            [
                self.python_executable,
                str(self._pack_script),
                str(unpacked_dir),
                str(output_pptx),
                "--original",
                str(original_pptx),
                "--validate",
                "false",
            ],
        )

    def validate(self, unpacked_dir: Path, *, original_pptx: Path) -> ValidationResult:
        """PPTX 스키마 검증."""
        completed = self._run_command(
            [
                self.python_executable,
                str(self._validate_script),
                str(unpacked_dir),
                "--original",
                str(original_pptx),
            ]
        )
        return ValidationResult(
            success=completed.returncode == 0,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def repair(self, unpacked_dir: Path, *, original_pptx: Path) -> ValidationResult:
        """검증기의 auto-repair 를 실행한다."""
        completed = self._run_command(
            [
                self.python_executable,
                str(self._validate_script),
                str(unpacked_dir),
                "--original",
                str(original_pptx),
                "--auto-repair",
            ]
        )
        return ValidationResult(
            success=completed.returncode == 0,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def remove_unselected_slides(
        self,
        unpacked_dir: str | Path,
        selected_slide_filenames: Sequence[str],
    ) -> tuple[str, ...]:
        """
        `ppt/presentation.xml` 의 `p:sldIdLst` 에서 미선택 슬라이드를 제거.

        실제 슬라이드 파트와 rels, Content_Types 정리는 뒤이어 실행되는 `clean`이 담당한다.
        남길 슬라이드는 `selected_slide_filenames` 순서대로 재배치한다.
        """
        selected_sequence = tuple(
            _normalize_slide_filename(name) for name in selected_slide_filenames
        )
        selected = set(selected_sequence)
        if not selected:
            raise ValueError("선택된 슬라이드가 없습니다.")
        if len(selected) != len(selected_sequence):
            raise ValueError("선택 슬라이드에 중복이 있습니다.")

        root_dir = Path(unpacked_dir)
        presentation_path = root_dir / "ppt" / "presentation.xml"
        rels_path = root_dir / "ppt" / "_rels" / "presentation.xml.rels"
        if not presentation_path.is_file():
            raise PptxToolchainError(
                f"presentation.xml 파일을 찾을 수 없습니다: {presentation_path}"
            )
        if not rels_path.is_file():
            raise PptxToolchainError(f"presentation.xml.rels 파일을 찾을 수 없습니다: {rels_path}")

        rid_to_slide = _load_slide_relationships(rels_path)
        dom = defusedxml.minidom.parse(str(presentation_path))
        slide_id_lists = dom.getElementsByTagNameNS(_PRESENTATION_NS, "sldIdLst")
        if not slide_id_lists:
            raise PptxToolchainError("presentation.xml 에 p:sldIdLst 가 없습니다.")

        slide_id_list = slide_id_lists[0]
        slide_ids = [
            node
            for node in slide_id_list.childNodes
            if node.nodeType == node.ELEMENT_NODE and node.localName == "sldId"
        ]
        available = {
            slide_filename
            for slide_id in slide_ids
            if (slide_filename := rid_to_slide.get(_relationship_id(slide_id)))
        }
        missing = sorted(selected - available)
        if missing:
            raise ValueError(f"선택 슬라이드를 찾을 수 없습니다: {', '.join(missing)}")

        slide_id_by_filename: dict[str, Element] = {}
        for slide_id in slide_ids:
            slide_filename = rid_to_slide.get(_relationship_id(slide_id))
            slide_id_list.removeChild(slide_id)
            if slide_filename in selected:
                slide_id_by_filename[slide_filename] = slide_id
            else:
                slide_id.unlink()

        for slide_filename in selected_sequence:
            slide_id_list.appendChild(slide_id_by_filename[slide_filename])

        presentation_path.write_bytes(dom.toxml(encoding="UTF-8"))
        return selected_sequence

    @property
    def _unpack_script(self) -> Path:
        return self.skill_dir / "scripts" / "office" / "unpack.py"

    @property
    def _clean_script(self) -> Path:
        return self.skill_dir / "scripts" / "clean.py"

    @property
    def _pack_script(self) -> Path:
        return self.skill_dir / "scripts" / "office" / "pack.py"

    @property
    def _validate_script(self) -> Path:
        return self.skill_dir / "scripts" / "office" / "validate.py"

    def _required_scripts(self) -> tuple[Path, Path, Path, Path]:
        return (
            self._unpack_script,
            self._clean_script,
            self._pack_script,
            self._validate_script,
        )

    def _assert_tmp_root(self) -> None:
        tmp_root = self.tmp_root.resolve()
        if not tmp_root.is_relative_to(_TMP_ROOT):
            raise ValueError("PPTX 도구 체인 작업 디렉터리는 /tmp 하위여야 합니다.")

    def _ensure_tmp_root(self) -> None:
        tmp_root = self.tmp_root.resolve()
        tmp_root.mkdir(parents=True, exist_ok=True)

    def _run_checked(self, command_name: str, command: Sequence[str]) -> None:
        completed = self._run_command(command)
        if completed.returncode == 0:
            return
        raise PptxToolchainError(
            f"PPTX {command_name} 명령이 실패했습니다. exit={completed.returncode}\n"
            f"stdout:\n{completed.stdout or ''}\n"
            f"stderr:\n{completed.stderr or ''}"
        )

    def _run_command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self.runner:
            return self.runner(command, self.skill_dir)
        return subprocess.run(
            list(command),
            cwd=self.skill_dir,
            text=True,
            capture_output=True,
            check=False,
        )


def _load_slide_relationships(rels_path: Path) -> dict[str, str]:
    dom = defusedxml.minidom.parse(str(rels_path))
    relationships: dict[str, str] = {}

    for rel in dom.getElementsByTagNameNS(_PACKAGE_RELATIONSHIPS_NS, "Relationship"):
        rel_type = rel.getAttribute("Type")
        target = rel.getAttribute("Target")
        if rel_type != _SLIDE_RELATIONSHIP_TYPE:
            continue
        slide_filename = _slide_filename_from_relationship_target(target)
        if slide_filename:
            relationships[rel.getAttribute("Id")] = slide_filename

    return relationships


def _relationship_id(node) -> str:
    return node.getAttributeNS(_RELATIONSHIPS_NS, "id") or node.getAttribute("r:id")


def _slide_filename_from_relationship_target(target: str) -> str | None:
    if not target:
        return None
    if target.startswith("/"):
        normalized = posixpath.normpath(target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join("ppt", target))
    prefix = "ppt/slides/"
    if not normalized.startswith(prefix):
        return None
    return _normalize_slide_filename(normalized.removeprefix(prefix))


def _normalize_slide_filename(slide_filename: str) -> str:
    normalized = posixpath.normpath(slide_filename.replace("\\", "/"))
    if normalized.startswith("/"):
        normalized = normalized.lstrip("/")
    if normalized.startswith("ppt/slides/"):
        normalized = normalized.removeprefix("ppt/slides/")
    if normalized.startswith("slides/"):
        normalized = normalized.removeprefix("slides/")
    return normalized
