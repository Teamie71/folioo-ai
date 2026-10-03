"""`langgraph.json` 등록 경로 검증

경로에 오타가 있거나 심볼이 없으면 `langgraph dev` 가 **등록된 그래프 전체**를
띄우지 못한다. 하나가 깨지면 나머지도 같이 못 쓴다. 실행 없이 잡을 수 있는
문제라 여기서 막는다.
"""

import importlib
import json
import pathlib

import pytest

MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "langgraph.json"


def _graphs() -> dict[str, str]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["graphs"]


def test_manifest_registers_both_agents():
    """인터뷰와 경험정리 그래프가 모두 등록돼 있다."""
    assert set(_graphs()) == {"interview-agent", "experience-map-agent"}


@pytest.mark.parametrize("name", sorted(_graphs()))
def test_registered_target_is_importable(name):
    """`module:symbol` 이 실제로 import 되고 컴파일된 그래프여야 한다."""
    module_path, _, symbol = _graphs()[name].partition(":")
    assert symbol, f"{name}: `module:symbol` 형식이 아닙니다."

    module = importlib.import_module(module_path)
    graph = getattr(module, symbol, None)

    assert graph is not None, f"{name}: {module_path} 에 `{symbol}` 이 없습니다."
    # 컴파일된 그래프만 서빙할 수 있다. builder 를 그대로 가리키면 기동에서 깨진다.
    assert hasattr(graph, "get_graph"), f"{name}: 컴파일된 그래프가 아닙니다."
