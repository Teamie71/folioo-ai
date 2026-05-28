"""PPTX 워커 런타임 import smoke 테스트."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKER_PATH = ROOT / "apps" / "pptx-worker"


def _worker_pythonpath() -> str:
    paths = [str(ROOT), str(WORKER_PATH)]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        paths.append(existing)
    return os.pathsep.join(paths)


def _run_python(source: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = _worker_pythonpath()
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def test_pptx_worker_package_import_does_not_eager_load_app() -> None:
    """패키지 import 만으로 FastAPI 앱과 라우터를 로드하지 않는다."""
    source = """
import sys

import pptx_worker  # noqa: F401

eager_modules = {
    "pptx_worker.main",
    "pptx_worker.api",
    "pptx_worker.api.tasks",
} & set(sys.modules)
if eager_modules:
    raise RuntimeError(f"eager worker imports detected: {sorted(eager_modules)}")
"""

    _run_python(textwrap.dedent(source))


def test_visualization_service_import_does_not_eager_load_worker_app() -> None:
    """service 단독 import 가 pptx_worker.main 순환 로딩을 유발하지 않는다."""
    source = """
import sys

import features.visualization.service  # noqa: F401

if "pptx_worker.main" in sys.modules:
    raise RuntimeError("features.visualization.service imported pptx_worker.main")
"""

    _run_python(textwrap.dedent(source))


@pytest.mark.parametrize(
    "module_names",
    [
        ("features.visualization.service", "features.visualization.qa", "pptx_worker.main"),
        ("features.visualization.qa", "features.visualization.service", "pptx_worker.main"),
        ("pptx_worker.main", "features.visualization.service", "features.visualization.qa"),
    ],
)
def test_worker_runtime_import_order_boots_fastapi_app(module_names: tuple[str, ...]) -> None:
    """주요 worker runtime 모듈은 import 순서와 무관하게 앱을 생성한다."""
    source = f"""
import importlib

from fastapi import FastAPI

for module_name in {module_names!r}:
    importlib.import_module(module_name)

main = importlib.import_module("pptx_worker.main")
app = main.create_app()
if not isinstance(app, FastAPI):
    raise TypeError(f"create_app returned {{type(app).__name__}}, expected FastAPI")

routes = {{route.path for route in app.routes}}
required = {{
    "/health",
    "/metrics",
    "/tasks/visualizations/generate",
    "/tasks/visualizations/regenerate",
}}
missing = sorted(required - routes)
if missing:
    raise RuntimeError(f"worker app routes missing: {{missing}}")
"""

    _run_python(textwrap.dedent(source))
