#!/usr/bin/env bash
set -euo pipefail

command -v soffice >/dev/null
command -v pdftoppm >/dev/null
soffice --headless --version >/dev/null
fc-match "Noto Sans CJK KR" | grep -qi "Noto"

PYTHON_BIN=""
for candidate in "${PYTHON:-}" /app/.venv/bin/python /app/.venv/bin/python3 python python3; do
    if [[ -z "$candidate" ]]; then
        continue
    fi
    if [[ "$candidate" == */* ]]; then
        if [[ -x "$candidate" ]]; then
            PYTHON_BIN="$candidate"
            break
        fi
        continue
    fi
    if command -v "$candidate" >/dev/null; then
        PYTHON_BIN="$(command -v "$candidate")"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo "python 실행 파일을 찾을 수 없습니다." >&2
    exit 127
fi

"$PYTHON_BIN" - <<'PY'
import importlib
import os
import subprocess
import sys
import textwrap

import defusedxml  # noqa: F401
import fastapi  # noqa: F401
import langchain_openai  # noqa: F401
import lxml.etree  # noqa: F401
import markitdown  # noqa: F401
import PIL  # noqa: F401
import uvicorn  # noqa: F401
from dotenv import load_dotenv  # noqa: F401
from fastapi import FastAPI
from google.cloud import storage, tasks_v2  # noqa: F401
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: F401

import common  # noqa: F401
import features.visualization.pptx.soffice_render  # noqa: F401
import pptx_worker  # noqa: F401
from features.visualization.pptx import (
    ANTHROPIC_PPTX_SKILL_ENV,
    PptxToolchain,
    PptxToolchainError,
)

if "pptx_worker.main" in sys.modules:
    raise RuntimeError("pptx_worker package import loaded pptx_worker.main eagerly")

try:
    PptxToolchain.from_env(env_var="FOLIOO_MISSING_PPTX_SKILL_DIR_SMOKE")
except PptxToolchainError as exc:
    if "환경변수가 설정되지 않았습니다" not in str(exc):
        raise
else:
    raise RuntimeError("missing PPTX skill env smoke did not fail")

if os.getenv(ANTHROPIC_PPTX_SKILL_ENV):
    PptxToolchain.from_env().ensure_available()


def run_import_order(module_names: tuple[str, ...]) -> None:
    source = textwrap.dedent(
        f"""
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
    )
    subprocess.run(
        [sys.executable, "-c", source],
        check=True,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )


for import_order in (
    ("features.visualization.service", "features.visualization.qa", "pptx_worker.main"),
    ("features.visualization.qa", "features.visualization.service", "pptx_worker.main"),
    ("pptx_worker.main", "features.visualization.service", "features.visualization.qa"),
):
    run_import_order(import_order)

main = importlib.import_module("pptx_worker.main")
app = main.create_app()
if not isinstance(app, FastAPI):
    raise TypeError(f"create_app returned {type(app).__name__}, expected FastAPI")
PY
