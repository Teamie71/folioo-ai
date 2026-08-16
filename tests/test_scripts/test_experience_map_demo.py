"""메인 서버 없는 경험 맵 로컬 데모 smoke test."""

import subprocess
import sys
from pathlib import Path


def test_demo_runs_without_external_services():
    """데모가 SSE 이벤트와 가상 맵 변경을 출력한다."""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/experience_map/demo.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"type": "commit_result"' in result.stdout
    assert '"response_kind": "suggestion"' in result.stdout
    assert "=== 가상 경험 맵 ===" in result.stdout
    assert "결제 오류 원인 분석 → 재시도 로직 추가로 장애 감소" in result.stdout
