"""GCS 클라이언트 단위 테스트"""

from unittest.mock import MagicMock, call, patch

import pytest

from features.visualization.storage.gcs_client import (
    GcsClient,
    _validate_identifier,
    job_workdir,
    pdf_key,
    pptx_key,
    preview_key,
    regeneration_attempt_pdf_key,
    regeneration_attempt_pptx_key,
    regeneration_attempt_preview_key,
    template_meta_key,
    template_pptx_key,
    template_thumbnail_key,
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

    def test_regeneration_attempt_keys(self):
        assert (
            regeneration_attempt_pptx_key("job-abc", "attempt-1")
            == "jobs/job-abc/attempts/attempt-1/current.pptx"
        )
        assert (
            regeneration_attempt_pdf_key("job-abc", "attempt-1")
            == "jobs/job-abc/attempts/attempt-1/current.pdf"
        )
        assert (
            regeneration_attempt_preview_key("job-abc", "attempt-1", 3)
            == "jobs/job-abc/attempts/attempt-1/previews/slide-03.jpg"
        )

    def test_template_pptx_key(self):
        assert template_pptx_key("blue") == "templates/blue/template.pptx"

    def test_template_meta_key(self):
        assert template_meta_key("blue") == "templates/blue/meta.json"

    def test_template_thumbnail_key(self):
        assert template_thumbnail_key("blue") == "templates/blue/thumbnail.jpg"


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

    def test_attempt_key_helpers_reject_slash_in_attempt_id(self):
        with pytest.raises(ValueError):
            regeneration_attempt_pptx_key("job-42", "attempt/1")

    @pytest.mark.parametrize(
        "helper",
        [template_pptx_key, template_meta_key, template_thumbnail_key],
    )
    @pytest.mark.parametrize("template_id", ["../secret", "../../etc/passwd", "/abs/path"])
    def test_template_key_helpers_reject_traversal_in_template_id(self, helper, template_id):
        with pytest.raises(ValueError):
            helper(template_id)


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


def test_gcs_client_uses_gcs_bucket_env_when_bucket_name_is_not_explicit(monkeypatch):
    """기본 생성자는 Cloud Run GCS_BUCKET env 값을 버킷 이름으로 사용한다."""
    monkeypatch.setenv("GCS_BUCKET", "folioo-498017-visualizations")
    with patch("features.visualization.storage.gcs_client.storage.Client") as mock_client_cls:
        GcsClient()

    mock_client_cls.return_value.bucket.assert_called_once_with("folioo-498017-visualizations")


def _use_distinct_blob_mocks(mock_bucket):
    """bucket.blob(key) 호출마다 key 별 Blob mock 을 반환한다."""
    blobs: dict[str, MagicMock] = {}

    def _blob(name: str) -> MagicMock:
        if name not in blobs:
            blobs[name] = MagicMock(name=f"blob:{name}")
        return blobs[name]

    mock_bucket.blob.side_effect = _blob
    return blobs


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


class TestDownloadTemplateMeta:
    def test_calls_correct_key(self, mock_gcs):
        client, mock_bucket, mock_blob, tmp_path = mock_gcs
        dest = tmp_path / "meta.json"

        client.download_template_meta("blue", dest)

        mock_bucket.blob.assert_called_once_with("templates/blue/meta.json")
        mock_blob.download_to_filename.assert_called_once_with(str(dest))


class TestDownloadTemplateThumbnail:
    def test_calls_correct_key(self, mock_gcs):
        client, mock_bucket, mock_blob, tmp_path = mock_gcs
        dest = tmp_path / "thumbnail.jpg"

        client.download_template_thumbnail("blue", dest)

        mock_bucket.blob.assert_called_once_with("templates/blue/thumbnail.jpg")
        mock_blob.download_to_filename.assert_called_once_with(str(dest))


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


class TestUploadRegenerationAttemptPptx:
    def test_returns_attempt_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "output.pptx"
        src.touch()

        key = client.upload_regeneration_attempt_pptx("job-42", "attempt-1", src)

        assert key == "jobs/job-42/attempts/attempt-1/current.pptx"
        mock_bucket.blob.assert_called_once_with("jobs/job-42/attempts/attempt-1/current.pptx")
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


class TestUploadRegenerationAttemptPdf:
    def test_returns_attempt_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "output.pdf"
        src.touch()

        key = client.upload_regeneration_attempt_pdf("job-42", "attempt-1", src)

        assert key == "jobs/job-42/attempts/attempt-1/current.pdf"
        mock_bucket.blob.assert_called_once_with("jobs/job-42/attempts/attempt-1/current.pdf")
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


class TestUploadRegenerationAttemptPreview:
    def test_returns_attempt_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        src = tmp_path / "slide.jpg"
        src.touch()

        key = client.upload_regeneration_attempt_preview("job-42", "attempt-1", 3, src)

        assert key == "jobs/job-42/attempts/attempt-1/previews/slide-03.jpg"
        mock_bucket.blob.assert_called_once_with(
            "jobs/job-42/attempts/attempt-1/previews/slide-03.jpg"
        )
        mock_blob.upload_from_filename.assert_called_once_with(str(src), content_type="image/jpeg")


class TestDownloadPreview:
    def test_calls_correct_key(self, mock_gcs, tmp_path):
        client, mock_bucket, mock_blob, _ = mock_gcs
        dest = tmp_path / "slide-01.jpg"

        client.download_preview("job-42", 1, dest)

        mock_bucket.blob.assert_called_once_with("jobs/job-42/previews/slide-01.jpg")
        mock_blob.download_to_filename.assert_called_once_with(str(dest))


class TestPromoteRegenerationAttempt:
    def test_copies_attempt_artifacts_to_canonical_after_backup(self, mock_gcs):
        client, mock_bucket, _, _ = mock_gcs
        blobs = _use_distinct_blob_mocks(mock_bucket)

        keys = client.promote_regeneration_attempt("job-42", "attempt-1", 3)

        assert keys.canonical_pptx_key == "jobs/job-42/current.pptx"
        assert keys.canonical_pdf_key == "jobs/job-42/current.pdf"
        assert keys.canonical_preview_key == "jobs/job-42/previews/slide-03.jpg"
        assert mock_bucket.copy_blob.call_args_list == [
            call(
                blobs["jobs/job-42/current.pptx"],
                mock_bucket,
                "jobs/job-42/attempts/attempt-1/rollback/current.pptx",
            ),
            call(
                blobs["jobs/job-42/current.pdf"],
                mock_bucket,
                "jobs/job-42/attempts/attempt-1/rollback/current.pdf",
            ),
            call(
                blobs["jobs/job-42/previews/slide-03.jpg"],
                mock_bucket,
                "jobs/job-42/attempts/attempt-1/rollback/slide-03.jpg",
            ),
            call(
                blobs["jobs/job-42/attempts/attempt-1/current.pptx"],
                mock_bucket,
                "jobs/job-42/current.pptx",
            ),
            call(
                blobs["jobs/job-42/attempts/attempt-1/current.pdf"],
                mock_bucket,
                "jobs/job-42/current.pdf",
            ),
            call(
                blobs["jobs/job-42/attempts/attempt-1/previews/slide-03.jpg"],
                mock_bucket,
                "jobs/job-42/previews/slide-03.jpg",
            ),
        ]

    def test_restore_backups_when_promote_copy_fails(self, mock_gcs):
        client, mock_bucket, _, _ = mock_gcs
        blobs = _use_distinct_blob_mocks(mock_bucket)
        mock_bucket.copy_blob.side_effect = [
            None,
            None,
            None,
            None,
            RuntimeError("copy failed"),
            None,
            None,
            None,
        ]

        with pytest.raises(RuntimeError, match="copy failed"):
            client.promote_regeneration_attempt("job-42", "attempt-1", 3)

        assert mock_bucket.copy_blob.call_args_list[-3:] == [
            call(
                blobs["jobs/job-42/attempts/attempt-1/rollback/slide-03.jpg"],
                mock_bucket,
                "jobs/job-42/previews/slide-03.jpg",
            ),
            call(
                blobs["jobs/job-42/attempts/attempt-1/rollback/current.pdf"],
                mock_bucket,
                "jobs/job-42/current.pdf",
            ),
            call(
                blobs["jobs/job-42/attempts/attempt-1/rollback/current.pptx"],
                mock_bucket,
                "jobs/job-42/current.pptx",
            ),
        ]


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
