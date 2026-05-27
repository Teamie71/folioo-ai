"""PPTX 워커 Cloud Run 배포 설정 테스트."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "apps" / "pptx-worker" / "Dockerfile"
SERVICE_YAML = ROOT / "deploy" / "pptx-worker" / "cloud-run-service.yaml"
CLOUDBUILD_YAML = ROOT / "deploy" / "pptx-worker" / "cloudbuild.yaml"
README = ROOT / "deploy" / "pptx-worker" / "README.md"
VERIFY_SCRIPT = ROOT / "apps" / "pptx-worker" / "scripts" / "verify_runtime_image.sh"


def _load_service() -> dict:
    return yaml.safe_load(SERVICE_YAML.read_text())


def _container(service: dict) -> dict:
    return service["spec"]["template"]["spec"]["containers"][0]


def test_cloud_run_service_matches_worker_resource_spec() -> None:
    """Cloud Run 서비스 사양이 soffice 워커 리소스 요구를 반영한다."""
    service = _load_service()
    template = service["spec"]["template"]
    template_spec = template["spec"]
    annotations = template["metadata"]["annotations"]
    container = _container(service)

    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "folioo-pptx-worker"
    assert service["metadata"]["annotations"]["run.googleapis.com/invoker-iam-disabled"] == "false"
    assert annotations["autoscaling.knative.dev/minScale"] == "0"
    assert annotations["autoscaling.knative.dev/maxScale"] == "20"
    assert annotations["folioo.ai/memory-budget"] == "4096Mi"
    assert annotations["folioo.ai/tmp-required"] == "1Gi"
    assert template_spec["containerConcurrency"] == 1
    assert template_spec["timeoutSeconds"] == 1800
    assert template_spec["serviceAccountName"] == "${WORKER_RUNTIME_SERVICE_ACCOUNT}"
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "5120Mi"}


def test_cloud_run_service_caps_java_heap_and_tmp_volume() -> None:
    """JVM 힙 캡과 /tmp 1Gi 메모리 볼륨을 서비스 사양에 고정한다."""
    service = _load_service()
    container = _container(service)
    env = {item["name"]: item["value"] for item in container["env"]}
    volume_mounts = {item["name"]: item["mountPath"] for item in container["volumeMounts"]}
    volumes = {item["name"]: item for item in service["spec"]["template"]["spec"]["volumes"]}

    assert env["JAVA_TOOL_OPTIONS"] == "-Xmx512m"
    assert env["PPTX_WORKER_RECYCLE_AFTER"] == "20"
    assert volume_mounts["pptx-worker-tmp"] == "/tmp"
    assert volumes["pptx-worker-tmp"]["emptyDir"] == {
        "medium": "Memory",
        "sizeLimit": "1Gi",
    }


def test_worker_dockerfile_has_runtime_toolchain_and_separate_entrypoint() -> None:
    """워커 Dockerfile 이 변환 도구와 워커 전용 진입점을 포함한다."""
    dockerfile = DOCKERFILE.read_text()

    for expected in [
        "FROM ubuntu:22.04",
        "libreoffice-impress",
        "libreoffice-core",
        "poppler-utils",
        "fonts-noto-cjk",
        "fonts-noto-cjk-extra",
        "python3",
        "python3-pip",
        "uv sync --frozen --no-dev --group template-tools",
        "COPY common/ ./common/",
        "COPY features/ ./features/",
        "COPY apps/pptx-worker/ ./apps/pptx-worker/",
        "PYTHONPATH=/app:/app/apps/pptx-worker",
        "JAVA_TOOL_OPTIONS=-Xmx512m",
        "pptx_worker.main:app",
    ]:
        assert expected in dockerfile

    assert "COPY app/ ./app/" not in dockerfile


def test_runtime_image_smoke_script_checks_required_binaries_and_imports() -> None:
    """이미지 스모크 스크립트가 시스템 도구와 Python 의존성을 검증한다."""
    script = VERIFY_SCRIPT.read_text()

    for expected in [
        "command -v soffice",
        "command -v pdftoppm",
        'fc-match "Noto Sans CJK KR"',
        "import defusedxml",
        "import fastapi",
        "import lxml.etree",
        "import markitdown",
        "import PIL",
        "import uvicorn",
        "from google.cloud import storage, tasks_v2",
        "import common",
        "import pptx_worker",
    ]:
        assert expected in script


def test_cloudbuild_uses_worker_dockerfile() -> None:
    """Cloud Build 구성이 워커 Dockerfile 을 별도 이미지로 빌드한다."""
    config = yaml.safe_load(CLOUDBUILD_YAML.read_text())
    args = config["steps"][0]["args"]

    assert args == [
        "build",
        "-f",
        "apps/pptx-worker/Dockerfile",
        "-t",
        "${_IMAGE_URI}",
        ".",
    ]
    assert config["images"] == ["${_IMAGE_URI}"]


def test_deployment_readme_documents_required_auth_checks() -> None:
    """운영 확인 절차가 require-authentication 과 Cloud Tasks SA 호출을 다룬다."""
    readme = README.read_text()

    for expected in [
        "--invoker-iam-check",
        "roles/run.invoker",
        "roles/iam.serviceAccountUser",
        "serviceAccount:${CLOUD_TASKS_SERVICE_ACCOUNT}",
        "TASK_ENQUEUER_PRINCIPAL",
        "iam.serviceAccounts.actAs",
        "oidc_token.service_account_email",
        "curl -sS -o /tmp/unauth.out",
        '--impersonate-service-account "$CLOUD_TASKS_SERVICE_ACCOUNT"',
        "401 또는 403",
    ]:
        assert expected in readme
