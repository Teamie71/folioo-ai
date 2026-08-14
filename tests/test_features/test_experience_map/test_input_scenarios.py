"""파일·채팅 입력이 새 블록 커밋 후보까지 가는 graph 시나리오 테스트."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from features.experience_map import graph as graph_module
from features.experience_map.state import build_thread_config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_kind", "content_type", "extractor"),
    [
        ("parser_file", "text/plain", "parser"),
        ("ocr_file", "application/pdf", "ocr"),
        ("chat", None, None),
    ],
)
async def test_input_kind_reaches_new_block_commit_candidate(
    monkeypatch, input_kind, content_type, extractor
):
    """파서·OCR 파일과 채팅 입력은 모두 새 block add 후보를 만든다."""
    calls: list[str] = []
    source_text = "결제 오류를 분석하고 재시도 로직을 추가했다."

    async def route(state):
        calls.append("router")
        return {**state, "intent": "file_input" if state.get("file_references") else "chat_input"}

    async def process_files(state):
        calls.append("file_processor")
        return {
            **state,
            "extracted_files": [
                {
                    "file_id": "file-1",
                    "text": source_text,
                    "source_hash": "a" * 64,
                    "extractor": extractor,
                }
            ],
            "extracted_text": source_text,
        }

    async def filter_content(state):
        calls.append("content_filter")
        assert state.get("extracted_text") == source_text if state.get("file_references") else True
        return {
            **state,
            "new_items": [
                {
                    "item_id": "input_1",
                    "text": source_text,
                    "source": "file" if state.get("file_references") else "message",
                }
            ],
            "gap_answer_items": [],
            "excluded_reasons": [],
        }

    async def select_target(state):
        calls.append("target_activity")
        return {
            **state,
            "target_experience_alias": "exp_1",
            "activity_tree_text": "[exp_1] 결제 개선\n  [b_1] 문제 해결",
        }

    async def structure(state):
        calls.append("structure")
        return {
            **state,
            "structured_items": [
                {
                    "item_id": "input_1",
                    "action": "add",
                    "parent_ref": "b_1",
                    "text": source_text,
                }
            ],
        }

    async def refine(state):
        calls.append("refine")
        return {
            **state,
            "refined_items": [
                {"item_id": "input_1", "refined_text": "결제 오류 분석 → 재시도 로직 추가"}
            ],
        }

    monkeypatch.setattr(graph_module, "route", route)
    monkeypatch.setattr(graph_module, "process_files", process_files)
    monkeypatch.setattr(graph_module, "filter_content", filter_content)
    monkeypatch.setattr(graph_module, "select_target_activity", select_target)
    monkeypatch.setattr(graph_module, "structure_blocks", structure)
    monkeypatch.setattr(graph_module, "refine_text", refine)
    graph = graph_module.build_graph(checkpointer=InMemorySaver())

    state = {
        "user_id": "1",
        "session_id": f"session-{input_kind}",
        "request_id": f"request-{input_kind}",
        "user_message": None if content_type else source_text,
        "file_references": (
            [
                {
                    "file_id": "file-1",
                    "filename": "input.pdf" if extractor == "ocr" else "input.txt",
                    "content_type": content_type,
                    "file_size": 10,
                    "sha256": "a" * 64,
                    "gcs_object": "demo/input",
                }
            ]
            if content_type
            else []
        ),
        "alias_to_block_id": {"exp_1": "101", "b_1": "305"},
    }
    result = await graph.ainvoke(state, build_thread_config(state["session_id"]))

    expected = ["router"]
    if content_type:
        expected.append("file_processor")
    expected.extend(["content_filter", "target_activity", "structure", "refine"])
    assert calls == expected
    assert result["commit_items"] == [
        {
            "item_id": "input_1",
            "action": "add",
            "parent_ref": "b_1",
            "text": "결제 오류 분석 → 재시도 로직 추가",
        }
    ]
