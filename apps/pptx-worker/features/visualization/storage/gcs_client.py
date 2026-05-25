"""GCS 직접 R/W 클라이언트 — IAM 인증, signed URL 미경유"""

import logging
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from google.cloud import storage

logger = logging.getLogger(__name__)

BUCKET_NAME = "folioo-visualizations"

_PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


# ---------------------------------------------------------------------------
# Canonical key helpers (§9.1)
# ---------------------------------------------------------------------------


def preview_key(job_id: str, slide_order: int) -> str:
    return f"jobs/{job_id}/previews/slide-{slide_order:02d}.jpg"


def pptx_key(job_id: str) -> str:
    return f"jobs/{job_id}/current.pptx"


def pdf_key(job_id: str) -> str:
    return f"jobs/{job_id}/current.pdf"


def template_pptx_key(template_id: str) -> str:
    return f"templates/{template_id}/template.pptx"


# ---------------------------------------------------------------------------
# Client
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
    # Template (read-only)
    # ------------------------------------------------------------------

    def download_template(self, template_id: str, dest: Path) -> None:
        """templates/{template_id}/template.pptx → dest"""
        key = template_pptx_key(template_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    # ------------------------------------------------------------------
    # Job PPTX / PDF
    # ------------------------------------------------------------------

    def download_pptx(self, job_id: str, dest: Path) -> None:
        """jobs/{job_id}/current.pptx → dest"""
        key = pptx_key(job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)

    def upload_pptx(self, job_id: str, src: Path) -> str:
        """src → jobs/{job_id}/current.pptx  (returns GCS key)"""
        key = pptx_key(job_id)
        self._bucket.blob(key).upload_from_filename(str(src), content_type=_PPTX_CONTENT_TYPE)
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def upload_pdf(self, job_id: str, src: Path) -> str:
        """src → jobs/{job_id}/current.pdf  (returns GCS key)"""
        key = pdf_key(job_id)
        self._bucket.blob(key).upload_from_filename(str(src), content_type="application/pdf")
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def upload_preview(self, job_id: str, slide_order: int, src: Path) -> str:
        """src → jobs/{job_id}/previews/slide-NN.jpg  (returns GCS key)"""
        key = preview_key(job_id, slide_order)
        self._bucket.blob(key).upload_from_filename(str(src), content_type="image/jpeg")
        logger.debug("gcs upload %s <- %s", key, src)
        return key

    def download_preview(self, job_id: str, slide_order: int, dest: Path) -> None:
        """jobs/{job_id}/previews/slide-NN.jpg → dest"""
        key = preview_key(job_id, slide_order)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._bucket.blob(key).download_to_filename(str(dest))
        logger.debug("gcs download %s -> %s", key, dest)


# ---------------------------------------------------------------------------
# Temp-directory lifecycle
# ---------------------------------------------------------------------------


@contextmanager
def job_workdir(job_id: str) -> Generator[Path, None, None]:
    """작업용 /tmp 디렉터리를 만들고 종료 시 자동 삭제한다."""
    workdir = Path(tempfile.mkdtemp(prefix=f"job_{job_id}_", dir="/tmp"))
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        logger.debug("cleaned up workdir %s", workdir)
