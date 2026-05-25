"""GCS 클라이언트 단위 테스트"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from features.visualization.storage.gcs_client import (
    GcsClient,
    job_workdir,
    pdf_key,
    pptx_key,
    preview_key,
    template_pptx_key,
)


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


class TestCanonicalKeys:
    def test_preview_key_zero_padded(self):
        assert preview_key("job-1", 3) == "jobs/job-1/previews/slide-03.jpg"

    def test_preview_key_double_digit(self):
        assert preview_key("job-1", 12) == "jobs/job-1/previews/slide-12.jpg"

    def test_pptx_key(self):
        assert pptx_key("job-abc") == "jobs/job-abc/current.pptx"

    def test_pdf_key(self):
        assert pdf_key("job-abc") == "jobs/job-abc/current.pdf"

    def test_template_pptx_key(self):
        assert template_pptx_key("blue") == "templates/blue/template.pptx"


# ---------------------------------------------------------------------------
# GcsClient — GCS 호출 mock
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_gcs(tmp_path):
    """storage.Client 을 mock 으로 교체한 GcsClient 반환"""
    with patch("features.visualization.storage.gcs_client.storage.Client") as MockClient:
        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        MockClient.return_value.bucket.return_value = mock_bucket

        client = GcsClient(bucket_name="folioo-visualizations")
        yield client, mock_bucket, mock_blob, tmp_path


class TestDownloadTemplate:
    def test_calls_correct_key(self, mock_gcs):
        client, mock_bucket, mock_blob, tmp_path = mock_gcs
        dest = tmp_path / "template.pptx"

        client.download_template("blue", dest)

        mock_bucket.blob.assert_called_once_with("templates/blue/template.pptx")
        mock_blob.download_to_filename.assert_called_once_with(str(dest))

    def test_creates_parent_dirs(self, mock_gcs):
        client, _, _, tmp_path = mock_gcs
        dest = tmp_path / "nested" / "dir" / "template.pptx"

        client.download_template("green", dest)

        assert dest.parent.exists()


class TestDownloadPptx:
    def test_calls_correct_key(self, mock_gcs):
        client, mock_bucket, mock_blob, tmp_path = mock_gcs
        dest = tmp_path / "current.pptx"

        client.download_pptx("job-42", dest)

        mock_bucket.blob.assert_called_once_with("jobs/job-42/current.pptx")
        mock_blob.download_to_filename.assert_called_once_with(str(dest))


class TestUploadPptx:
    def test_returns_canonical_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "output.pptx"
        src.touch()

        key = client.upload_pptx("job-42", src)

        assert key == "jobs/job-42/current.pptx"
        mock_bucket.blob.assert_called_once_with("jobs/job-42/current.pptx")
        mock_blob.upload_from_filename.assert_called_once_with(
            str(src),
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )


class TestUploadPdf:
    def test_returns_canonical_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "output.pdf"
        src.touch()

        key = client.upload_pdf("job-42", src)

        assert key == "jobs/job-42/current.pdf"
        mock_blob.upload_from_filename.assert_called_once_with(
            str(src), content_type="application/pdf"
        )


class TestUploadPreview:
    def test_returns_canonical_key_zero_padded(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "slide.jpg"
        src.touch()

        key = client.upload_preview("job-42", 3, src)

        assert key == "jobs/job-42/previews/slide-03.jpg"
        mock_bucket.blob.assert_called_once_with("jobs/job-42/previews/slide-03.jpg")
        mock_blob.upload_from_filename.assert_called_once_with(
            str(src), content_type="image/jpeg"
        )

    def test_returns_canonical_key_double_digit(self, mock_gcs, tmp_path):
        client, mock_bucket, _, _ = mock_gcs
        src = tmp_path / "slide.jpg"
        src.touch()

        key = client.upload_preview("job-99", 10, src)

        assert key == "jobs/job-99/previews/slide-10.jpg"


class TestDownloadPreview:
    def test_calls_correct_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        dest = tmp_path / "slide-01.jpg"

        client.download_preview("job-42", 1, dest)

        mock_bucket.blob.assert_called_once_with("jobs/job-42/previews/slide-01.jpg")
        mock_blob.download_to_filename.assert_called_once_with(str(dest))


# ---------------------------------------------------------------------------
# job_workdir — 임시 파일 정리
# ---------------------------------------------------------------------------


class TestJobWorkdir:
    def test_yields_existing_path(self):
        with job_workdir("test-job") as workdir:
            assert workdir.exists()
            assert workdir.is_dir()
            captured = workdir

        assert not captured.exists()

    def test_cleanup_on_exception(self):
        captured = None
        with pytest.raises(RuntimeError):
            with job_workdir("test-job") as workdir:
                captured = workdir
                raise RuntimeError("simulated failure")

        assert captured is not None
        assert not captured.exists()

    def test_workdir_under_tmp(self):
        with job_workdir("test-job") as workdir:
            assert str(workdir).startswith("/tmp")

    def test_files_inside_are_removed(self):
        with job_workdir("test-job") as workdir:
            (workdir / "output.pptx").write_bytes(b"data")
            (workdir / "output.pdf").write_bytes(b"data")
            captured = workdir

        assert not captured.exists()
