"""soffice 렌더 래퍼 테스트."""

import signal
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from unittest.mock import patch

import pytest
from pptx_worker.metrics import WorkerMetricsRegistry

from features.visualization.pptx.soffice_render import (
    InMemoryConversionCounter,
    PptxRenderer,
    PptxRenderError,
    RenderOptions,
    should_recycle_worker,
)


class FakeProcess:
    """Popen 테스트 더블."""

    def __init__(
        self,
        command: list[str],
        factory: "FakePopenFactory",
        *,
        timeout_once: bool = False,
        failure_returncode: int | None = None,
    ) -> None:
        self.command = command
        self.factory = factory
        self.timeout_once = timeout_once
        self.failure_returncode = failure_returncode
        self.killed = False
        self.pid = 10_000_000 + len(factory.processes)
        self.returncode = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if self.timeout_once and not self.killed:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if self.killed:
            self.returncode = -9
            return "", "timeout"
        if self.failure_returncode is not None:
            self.returncode = self.failure_returncode
            stdout, stderr = self.factory.output_for(self.command[0])
            return stdout, stderr or f"{self.command[0]} failed"
        self.factory.complete_command(self.command)
        return self.factory.output_for(self.command[0])

    def kill(self) -> None:
        self.killed = True


class FakePopenFactory:
    """soffice/pdftoppm 산출물을 만드는 Popen factory."""

    def __init__(
        self,
        *,
        full_pages: list[int] | None = None,
        timeout_soffice_attempts: int = 0,
        failing_commands: set[str] | dict[str, int] | None = None,
        command_outputs: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self.full_pages = [1, 2, 3] if full_pages is None else full_pages
        self.timeout_soffice_attempts = timeout_soffice_attempts
        self.failing_commands = self._normalize_failing_commands(failing_commands)
        self.command_outputs = command_outputs or {}
        self.commands: list[list[str]] = []
        self.popen_kwargs: list[dict] = []
        self.processes: list[FakeProcess] = []
        self._lock = Lock()

    def __call__(
        self,
        command: list[str],
        stdout: int,
        stderr: int,
        text: bool,
        **kwargs,
    ) -> FakeProcess:
        del stdout, stderr, text
        with self._lock:
            timeout_once = command[0] == "soffice" and self.timeout_soffice_attempts > 0
            if timeout_once:
                self.timeout_soffice_attempts -= 1
            process = FakeProcess(
                command,
                self,
                timeout_once=timeout_once,
                failure_returncode=self.failing_commands.get(command[0]),
            )
            self.commands.append(command)
            self.popen_kwargs.append(kwargs)
            self.processes.append(process)
            return process

    def complete_command(self, command: list[str]) -> None:
        if command[0] == "soffice":
            self._create_pdf(command)
        elif command[0] == "pdftoppm":
            self._create_images(command)
        else:
            raise AssertionError(f"알 수 없는 명령입니다: {command}")

    def output_for(self, command_name: str) -> tuple[str, str]:
        return self.command_outputs.get(command_name, ("", ""))

    @staticmethod
    def _normalize_failing_commands(
        failing_commands: set[str] | dict[str, int] | None,
    ) -> dict[str, int]:
        if failing_commands is None:
            return {}
        if isinstance(failing_commands, dict):
            return dict(failing_commands)
        return {command: 1 for command in failing_commands}

    @staticmethod
    def _create_pdf(command: list[str]) -> None:
        outdir = Path(command[command.index("--outdir") + 1])
        pptx_path = Path(command[-1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{pptx_path.stem}.pdf").write_bytes(b"%PDF-1.4")

    def _create_images(self, command: list[str]) -> None:
        prefix = Path(command[-1])
        if "-f" in command:
            first = int(command[command.index("-f") + 1])
            last = int(command[command.index("-l") + 1])
            pages = list(range(first, last + 1))
        else:
            pages = self.full_pages
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for page in pages:
            (prefix.parent / f"{prefix.name}-{page}.jpg").write_bytes(b"jpg")

    def commands_for(self, executable: str) -> list[list[str]]:
        return [command for command in self.commands if command[0] == executable]


@pytest.fixture()
def source_pptx(tmp_path: Path) -> Path:
    """테스트용 PPTX 파일."""
    pptx_path = tmp_path / "deck.pptx"
    pptx_path.write_bytes(b"pptx")
    return pptx_path


@pytest.fixture()
def tmp_root(tmp_path: Path) -> Path:
    """렌더러 내부 임시 루트."""
    path = tmp_path / "render-tmp"
    path.mkdir()
    return path


def _make_renderer(
    tmp_root: Path,
    counter: InMemoryConversionCounter | None = None,
    metrics: WorkerMetricsRegistry | None = None,
) -> PptxRenderer:
    """테스트용 렌더러를 만든다."""
    return PptxRenderer(
        options=RenderOptions(timeout_seconds=0.1, tmp_root=tmp_root),
        counter=counter,
        metrics=metrics or WorkerMetricsRegistry(),
    )


def _user_installation_arg(command: list[str]) -> str:
    """soffice 명령의 UserInstallation 인자를 반환한다."""
    return next(arg for arg in command if arg.startswith("-env:UserInstallation="))


def _assert_tmp_root_empty(tmp_root: Path) -> None:
    """렌더러 내부 작업 디렉터리가 모두 제거됐는지 확인한다."""
    assert list(tmp_root.iterdir()) == []


def test_full_render_outputs_all_pages_and_omits_page_flags(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """전체 모드는 -f/-l 없이 PDF 와 N장의 JPG 를 산출한다."""
    factory = FakePopenFactory(full_pages=[1, 2, 3])

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        result = _make_renderer(tmp_root).render(source_pptx, tmp_path / "out")

    assert result.pdf_path == tmp_path / "out" / "deck.pdf"
    assert result.pdf_path.read_bytes() == b"%PDF-1.4"
    assert [slide.page for slide in result.slides] == [1, 2, 3]
    assert [path.name for path in result.image_paths] == [
        "slide-01.jpg",
        "slide-02.jpg",
        "slide-03.jpg",
    ]
    pdftoppm_command = factory.commands_for("pdftoppm")[0]
    assert "-f" not in pdftoppm_command
    assert "-l" not in pdftoppm_command
    assert result.conversion_count == 1
    assert factory.popen_kwargs[0]["start_new_session"] is True
    _assert_tmp_root_empty(tmp_root)


def test_successful_render_updates_duration_rss_and_tmp_metrics(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """정상 변환은 duration 분위수, RSS, tmp 사용량 gauge 를 갱신한다."""
    metrics = WorkerMetricsRegistry()
    factory = FakePopenFactory(full_pages=[1])
    leftover = tmp_root / "leftover.bin"
    leftover.write_bytes(b"tmp")

    with (
        patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory),
        patch(
            "features.visualization.pptx.soffice_render.time.perf_counter",
            side_effect=[10.0, 12.5],
        ),
        patch("features.visualization.pptx.soffice_render._max_child_rss_bytes", return_value=512),
    ):
        _make_renderer(tmp_root, metrics=metrics).render(source_pptx, tmp_path / "out")

    snapshot = metrics.snapshot()
    assert snapshot.quantile(0.5) == 2.5
    assert snapshot.quantile(0.95) == 2.5
    assert snapshot.quantile(0.99) == 2.5
    assert snapshot.soffice_rss_bytes == 512
    assert snapshot.tmp_disk_bytes_used == 3
    leftover.unlink()
    _assert_tmp_root_empty(tmp_root)


def test_single_page_render_uses_f_l_and_outputs_one_jpg(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """단일 페이지 모드는 -f N -l N 으로 JPG 1장만 산출한다."""
    factory = FakePopenFactory()

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        result = _make_renderer(tmp_root).render(source_pptx, tmp_path / "out", page=2)

    assert [slide.page for slide in result.slides] == [2]
    assert [path.name for path in result.image_paths] == ["slide-02.jpg"]
    pdftoppm_command = factory.commands_for("pdftoppm")[0]
    assert pdftoppm_command[pdftoppm_command.index("-f") + 1] == "2"
    assert pdftoppm_command[pdftoppm_command.index("-l") + 1] == "2"
    _assert_tmp_root_empty(tmp_root)


def test_rerender_cleans_stale_owned_outputs(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """같은 output_dir 재렌더링 시 이전 PDF/JPG 산출물을 제거한다."""
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "deck.pdf").write_bytes(b"old pdf")
    (output_dir / "slide-01.jpg").write_bytes(b"old 1")
    (output_dir / "slide-03.jpg").write_bytes(b"old 3")
    (output_dir / "notes.txt").write_text("keep", encoding="utf-8")
    factory = FakePopenFactory()

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        result = _make_renderer(tmp_root).render(source_pptx, output_dir, page=2)

    assert [path.name for path in result.image_paths] == ["slide-02.jpg"]
    assert sorted(path.name for path in output_dir.iterdir()) == [
        "deck.pdf",
        "notes.txt",
        "slide-02.jpg",
    ]
    assert (output_dir / "notes.txt").read_text(encoding="utf-8") == "keep"
    _assert_tmp_root_empty(tmp_root)


def test_soffice_timeout_is_killed_and_retried_once(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """soffice timeout 은 SIGKILL 후 1회 재시도한다."""
    factory = FakePopenFactory(timeout_soffice_attempts=1)

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        result = _make_renderer(tmp_root).render(source_pptx, tmp_path / "out")

    soffice_processes = [
        process for process in factory.processes if process.command[0] == "soffice"
    ]
    soffice_commands = factory.commands_for("soffice")
    assert len(soffice_processes) == 2
    assert soffice_processes[0].killed is True
    assert result.soffice_attempts == 2
    assert _user_installation_arg(soffice_commands[0]) != _user_installation_arg(
        soffice_commands[1]
    )
    _assert_tmp_root_empty(tmp_root)


def test_timeout_kills_process_group_before_retry(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """timeout 시 개별 프로세스가 아니라 프로세스 그룹을 SIGKILL 한다."""
    factory = FakePopenFactory(timeout_soffice_attempts=1)

    def mark_process_killed(process_group_id: int, kill_signal: int) -> None:
        del process_group_id, kill_signal
        factory.processes[-1].killed = True

    with (
        patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory),
        patch("features.visualization.pptx.soffice_render.os.getpgid", return_value=1234),
        patch(
            "features.visualization.pptx.soffice_render.os.killpg",
            side_effect=mark_process_killed,
        ) as mock_killpg,
    ):
        _make_renderer(tmp_root).render(source_pptx, tmp_path / "out")

    mock_killpg.assert_called_once_with(1234, signal.SIGKILL)
    assert factory.popen_kwargs[0]["start_new_session"] is True
    _assert_tmp_root_empty(tmp_root)


def test_soffice_timeout_cleans_workdir_when_retry_fails(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """재시도까지 timeout 이면 예외를 내고 내부 /tmp 작업 디렉터리를 제거한다."""
    counter = InMemoryConversionCounter()
    metrics = WorkerMetricsRegistry()
    factory = FakePopenFactory(timeout_soffice_attempts=2)

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        with pytest.raises(PptxRenderError, match="초과") as exc_info:
            _make_renderer(tmp_root, counter, metrics).render(source_pptx, tmp_path / "out")

    soffice_processes = [
        process for process in factory.processes if process.command[0] == "soffice"
    ]
    assert [process.killed for process in soffice_processes] == [True, True]
    assert exc_info.value.__cause__ is not None
    assert exc_info.value.__cause__.args
    assert counter.value == 0
    assert metrics.snapshot().soffice_conversion_failures_total["timeout"] == 1
    _assert_tmp_root_empty(tmp_root)


@pytest.mark.parametrize("command_name", ["soffice", "pdftoppm"])
def test_non_zero_exit_raises_render_error(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
    command_name: str,
) -> None:
    """외부 명령이 non-zero exit 이면 렌더 실패로 처리한다."""
    counter = InMemoryConversionCounter()
    metrics = WorkerMetricsRegistry()
    factory = FakePopenFactory(failing_commands={command_name})

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        with pytest.raises(PptxRenderError, match=f"{command_name} 명령이 실패") as exc_info:
            _make_renderer(tmp_root, counter, metrics).render(source_pptx, tmp_path / "out")

    assert counter.value == 0
    assert exc_info.value.args
    assert metrics.snapshot().soffice_conversion_failures_total["other"] == 1
    _assert_tmp_root_empty(tmp_root)


def test_soffice_sigkill_failure_is_counted_as_oom(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """timeout 없이 SIGKILL 된 soffice 실패는 OOM reason 으로 분류한다."""
    metrics = WorkerMetricsRegistry()
    factory = FakePopenFactory(failing_commands={"soffice": -signal.SIGKILL})

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        with pytest.raises(PptxRenderError, match="soffice 명령이 실패"):
            _make_renderer(tmp_root, metrics=metrics).render(source_pptx, tmp_path / "out")

    snapshot = metrics.snapshot()
    assert snapshot.soffice_conversion_failures_total["oom"] == 1
    assert snapshot.worker_oom_kill_total == 1
    _assert_tmp_root_empty(tmp_root)


def test_font_fallback_warning_in_soffice_logs_increments_counter(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """soffice 로그의 font fallback 경고는 별도 카운터로 집계한다."""
    metrics = WorkerMetricsRegistry()
    factory = FakePopenFactory(
        full_pages=[1],
        command_outputs={
            "soffice": (
                "",
                "warn: font fallback requested for MissingDisplay\ninfo: font substitution applied",
            )
        },
    )

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        _make_renderer(tmp_root, metrics=metrics).render(source_pptx, tmp_path / "out")

    assert metrics.snapshot().font_fallback_warnings_total == 2
    _assert_tmp_root_empty(tmp_root)


def test_empty_pdftoppm_output_raises_render_error(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """pdftoppm 이 성공했지만 JPG를 만들지 않으면 실패로 처리한다."""
    counter = InMemoryConversionCounter()
    factory = FakePopenFactory(full_pages=[])

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        with pytest.raises(PptxRenderError, match="JPG 산출물"):
            _make_renderer(tmp_root, counter).render(source_pptx, tmp_path / "out")

    assert counter.value == 0
    _assert_tmp_root_empty(tmp_root)


def test_concurrent_renders_use_distinct_user_installations_and_increment_counter(
    source_pptx: Path,
    tmp_root: Path,
    tmp_path: Path,
) -> None:
    """동시 렌더링도 UserInstallation 충돌 없이 독립 프로필을 사용한다."""
    counter = InMemoryConversionCounter()
    renderer = _make_renderer(tmp_root, counter)
    factory = FakePopenFactory()

    def render_one(index: int) -> int:
        result = renderer.render(source_pptx, tmp_path / f"out-{index}")
        return result.conversion_count

    with patch("features.visualization.pptx.soffice_render.subprocess.Popen", factory):
        with ThreadPoolExecutor(max_workers=2) as executor:
            counts = list(executor.map(render_one, [1, 2]))

    soffice_commands = factory.commands_for("soffice")
    installations = {_user_installation_arg(command) for command in soffice_commands}
    assert sorted(counts) == [1, 2]
    assert len(soffice_commands) == 2
    assert len(installations) == 2
    assert counter.value == 2
    _assert_tmp_root_empty(tmp_root)


def test_conversion_counter_exposes_worker_recycle_threshold() -> None:
    """카운터는 워커 재활용 기준과 연동할 수 있다."""
    counter = InMemoryConversionCounter(initial_value=19)

    assert should_recycle_worker(counter) is False
    assert counter.increment() == 20
    assert should_recycle_worker(counter) is True

    with pytest.raises(ValueError, match="max_conversions"):
        should_recycle_worker(counter, max_conversions=0)
