"""GCS 직접 R/W 클라이언트 — IAM 인증, signed URL 미경유"""

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from google.cloud import storage
from pptx_worker.metrics import WORKER_TMP_ROOT, get_worker_metrics, safe_directory_size

logger = logging.getLogger(__name__)

DEFAULT_BUCKET_NAME = "folioo-visualizations"
BUCKET_NAME = os.getenv("GCS_BUCKET", DEFAULT_BUCKET_NAME)

_PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

# job_id / template_id 허용 패턴 — 슬래시·경로 트래버설·공백 차단
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_identifier(value: str, field_name: str) -> str:
    """식별자가 허용 패턴에 맞는지 검증한다.

    Args:
        value: 검증할 식별자 문자열.
        field_name: 에러 메시지에 사용할 필드명.

    Returns:
        검증을 통과한 식별자 문자열.

    Raises:
        ValueError: 식별자가 비어 있거나 허용 패턴(`[A-Za-z0-9_-]`)을 벗어난 경우.
    """
    if not value or not _ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name}는 영문자/숫자/_/- 만 사용할 수 있습니다. (받은 값: {value!r})"
        )
    return value


# ---------------------------------------------------------------------------
# canonical key 헬퍼 (§9.1)
# ---------------------------------------------------------------------------


def preview_key(job_id: str, slide_order: int) -> str:
    """프리뷰 이미지의 canonical GCS 키를 반환한다.

    Args:
        job_id: 시각화 작업 ID.
        slide_order: 슬라이드 순서 (1~99).

    Returns:
        ``jobs/{job_id}/previews/slide-NN.jpg`` 형식의 GCS 키.

    Raises:
        ValueError: job_id가 허용 패턴을 벗어나거나 slide_order가 범위를 초과한 경우.
    """
    _validate_identifier(job_id, "job_id")
    if not 1 <= slide_order <= 99:
        raise ValueError(f"slide_order는 1~99 범위여야 합니다. (받은 값: {slide_order})")
    return f"jobs/{job_id}/previews/slide-{slide_order:02d}.jpg"


def pptx_key(job_id: str) -> str:
    """현재 PPTX 파일의 canonical GCS 키를 반환한다.

    Args:
        job_id: 시각화 작업 ID.

    Returns:
        ``jobs/{job_id}/current.pptx`` 형식의 GCS 키.

    Raises:
        ValueError: job_id가 허용 패턴을 벗어난 경우.
    """
    _validate_identifier(job_id, "job_id")
    return f"jobs/{job_id}/current.pptx"


def pdf_key(job_id: str) -> str:
    """현재 PDF 파일의 canonical GCS 키를 반환한다.

    Args:
        job_id: 시각화 작업 ID.

    Returns:
        ``jobs/{job_id}/current.pdf`` 형식의 GCS 키.

    Raises:
        ValueError: job_id가 허용 패턴을 벗어난 경우.
    """
    _validate_identifier(job_id, "job_id")
    return f"jobs/{job_id}/current.pdf"


def regeneration_attempt_pptx_key(job_id: str, attempt_id: str) -> str:
    """재생성 attempt PPTX 파일의 staging GCS 키를 반환한다."""
    _validate_identifier(job_id, "job_id")
    _validate_identifier(attempt_id, "attempt_id")
    return f"jobs/{job_id}/attempts/{attempt_id}/current.pptx"


def regeneration_attempt_pdf_key(job_id: str, attempt_id: str) -> str:
    """재생성 attempt PDF 파일의 staging GCS 키를 반환한다."""
    _validate_identifier(job_id, "job_id")
    _validate_identifier(attempt_id, "attempt_id")
    return f"jobs/{job_id}/attempts/{attempt_id}/current.pdf"


def regeneration_attempt_preview_key(job_id: str, attempt_id: str, slide_order: int) -> str:
    """재생성 attempt 프리뷰 이미지의 staging GCS 키를 반환한다."""
    _validate_identifier(job_id, "job_id")
    _validate_identifier(attempt_id, "attempt_id")
    if not 1 <= slide_order <= 99:
        raise ValueError(f"slide_order는 1~99 범위여야 합니다. (받은 값: {slide_order})")
    return f"jobs/{job_id}/attempts/{attempt_id}/previews/slide-{slide_order:02d}.jpg"


@dataclass(frozen=True, slots=True)
class RegenerationArtifactKeys:
    """재생성 attempt 산출물과 canonical 산출물의 GCS key 묶음."""

    attempt_pptx_key: str
    attempt_pdf_key: str
    attempt_preview_key: str
    canonical_pptx_key: str
    canonical_pdf_key: str
    canonical_preview_key: str


def template_pptx_key(template_id: str) -> str:
    """템플릿 PPTX 파일의 canonical GCS 키를 반환한다.

    Args:
        template_id: 템플릿 ID (예: ``blue``, ``green``).

    Returns:
        ``templates/{template_id}/template.pptx`` 형식의 GCS 키.

    Raises:
        ValueError: template_id가 허용 패턴을 벗어난 경우.
    """
    _validate_identifier(template_id, "template_id")
    return f"templates/{template_id}/template.pptx"


