"""GCS 클라이언트 단위 테스트"""

from unittest.mock import MagicMock, patch

import pytest

from features.visualization.storage.gcs_client import (
    GcsClient,
    _validate_identifier,
    job_workdir,
    pdf_key,
    pptx_key,
    preview_key,
    template_pptx_key,
)

# ---------------------------------------------------------------------------
# canonical key 헬퍼
# ---------------------------------------------------------------------------


class TestCanonicalKeys:
    @pytest.mark.parametrize(
        ("slide_order", "expected"),
        [
            (1, "jobs/job-1/previews/slide-01.jpg"),
            (3, "jobs/job-1/previews/slide-03.jpg"),
            (12, "jobs/job-1/previews/slide-12.jpg"),
            (99, "jobs/job-1/previews/slide-99.jpg"),
        ],
    )
    def test_preview_key_valid(self, slide_order, expected):
        assert preview_key("job-1", slide_order) == expected

    @pytest.mark.parametrize("slide_order", [0, 100, -1])
    def test_preview_key_invalid_order(self, slide_order):
        with pytest.raises(ValueError, match="slide_order"):
            preview_key("job-1", slide_order)

    def test_pptx_key(self):
        assert pptx_key("job-abc") == "jobs/job-abc/current.pptx"

    def test_pdf_key(self):
        assert pdf_key("job-abc") == "jobs/job-abc/current.pdf"

    def test_template_pptx_key(self):
        assert template_pptx_key("blue") == "templates/blue/template.pptx"


class TestValidateIdentifier:
    @pytest.mark.parametrize("value", ["job-42", "blue", "abc_123", "JOB-ID"])
    def test_valid(self, value):
        assert _validate_identifier(value, "x") == value

    @pytest.mark.parametrize("value", ["", "job/42", "../secret", "job 1", "job.id"])
    def test_invalid(self, value):
        with pytest.raises(ValueError, match="x"):
            _validate_identifier(value, "x")

    def test_key_helpers_reject_slash_in_job_id(self):
        with pytest.raises(ValueError):
            pptx_key("job/42")

    def test_key_helpers_reject_traversal_in_template_id(self):
        with pytest.raises(ValueError):
            template_pptx_key("../secret")


# ---------------------------------------------------------------------------
# GcsClient — GCS 호출 모의 객체
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_gcs(tmp_path):
    """storage.Client 을 mock 으로 교체한 GcsClient 반환"""
    with patch("features.visualization.storage.gcs_client.storage.Client") as mock_client_cls:
        mock_blob = MagicMock()
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client_cls.return_value.bucket.return_value = mock_bucket

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
        mock_bucket.blob.assert_called_once_with("jobs/job-42/current.pdf")
        mock_blob.upload_from_filename.assert_called_once_with(
            str(src), content_type="application/pdf"
        )


class TestUploadPreview:
    @pytest.mark.parametrize(
        ("job_id", "slide_order", "expected_key"),
        [
            ("job-42", 3, "jobs/job-42/previews/slide-03.jpg"),
            ("job-99", 10, "jobs/job-99/previews/slide-10.jpg"),
        ],
    )
    def test_returns_canonical_key(self, mock_gcs, tmp_path, job_id, slide_order, expected_key):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "slide.jpg"
        src.touch()

        key = client.upload_preview(job_id, slide_order, src)

        assert key == expected_key
        mock_bucket.blob.assert_called_once_with(expected_key)
        mock_blob.upload_from_filename.assert_called_once_with(str(src), content_type="image/jpeg")


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

    def test_invalid_job_id_raises(self):
        with pytest.raises(ValueError, match="job_id"):
            with job_workdir("job/invalid"):
                pass
