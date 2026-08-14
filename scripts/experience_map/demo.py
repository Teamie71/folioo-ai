"""메인 서버 없이 경험 맵 처리 흐름을 확인하는 로컬 데모.

실제 LangGraph 배선, validate, coordinator와 SSE 이벤트 모델을 실행한다. 외부 LLM,
템플릿 API, 커밋 API만 결정적인 in-memory 대역으로 바꾸므로 API 키나 메인 서버 없이
입력 → 처리 이벤트 → 가상 맵 변경을 확인할 수 있다.

실행:
    uv run python scripts/experience_map/demo.py
"""

import json
import sys
from pathlib import Path

# `uv run python scripts/...`로 실행할 때도 저장소 패키지를 찾게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.experience_map.demo import run_demo


async def main() -> None:
    """데모 입력을 실행하고 SSE 형식 이벤트와 가상 맵 변경을 출력한다."""
    events, demo_map = await run_demo()
    print("=== 경험 맵 로컬 데모 SSE ===")
    for event in events:
        print(f"data: {json.dumps(event, ensure_ascii=False)}")

    print("\n=== 가상 경험 맵 ===")
    print(json.dumps(demo_map, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