def template_meta_key(template_id: str) -> str:
    """템플릿 meta.json 의 canonical GCS 키를 반환한다.

    Args:
        template_id: 템플릿 ID. 영문/숫자, `_`, `-` 만 허용한다.

    Returns:
        ``templates/{template_id}/meta.json`` 형식의 GCS 키.

    Raises:
        ValueError: template_id가 허용 패턴을 벗어난 경우.
    """
    _validate_identifier(template_id, "template_id")
    return f"templates/{template_id}/meta.json"


def template_thumbnail_key(template_id: str) -> str:
    """템플릿 썸네일 이미지의 canonical GCS 키를 반환한다.

    Args:
        template_id: 템플릿 ID. 영문/숫자, `_`, `-` 만 허용한다.

    Returns:
        ``templates/{template_id}/thumbnail.jpg`` 형식의 GCS 키.

    Raises:
        ValueError: template_id가 허용 패턴을 벗어난 경우.
    """
    _validate_identifier(template_id, "template_id")
    return f"templates/{template_id}/thumbnail.jpg"


# ---------------------------------------------------------------------------
# 클라이언트
# ---------------------------------------------------------------------------


class GcsClient:
    """IAM 직접 인증으로 GCS 버킷을 읽고 쓰는 클라이언트.

    컨테이너 실행 시 Workload Identity / ADC 로 자동 인증된다.
    signed URL 발급은 메인 백엔드 전담이므로 이 클라이언트에는 없다.
    """

    def __init__(self, bucket_name: str | None = None) -> None:
        self._storage_client = storage.Client()
        resolved_bucket_name = bucket_name or os.getenv("GCS_BUCKET", DEFAULT_BUCKET_NAME)
        self._bucket = self._storage_client.bucket(resolved_bucket_name)

    # ------------------------------------------------------------------
    # 템플릿 (읽기 전용)
    # ------------------------------------------------------------------

    def download_template(self, template_id: str, dest: Path) -> None:
        """템플릿 PPTX를 GCS에서 로컬 경로로 다운로드한다."""
        key = template_pptx_key(template_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    def download_template_meta(self, template_id: str, dest: Path) -> None:
        """템플릿 meta.json 을 GCS에서 로컬 경로로 다운로드한다.

        Args:
            template_id: 다운로드할 템플릿 ID. 영문/숫자, `_`, `-` 만 허용한다.
            dest: 다운로드 대상 로컬 파일 경로.

        Raises:
            ValueError: template_id가 허용 패턴을 벗어난 경우.
        """
        key = template_meta_key(template_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    def download_template_thumbnail(self, template_id: str, dest: Path) -> None:
        """템플릿 thumbnail.jpg 를 GCS에서 로컬 경로로 다운로드한다.

        Args:
            template_id: 다운로드할 템플릿 ID. 영문/숫자, `_`, `-` 만 허용한다.
            dest: 다운로드 대상 로컬 파일 경로.

        Raises:
            ValueError: template_id가 허용 패턴을 벗어난 경우.
        """
        key = template_thumbnail_key(template_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    # ------------------------------------------------------------------
    # 잡 PPTX / PDF
    # ------------------------------------------------------------------

    def download_pptx(self, job_id: str, dest: Path) -> None:
        """현재 PPTX를 GCS에서 로컬 경로로 다운로드한다."""
        key = pptx_key(job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    def upload_pptx(self, job_id: str, src: Path) -> str:
        """로컬 PPTX 파일을 GCS에 업로드하고 GCS 키를 반환한다."""
        key = pptx_key(job_id)
        self._bucket.blob(key).upload_from_filename(str(src), content_type=_PPTX_CONTENT_TYPE)
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def upload_regeneration_attempt_pptx(self, job_id: str, attempt_id: str, src: Path) -> str:
        """재생성 PPTX 산출물을 attempt key 로 업로드한다."""
        key = regeneration_attempt_pptx_key(job_id, attempt_id)
        self._bucket.blob(key).upload_from_filename(str(src), content_type=_PPTX_CONTENT_TYPE)
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def upload_pdf(self, job_id: str, src: Path) -> str:
        """로컬 PDF 파일을 GCS에 업로드하고 GCS 키를 반환한다."""
        key = pdf_key(job_id)
        self._bucket.blob(key).upload_from_filename(str(src), content_type="application/pdf")
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def upload_regeneration_attempt_pdf(self, job_id: str, attempt_id: str, src: Path) -> str:
        """재생성 PDF 산출물을 attempt key 로 업로드한다."""
        key = regeneration_attempt_pdf_key(job_id, attempt_id)
        self._bucket.blob(key).upload_from_filename(str(src), content_type="application/pdf")
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    # ------------------------------------------------------------------
    # 프리뷰
    # ------------------------------------------------------------------

    def upload_preview(self, job_id: str, slide_order: int, src: Path) -> str:
        """슬라이드 프리뷰 JPG를 GCS에 업로드하고 GCS 키를 반환한다."""
        key = preview_key(job_id, slide_order)
        self._bucket.blob(key).upload_from_filename(str(src), content_type="image/jpeg")
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def upload_regeneration_attempt_preview(
        self,
        job_id: str,
        attempt_id: str,
        slide_order: int,
        src: Path,
    ) -> str:
        """재생성 프리뷰 산출물을 attempt key 로 업로드한다."""
        key = regeneration_attempt_preview_key(job_id, attempt_id, slide_order)
        self._bucket.blob(key).upload_from_filename(str(src), content_type="image/jpeg")
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def download_preview(self, job_id: str, slide_order: int, dest: Path) -> None:
        """슬라이드 프리뷰 JPG를 GCS에서 로컬 경로로 다운로드한다."""
        key = preview_key(job_id, slide_order)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    def promote_regeneration_attempt(
        self,
        job_id: str,
        attempt_id: str,
        slide_order: int,
    ) -> RegenerationArtifactKeys:
        """재생성 attempt 산출물을 canonical key 로 promote 한다.

        Main Backend callback 이 성공한 뒤에만 호출되어야 한다. Promote 중 일부 copy 가
        실패하면 사전에 백업한 이전 canonical 객체를 가능한 범위에서 복원한다.
        """
        keys = RegenerationArtifactKeys(
            attempt_pptx_key=regeneration_attempt_pptx_key(job_id, attempt_id),
            attempt_pdf_key=regeneration_attempt_pdf_key(job_id, attempt_id),
            attempt_preview_key=regeneration_attempt_preview_key(job_id, attempt_id, slide_order),
            canonical_pptx_key=pptx_key(job_id),
            canonical_pdf_key=pdf_key(job_id),
            canonical_preview_key=preview_key(job_id, slide_order),
        )
        backups = [
            (
                keys.canonical_pptx_key,
                _regeneration_rollback_key(job_id, attempt_id, "current.pptx"),
            ),
            (
                keys.canonical_pdf_key,
                _regeneration_rollback_key(job_id, attempt_id, "current.pdf"),
            ),
            (
                keys.canonical_preview_key,
                _regeneration_rollback_key(job_id, attempt_id, f"slide-{slide_order:02d}.jpg"),
            ),
        ]
        promotions = [
            (keys.attempt_pptx_key, keys.canonical_pptx_key),
            (keys.attempt_pdf_key, keys.canonical_pdf_key),
            (keys.attempt_preview_key, keys.canonical_preview_key),
        ]

        backed_up: list[tuple[str, str]] = []
        try:
            for source_key, backup_key in backups:
                self._copy_blob(source_key, backup_key)
                backed_up.append((source_key, backup_key))
            for source_key, destination_key in promotions:
                self._copy_blob(source_key, destination_key)
        except Exception:
            logger.exception(
                "gcs regeneration promote failed; restoring previous canonical artifacts: "
                "job_id=%s attempt_id=%s slide_order=%s",
                job_id,
                attempt_id,
                slide_order,
            )
            for canonical_key, backup_key in reversed(backed_up):
                try:
                    self._copy_blob(backup_key, canonical_key)
                except Exception:
                    logger.exception(
                        "gcs regeneration rollback failed: canonical_key=%s backup_key=%s",
                        canonical_key,
                        backup_key,
                    )
            raise
        logger.info(
            "gcs regeneration attempt promoted: job_id=%s attempt_id=%s slide_order=%s",
            job_id,
            attempt_id,
            slide_order,
        )
        return keys

    def _copy_blob(self, source_key: str, destination_key: str) -> None:
        source_blob = self._bucket.blob(source_key)
        self._bucket.copy_blob(source_blob, self._bucket, destination_key)
        logger.debug("gcs copy %s -> %s", source_key, destination_key)


# ---------------------------------------------------------------------------
# 임시 디렉터리 생명주기
# ---------------------------------------------------------------------------


@contextmanager
def job_workdir(job_id: str) -> Generator[Path, None, None]:
    """작업용 /tmp 디렉터리를 만들고 종료 시 자동 삭제한다.

    Args:
        job_id: 시각화 작업 ID. 디렉터리 prefix에 사용된다.

    Yields:
        생성된 작업 디렉터리 경로.
    """
    safe_job_id = _validate_identifier(job_id, "job_id")
    WORKER_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"job_{safe_job_id}_", dir=str(WORKER_TMP_ROOT)))
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        get_worker_metrics().set_tmp_disk_bytes_used(safe_directory_size(WORKER_TMP_ROOT))
        logger.debug("cleaned up workdir %s", workdir)


def _regeneration_rollback_key(job_id: str, attempt_id: str, filename: str) -> str:
    _validate_identifier(job_id, "job_id")
    _validate_identifier(attempt_id, "attempt_id")
    if "/" in filename or not filename:
        raise ValueError(f"filename은 단일 파일명이어야 합니다. (받은 값: {filename!r})")
    return f"jobs/{job_id}/attempts/{attempt_id}/rollback/{filename}"
