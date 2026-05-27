#!/usr/bin/env bash
set -euo pipefail

command -v soffice >/dev/null
command -v pdftoppm >/dev/null
soffice --headless --version >/dev/null
fc-match "Noto Sans CJK KR" | grep -qi "Noto"

python - <<'PY'
import defusedxml  # noqa: F401
import fastapi  # noqa: F401
import lxml.etree  # noqa: F401
import markitdown  # noqa: F401
import PIL  # noqa: F401
import uvicorn  # noqa: F401
from google.cloud import storage, tasks_v2  # noqa: F401

import common  # noqa: F401
import features.visualization.pptx.soffice_render  # noqa: F401
import pptx_worker  # noqa: F401
PY
