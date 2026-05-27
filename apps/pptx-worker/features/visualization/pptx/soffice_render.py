"""LibreOffice/Poppler 기반 PPTX 렌더 래퍼."""

import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE = 20
_PPTX_SUFFIX = ".pptx"
_PDF_SUFFIX = ".pdf"
_JPG_SUFFIX = ".jpg"


class ConversionCounter(Protocol):
    """워커 재활용 로직과 공유할 누적 변환 카운터 인터페이스."""

    @property
    def value(self) -> int:
        """현재 누적 변환 횟수."""

    def increment(self) -> int:
        """변환 횟수를 1 증가시키고 증가 후 값을 반환한다."""


@dataclass
class InMemoryConversionCounter:
    """단일 워커 프로세스 안에서 쓰는 thread-safe 변환 카운터."""

    initial_value: int = 0
    _value: int = field(init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.initial_value < 0:
            raise ValueError("initial_value는 0 이상이어야 합니다.")
        self._value = self.initial_value

    @property
    def value(self) -> int:
        """현재 누적 변환 횟수."""
        with self._lock:
            return self._value

    def increment(self) -> int:
        """변환 횟수를 1 증가시키고 증가 후 값을 반환한다."""
        with self._lock:
            self._value += 1
            return self._value


def should_recycle_worker(
    counter: ConversionCounter,
    max_conversions: int = DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE,
) -> bool:
    """워커 인스턴스를 재활용해야 하는지 반환한다."""
    if max_conversions <= 0:
        raise ValueError("max_conversions는 1 이상이어야 합니다.")
    return counter.value >= max_conversions


@dataclass(frozen=True)
class RenderOptions:
    """PPTX 렌더링 옵션.

    timeout_seconds 는 soffice 와 pdftoppm 외부 명령에 공통으로 적용된다.
    """

    soffice_bin: str = "soffice"
    pdftoppm_bin: str = "pdftoppm"
    timeout_seconds: float = 60.0
    dpi: int = 150
    tmp_root: Path = Path("/tmp")


@dataclass(frozen=True)
class RenderedSlide:
    """렌더링된 단일 슬라이드 이미지."""

    page: int
    image_path: Path


@dataclass(frozen=True)
class RenderResult:
    """PPTX 렌더링 결과."""

    pdf_path: Path
    slides: tuple[RenderedSlide, ...]
    soffice_attempts: int
    conversion_count: int

    @property
    def image_paths(self) -> tuple[Path, ...]:
        """페이지 순서대로 정렬된 JPG 경로 목록."""
        return tuple(slide.image_path for slide in self.slides)


class PptxRenderError(RuntimeError):
    """PPTX 렌더링 실패."""


class PptxRenderer:
    """PPTX 를 PDF 와 페이지별 JPG 로 변환하는 무상태 렌더러."""

    def __init__(
        self,
        options: RenderOptions | None = None,
        counter: ConversionCounter | None = None,
    ) -> None:
        self._options = options or RenderOptions()
        self._counter = counter or InMemoryConversionCounter()
        self._validate_options(self._options)

    @property
    def counter(self) -> ConversionCounter:
        """누적 변환 카운터."""
        return self._counter

    def render(
        self,
        pptx_path: Path | str,
        output_dir: Path | str,
        *,
        page: int | None = None,
    ) -> RenderResult:
        """PPTX 를 PDF 와 JPG 프리뷰로 렌더링한다.

        Args:
            pptx_path: 입력 PPTX 경로.
            output_dir: 최종 PDF/JPG 를 저장할 디렉터리.
            page: 단일 페이지 렌더링 시 1부터 시작하는 페이지 번호.

        Returns:
            RenderResult: PDF 경로와 페이지별 JPG 경로.

        Raises:
            ValueError: 입력 경로, 옵션, 페이지 번호가 유효하지 않은 경우.
            PptxRenderError: 외부 변환 명령이 실패한 경우.
        """
        source_path = self._validate_pptx_path(Path(pptx_path))
        final_output_dir = Path(output_dir)
        self._validate_page(page)
        final_output_dir.mkdir(parents=True, exist_ok=True)

        workdir_obj = tempfile.TemporaryDirectory(
            prefix="folioo_render_",
            dir=str(self._options.tmp_root),
        )
        with workdir_obj as raw_workdir:
            workdir = Path(raw_workdir)
            input_pptx = workdir / "input.pptx"
            pdf_dir = workdir / "pdf"
            image_dir = workdir / "jpg"
            pdf_dir.mkdir()
            image_dir.mkdir()
            shutil.copy2(source_path, input_pptx)

            temp_pdf, attempts = self._convert_pptx_to_pdf(input_pptx, pdf_dir, workdir)
            temp_images = self._render_pdf_to_jpg(temp_pdf, image_dir, page)

            final_pdf = final_output_dir / f"{source_path.stem}{_PDF_SUFFIX}"
            self._clean_owned_outputs(final_output_dir, final_pdf)
            shutil.copy2(temp_pdf, final_pdf)
            slides = self._copy_images(temp_images, final_output_dir)

        conversion_count = self._counter.increment()
        return RenderResult(
            pdf_path=final_pdf,
            slides=tuple(slides),
            soffice_attempts=attempts,
            conversion_count=conversion_count,
        )

    def _convert_pptx_to_pdf(
        self,
        input_pptx: Path,
        pdf_dir: Path,
        workdir: Path,
    ) -> tuple[Path, int]:
        attempts = 0
        last_error: PptxRenderError | None = None
        for attempt in range(1, 3):
            attempts = attempt
            profile_dir = workdir / f"soffice-profile-{uuid.uuid4().hex}"
            profile_dir.mkdir()
            command = self._soffice_command(input_pptx, pdf_dir, profile_dir)
            try:
                self._run_command(command, "soffice")
            except _CommandTimeoutError as exc:
                last_error = PptxRenderError(
                    f"soffice 변환이 {self._options.timeout_seconds:g}초를 초과했습니다."
                )
                logger.warning("soffice timeout on attempt %s: %s", attempt, exc)
                if attempt == 2:
                    raise last_error from exc
                continue
            except PptxRenderError:
                raise
            finally:
                shutil.rmtree(profile_dir, ignore_errors=True)

            pdf_path = pdf_dir / f"{input_pptx.stem}{_PDF_SUFFIX}"
            if not pdf_path.is_file():
                raise PptxRenderError(f"soffice PDF 산출물을 찾을 수 없습니다: {pdf_path}")
            return pdf_path, attempts

        if last_error is not None:
            raise last_error
        raise PptxRenderError("soffice 변환에 실패했습니다.")

    def _render_pdf_to_jpg(
        self,
        pdf_path: Path,
        image_dir: Path,
        page: int | None,
    ) -> list[RenderedSlide]:
        output_prefix = image_dir / "slide"
        command = [
            self._options.pdftoppm_bin,
            "-jpeg",
            "-r",
            str(self._options.dpi),
        ]
        if page is not None:
            command.extend(["-f", str(page), "-l", str(page)])
        command.extend([str(pdf_path), str(output_prefix)])

        self._run_command(command, "pdftoppm")
        slides = self._collect_rendered_slides(image_dir, output_prefix.name)
        if page is not None and len(slides) != 1:
            raise PptxRenderError(
                f"단일 페이지 렌더링은 JPG 1장을 산출해야 합니다. (산출: {len(slides)}장)"
            )
        if not slides:
            raise PptxRenderError("pdftoppm JPG 산출물을 찾을 수 없습니다.")
        return slides

    def _soffice_command(self, input_pptx: Path, pdf_dir: Path, profile_dir: Path) -> list[str]:
        return [
            self._options.soffice_bin,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--nodefault",
            "--norestore",
            "--nocrashreport",
            "--nolockcheck",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "pdf:impress_pdf_Export",
            "--outdir",
            str(pdf_dir),
            str(input_pptx),
        ]

    def _run_command(self, command: Sequence[str], command_name: str) -> None:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **self._process_group_kwargs(),
        )
        try:
            stdout, stderr = process.communicate(timeout=self._options.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._kill_process_group(process)
            stdout, stderr = process.communicate()
            raise _CommandTimeoutError(
                command_name=command_name,
                command=command,
                stdout=stdout,
                stderr=stderr,
            ) from exc

        if process.returncode != 0:
            raise PptxRenderError(
                f"{command_name} 명령이 실패했습니다. "
                f"(exit={process.returncode}, stderr={stderr.strip()!r})"
            )

    def _clean_owned_outputs(self, output_dir: Path, pdf_path: Path) -> None:
        """이번 렌더러가 소유하는 이전 PDF/JPG 산출물을 제거한다."""
        pdf_path.unlink(missing_ok=True)
        for image_path in output_dir.glob(f"slide-*{_JPG_SUFFIX}"):
            if self._is_owned_image_name(image_path.name):
                image_path.unlink(missing_ok=True)

    def _copy_images(
        self,
        temp_slides: list[RenderedSlide],
        output_dir: Path,
    ) -> list[RenderedSlide]:
        final_slides: list[RenderedSlide] = []
        for slide in temp_slides:
            final_path = output_dir / f"slide-{slide.page:02d}{_JPG_SUFFIX}"
            shutil.copy2(slide.image_path, final_path)
            final_slides.append(RenderedSlide(page=slide.page, image_path=final_path))
        return final_slides

    @staticmethod
    def _collect_rendered_slides(image_dir: Path, prefix: str) -> list[RenderedSlide]:
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+){re.escape(_JPG_SUFFIX)}$")
        slides: list[RenderedSlide] = []
        for image_path in image_dir.glob(f"{prefix}-*{_JPG_SUFFIX}"):
            match = pattern.fullmatch(image_path.name)
            if match is None:
                continue
            slides.append(RenderedSlide(page=int(match.group(1)), image_path=image_path))
        return sorted(slides, key=lambda slide: slide.page)

    @staticmethod
    def _is_owned_image_name(name: str) -> bool:
        return re.fullmatch(rf"slide-\d+{re.escape(_JPG_SUFFIX)}", name) is not None

    @staticmethod
    def _process_group_kwargs() -> dict[str, bool | int]:
        if os.name == "posix":
            return {"start_new_session": True}
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    @staticmethod
    def _kill_process_group(process: subprocess.Popen) -> None:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                return
            except (OSError, OverflowError):
                logger.warning("failed to kill process group; falling back to process.kill()")

        try:
            process.kill()
        except OSError:
            pass

    @staticmethod
    def _validate_pptx_path(pptx_path: Path) -> Path:
        if pptx_path.suffix.lower() != _PPTX_SUFFIX:
            raise ValueError(f"PPTX 파일만 렌더링할 수 있습니다: {pptx_path}")
        if not pptx_path.is_file():
            raise ValueError(f"PPTX 파일을 찾을 수 없습니다: {pptx_path}")
        return pptx_path

    @staticmethod
    def _validate_page(page: int | None) -> None:
        if page is not None and page < 1:
            raise ValueError(f"page는 1 이상이어야 합니다. (받은 값: {page})")

    @staticmethod
    def _validate_options(options: RenderOptions) -> None:
        if options.timeout_seconds <= 0:
            raise ValueError("timeout_seconds는 0보다 커야 합니다.")
        if options.dpi <= 0:
            raise ValueError("dpi는 0보다 커야 합니다.")
        if not options.tmp_root.is_dir():
            raise ValueError(f"tmp_root 디렉터리를 찾을 수 없습니다: {options.tmp_root}")


@dataclass(frozen=True)
class _CommandTimeoutError(PptxRenderError):
    """외부 명령 타임아웃."""

    command_name: str
    command: Sequence[str]
    stdout: str | None
    stderr: str | None

    def __str__(self) -> str:
        return f"{self.command_name} 명령이 타임아웃되었습니다."
