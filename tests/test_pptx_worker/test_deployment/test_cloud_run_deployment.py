"""PPTX 워커 Cloud Run 배포 설정 테스트."""

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = ROOT / "apps" / "pptx-worker" / "Dockerfile"
SERVICE_YAML = ROOT / "deploy" / "pptx-worker" / "cloud-run-service.yaml"
CLOUDBUILD_YAML = ROOT / "deploy" / "pptx-worker" / "cloudbuild.yaml"
README = ROOT / "deploy" / "pptx-worker" / "README.md"
VERIFY_SCRIPT = ROOT / "apps" / "pptx-worker" / "scripts" / "verify_runtime_image.sh"
PYPROJECT = ROOT / "pyproject.toml"


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


@pytest.mark.parametrize(
    "expected",
    [
        "FROM ubuntu:22.04",
        "libreoffice-impress",
        "libreoffice-core",
        "poppler-utils",
        "fonts-noto-cjk",
        "fonts-noto-cjk-extra",
        "python3",
        "python3-pip",
        "uv sync --frozen --no-dev --only-group pptx-worker",
        "COPY common/ ./common/",
        "COPY features/visualization/ ./features/visualization/",
        "COPY apps/pptx-worker/ ./apps/pptx-worker/",
        "UV_PYTHON_INSTALL_DIR=/opt/uv-python",
        "PYTHONPATH=/app:/app/apps/pptx-worker",
        "JAVA_TOOL_OPTIONS=-Xmx512m",
        "useradd --create-home --uid 10001 appuser",
        "chown -R appuser:appuser /app /opt/uv-python",
        "USER appuser",
        "PORT:-8080",
        "pptx_worker.main:app",
    ],
)
def test_worker_dockerfile_has_runtime_toolchain_and_separate_entrypoint(
    expected: str,
) -> None:
    """워커 Dockerfile 이 변환 도구와 워커 전용 진입점을 포함한다."""
    dockerfile = DOCKERFILE.read_text()

    assert expected in dockerfile


def test_worker_dockerfile_excludes_unrelated_application_units() -> None:
    """워커 이미지는 메인 앱과 비시각화 feature 트리를 복사하지 않는다."""
    dockerfile = DOCKERFILE.read_text()

    assert "COPY app/ ./app/" not in dockerfile
    assert "COPY features/ ./features/" not in dockerfile


@pytest.mark.parametrize(
    "expected",
    [
        "defusedxml>=0.7.1",
        "fastapi>=0.128.0",
        "google-cloud-storage>=3.10.1",
        "google-cloud-tasks>=2.18.0",
        "httpx>=0.28.1",
        "langchain-core>=1.2.3",
        "langchain-openai>=1.1.7",
        "lxml>=6.0.0",
        "markitdown[pptx]>=0.1.3",
        "pillow>=11.0.0",
        "python-dotenv>=1.2.1",
        "uvicorn[standard]>=0.40.0",
    ],
)
def test_pptx_worker_dependency_group_includes_runtime_imports(expected: str) -> None:
    """워커 전용 dependency group 이 실제 앱 import 의존성을 포함한다."""
    pyproject = tomllib.loads(PYPROJECT.read_text())

    assert expected in pyproject["dependency-groups"]["pptx-worker"]


@pytest.mark.parametrize(
    "expected",
    [
        "command -v soffice",
        "command -v pdftoppm",
        'fc-match "Noto Sans CJK KR"',
        "PYTHON_BIN",
        "/app/.venv/bin/python",
        "python3",
        "import defusedxml",
        "import fastapi",
        "import langchain_openai",
        "import lxml.etree",
        "import markitdown",
        "import PIL",
        "import uvicorn",
        "from dotenv import load_dotenv",
        "from google.cloud import storage, tasks_v2",
        "from langchain_core.messages import HumanMessage, SystemMessage",
        "import common",
        "import pptx_worker",
        "ANTHROPIC_PPTX_SKILL_ENV",
        "PptxToolchain.from_env",
        "PptxToolchainError",
        "ensure_available()",
        "pptx_worker.main",
        "main.create_app()",
        "/tasks/visualizations/generate",
        "/tasks/visualizations/regenerate",
        "subprocess.run",
    ],
)
def test_runtime_image_smoke_script_checks_required_binaries_and_imports(
    expected: str,
) -> None:
    """이미지 스모크 스크립트가 시스템 도구와 Python 의존성을 검증한다."""
    script = VERIFY_SCRIPT.read_text()

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
    assert (
        config["substitutions"]["_IMAGE_URI"]
        == "asia-northeast3-docker.pkg.dev/$PROJECT_ID/folioo-ai/pptx-worker:latest"
    )


@pytest.mark.parametrize(
    "expected",
    [
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
    ],
)
def test_deployment_readme_documents_required_auth_checks(expected: str) -> None:
    """운영 확인 절차가 require-authentication 과 Cloud Tasks SA 호출을 다룬다."""
    readme = README.read_text()

    assert expected in readme


@pytest.mark.parametrize(
    "expected",
    [
        "이미지 build smoke는 `ANTHROPIC_PPTX_SKILL_DIR` 미설정 시 빠르게 실패하는 경로만",
        "Secret/volume/env로 runtime에 주입",
        "`PptxToolchain.ensure_available()`까지",
    ],
)
def test_deployment_readme_documents_runtime_skill_smoke_scope(expected: str) -> None:
    """PPTX skill directory smoke 범위가 build/runtime 경계를 설명한다."""
    readme = README.read_text()

    assert expected in readme
