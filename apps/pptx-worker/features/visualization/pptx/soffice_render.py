"""LibreOffice/Poppler 기반 PPTX 렌더 래퍼."""

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Protocol

from pptx_worker.metrics import (
    WORKER_TMP_ROOT,
    FailureReason,
    WorkerMetricsRegistry,
    get_worker_metrics,
    safe_directory_size,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE = 20
_PPTX_SUFFIX = ".pptx"
_PDF_SUFFIX = ".pdf"
_JPG_SUFFIX = ".jpg"
_FONT_FALLBACK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfont\b.*\bfallback\b", re.IGNORECASE),
    re.compile(r"\bfallback\b.*\bfont\b", re.IGNORECASE),
    re.compile(r"\bfont\b.*\bsubstitut", re.IGNORECASE),
    re.compile(r"\bsubstitut.*\bfont\b", re.IGNORECASE),
    re.compile(r"\bmissing\b.*\bfont\b", re.IGNORECASE),
    re.compile(r"\bfont\b.*\bnot found\b", re.IGNORECASE),
)


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
    tmp_root: Path = WORKER_TMP_ROOT


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
        metrics: WorkerMetricsRegistry | None = None,
    ) -> None:
        self._options = options or RenderOptions()
        self._counter = counter or InMemoryConversionCounter()
        self._metrics = metrics or get_worker_metrics()
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
        soffice_rss_bytes: int | None = None
        soffice_duration_seconds = 0.0
        source_path = self._validate_pptx_path(Path(pptx_path))
        final_output_dir = Path(output_dir)
        self._validate_page(page)
        final_output_dir.mkdir(parents=True, exist_ok=True)

        try:
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

                (
                    temp_pdf,
                    attempts,
                    soffice_rss_bytes,
                    soffice_duration_seconds,
                ) = self._convert_pptx_to_pdf(input_pptx, pdf_dir, workdir)
                temp_images = self._render_pdf_to_jpg(temp_pdf, image_dir, page)

                final_pdf = final_output_dir / f"{source_path.stem}{_PDF_SUFFIX}"
                self._clean_owned_outputs(final_output_dir, final_pdf)
                shutil.copy2(temp_pdf, final_pdf)
                slides = self._copy_images(temp_images, final_output_dir)

            conversion_count = self._counter.increment()
            self._metrics.observe_soffice_conversion_success(
                duration_seconds=soffice_duration_seconds,
                rss_bytes=soffice_rss_bytes,
            )
            return RenderResult(
                pdf_path=final_pdf,
                slides=tuple(slides),
                soffice_attempts=attempts,
                conversion_count=conversion_count,
            )
        except Exception as exc:
            failure_reason = _classify_render_exception(exc)
            if failure_reason is not None:
                self._metrics.record_soffice_conversion_failure(failure_reason)
            raise
        finally:
            self._metrics.set_tmp_disk_bytes_used(safe_directory_size(self._options.tmp_root))

    def _convert_pptx_to_pdf(
        self,
        input_pptx: Path,
        pdf_dir: Path,
        workdir: Path,
    ) -> tuple[Path, int, int | None, float]:
        started_at = time.perf_counter()
        attempts = 0
        last_error: PptxRenderError | None = None
        rss_bytes: int | None = None
        for attempt in range(1, 3):
            attempts = attempt
            profile_dir = workdir / f"soffice-profile-{uuid.uuid4().hex}"
            profile_dir.mkdir()
            command = self._soffice_command(input_pptx, pdf_dir, profile_dir)
            try:
                run_result = self._run_command(command, "soffice")
                rss_bytes = _max_optional_int(rss_bytes, run_result.rss_bytes)
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
            return pdf_path, attempts, rss_bytes, time.perf_counter() - started_at

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

    def _run_command(self, command: Sequence[str], command_name: str) -> "_CommandRunResult":
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **self._process_group_kwargs(),
        )
        rss_sampler = _ProcessRssSampler(process.pid)
        rss_sampler.start()
        try:
            stdout, stderr = process.communicate(timeout=self._options.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._kill_process_group(process)
            stdout, stderr = process.communicate()
            rss_bytes = _rss_with_fallback(rss_sampler)
            raise _CommandTimeoutError(
                command_name=command_name,
                command=command,
                stdout=stdout,
                stderr=stderr,
                rss_bytes=rss_bytes,
            ) from exc

        rss_bytes = _rss_with_fallback(rss_sampler)
        if command_name == "soffice":
            fallback_count = count_font_fallback_warnings(stdout, stderr)
            self._metrics.record_font_fallback_warnings(fallback_count)

        if process.returncode != 0:
            raise _CommandFailedError(
                command_name=command_name,
                command=command,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                rss_bytes=rss_bytes,
            )
        return _CommandRunResult(
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
            rss_bytes=rss_bytes,
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
    rss_bytes: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", (str(self),))

    def __str__(self) -> str:
        return f"{self.command_name} 명령이 타임아웃되었습니다."


@dataclass(frozen=True)
class _CommandRunResult:
    """외부 명령 실행 결과."""

    stdout: str | None
    stderr: str | None
    returncode: int
    rss_bytes: int | None


@dataclass(frozen=True)
class _CommandFailedError(PptxRenderError):
    """외부 명령 non-zero 종료."""

    command_name: str
    command: Sequence[str]
    returncode: int
    stdout: str | None
    stderr: str | None
    rss_bytes: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", (str(self),))

    @property
    def is_oom(self) -> bool:
        """OOM kill 로 추정되는 종료인지 반환한다."""
        stderr = (self.stderr or "").lower()
        stdout = (self.stdout or "").lower()
        logs = f"{stdout}\n{stderr}"
        return (
            self.returncode in {-signal.SIGKILL, 128 + signal.SIGKILL}
            or "out of memory" in logs
            or "oom" in logs
        )

    def __str__(self) -> str:
        return (
            f"{self.command_name} 명령이 실패했습니다. "
            f"(exit={self.returncode}, stderr={(self.stderr or '').strip()!r})"
        )


def _classify_render_exception(exc: BaseException) -> FailureReason | None:
    """렌더링 단계에서 메트릭으로 집계할 실패 reason 을 반환한다."""
    if isinstance(exc, PptxRenderError):
        return _classify_render_failure(exc)
    if isinstance(exc, MemoryError):
        return "oom"
    if isinstance(exc, OSError):
        return "other"
    return None


def _classify_render_failure(exc: BaseException) -> FailureReason:
    """렌더링 예외 체인을 timeout/oom/other 로 분류한다."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, _CommandTimeoutError):
            return "timeout"
        if isinstance(current, _CommandFailedError) and current.is_oom:
            return "oom"
        current = current.__cause__
    return "other"


def count_font_fallback_warnings(*logs: str | None) -> int:
    """LibreOffice 로그에서 폰트 fallback/substitution 경고 수를 계산한다."""
    count = 0
    for log in logs:
        if not log:
            continue
        for line in log.splitlines():
            if any(pattern.search(line) for pattern in _FONT_FALLBACK_PATTERNS):
                count += 1
    return count


def _max_optional_int(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    if current is None:
        return value
    return max(current, value)


def _max_child_rss_bytes() -> int | None:
    """종료된 모든 child process 의 ru_maxrss 를 fallback 용도로 반환한다.

    Linux 에서는 기본적으로 /proc/<pid>/status 샘플링을 사용한다. 이 함수는
    /proc 을 사용할 수 없는 환경에서만 쓰는 best-effort fallback 이며,
    단일 child 가 아니라 현재 프로세스 생애 전체 child peak 일 수 있다.
    """
    try:
        import resource
    except ImportError:
        return None

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    max_rss = int(usage.ru_maxrss)
    if max_rss < 0:
        return None
    if sys.platform == "darwin":
        return max_rss
    return max_rss * 1024


def _rss_with_fallback(sampler: "_ProcessRssSampler") -> int | None:
    rss_bytes = sampler.stop()
    if rss_bytes is not None:
        return rss_bytes
    return _max_child_rss_bytes()


class _ProcessRssSampler:
    """Linux /proc status 에서 단일 child process 의 peak RSS 를 샘플링한다."""

    def __init__(self, pid: int, *, interval_seconds: float = 0.01) -> None:
        self._status_path = Path(f"/proc/{pid}/status")
        self._interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._peak_bytes: int | None = None
        self._lock = Lock()

    def start(self) -> None:
        """샘플링 스레드를 시작한다. /proc 이 없으면 no-op."""
        if not self._status_path.is_file():
            return
        self._sample_once()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        """샘플링을 멈추고 관측된 peak RSS 를 반환한다."""
        self._sample_once()
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None
        with self._lock:
            return self._peak_bytes

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self._sample_once()

    def _sample_once(self) -> None:
        rss_bytes = _read_linux_proc_status_peak_rss_bytes(self._status_path)
        if rss_bytes is None:
            return
        with self._lock:
            if self._peak_bytes is None or rss_bytes > self._peak_bytes:
                self._peak_bytes = rss_bytes


def _read_linux_proc_status_peak_rss_bytes(status_path: Path) -> int | None:
    """Linux /proc/<pid>/status 에서 VmHWM 또는 VmRSS 를 bytes 로 읽는다."""
    try:
        lines = status_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    fallback_rss: int | None = None
    for line in lines:
        if line.startswith("VmHWM:"):
            return _parse_proc_status_kb_line(line)
        if line.startswith("VmRSS:"):
            fallback_rss = _parse_proc_status_kb_line(line)
    return fallback_rss


def _parse_proc_status_kb_line(line: str) -> int | None:
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]) * 1024
    except ValueError:
        return None
