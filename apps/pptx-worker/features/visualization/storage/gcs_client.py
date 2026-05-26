"""GCS 직접 R/W 클라이언트 — IAM 인증, signed URL 미경유"""

import logging
import re
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from google.cloud import storage

logger = logging.getLogger(__name__)

BUCKET_NAME = "folioo-visualizations"

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


# ---------------------------------------------------------------------------
# 클라이언트
# ---------------------------------------------------------------------------


class GcsClient:
    """IAM 직접 인증으로 GCS 버킷을 읽고 쓰는 클라이언트.

    컨테이너 실행 시 Workload Identity / ADC 로 자동 인증된다.
    signed URL 발급은 메인 백엔드 전담이므로 이 클라이언트에는 없다.
    """

    def __init__(self, bucket_name: str = BUCKET_NAME) -> None:
        self._storage_client = storage.Client()
        self._bucket = self._storage_client.bucket(bucket_name)

    # ------------------------------------------------------------------
    # 템플릿 (읽기 전용)
    # ------------------------------------------------------------------

    def download_template(self, template_id: str, dest: Path) -> None:
        """템플릿 PPTX를 GCS에서 로컬 경로로 다운로드한다."""
        key = template_pptx_key(template_id)
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

    def upload_pdf(self, job_id: str, src: Path) -> str:
        """로컬 PDF 파일을 GCS에 업로드하고 GCS 키를 반환한다."""
        key = pdf_key(job_id)
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

    def download_preview(self, job_id: str, slide_order: int, dest: Path) -> None:
        """슬라이드 프리뷰 JPG를 GCS에서 로컬 경로로 다운로드한다."""
        key = preview_key(job_id, slide_order)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)


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
    workdir = Path(tempfile.mkdtemp(prefix=f"job_{safe_job_id}_", dir="/tmp"))
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        logger.debug("cleaned up workdir %s", workdir)
