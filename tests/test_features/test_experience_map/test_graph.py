"""경험정리 LangGraph 배선 테스트 (에이전트 문서 4절)."""

from langgraph.checkpoint.memory import InMemorySaver

from features.experience_map.graph import RETRY_POLICY, _after_filter, build_graph
from features.experience_map.state import build_thread_config


def test_graph_compiles_with_checkpointer():
    """운영 PostgreSQL saver와 같은 BaseCheckpointSaver 계약으로 compile한다."""
    graph = build_graph(checkpointer=InMemorySaver())

    assert graph is not None


def test_retry_policy_is_one_automatic_retry():
    """LLM client retry와 중복되지 않게 그래프만 두 번 시도한다."""
    assert RETRY_POLICY.max_attempts == 2


def test_thread_config_has_experience_map_namespace():
    config = build_thread_config("d9428888-122b-11e1-b85c-61cd3cbb3210")

    assert config["configurable"]["checkpoint_ns"] == "experience_map"


def test_filter_fallback_skips_target_activity_selection():
    """반영할 내용이 없으면 대상 활동 선택 LLM을 부르지 않는다."""
    assert _after_filter({"gap_answer_items": [], "new_items": []}) == "fallback"
    assert (
        _after_filter({"gap_answer_items": [], "new_items": [{"item_id": "it_1"}]})
        == "target_activity"
    )
