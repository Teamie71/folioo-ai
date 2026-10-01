"""파일 전용 경로 테스트: 줄 나누기, 칸 배정, 트리 만들기, 노드 분기."""

import pytest
from langchain_core.runnables import RunnableLambda

from features.experience_map import extractors
from features.experience_map.file_pipeline import assign as assign_module
from features.experience_map.file_pipeline import build_file_items, split_document
from features.experience_map.file_pipeline.assign import (
    AssignOutput,
    DocumentGate,
    LineAssignment,
    _VerifyOutput,
)
from features.experience_map.map_context import MapBlockRow, build_map_snapshot
from features.experience_map.nodes import content_filter as filter_node
from features.experience_map.nodes import structure as structure_node
from features.experience_map.state import start_turn
from features.experience_map.templates import TemplateCatalog
from features.experience_map.test_runtime import _test_template_catalog


async def _catalog() -> TemplateCatalog:
    return TemplateCatalog.model_validate(await _test_template_catalog())


def _template_rows(catalog_payload: dict) -> list[MapBlockRow]:
    """메인 서버가 새 활동에 미리 깔아 두는 빈 템플릿 블록을 흉내 낸다."""
    rows: list[MapBlockRow] = []
    next_id = [1000]

    def add(parent, level, kind, position, placeholder, editable=True):
        next_id[0] += 1
        block_id = str(next_id[0])
        rows.append(
            MapBlockRow(
                block_id=block_id,
                parent_id=parent,
                level=level,
                kind=kind,
                position=position,
                content=None,
                placeholder=placeholder,
                is_text_editable=editable,
                is_deletable=False,
            )
        )
        return block_id

    rows.append(MapBlockRow("100", None, 1, "CONTENT", 1, "그룹", None, False, False))
    rows.append(MapBlockRow("200", "100", 2, "EXPERIENCE", 1, "활동", None, True, False))
    for position, section in enumerate(catalog_payload["sections"], start=1):
        container = add("200", 3, f"SECTION_{section['section_id']}", position, "내용", False)
        for slot_position, slot in enumerate(section["slots"], start=1):
            slot_id = add(container, 4, "CONTENT", slot_position, slot["placeholder"])
            if slot.get("is_anchor") and section["templates"]:
                for child_position, child in enumerate(section["templates"][0]["slots"], 1):
                    add(slot_id, 5, "CONTENT", child_position, child["placeholder"])
    return rows


async def _template_state() -> dict:
    snapshot = build_map_snapshot(_template_rows(await _test_template_catalog()), 1)
    alias = snapshot.block_id_to_activity_alias()["200"]
    context = snapshot.get_activity_context(alias)
    state = start_turn(
        {"user_id": "1", "session_id": "s"},
        request_id="r",
        request_hash="h",
        user_message=None,
        context_experience_id="200",
    )
    state.update(
        target_experience_alias=alias,
        activity_tree_text=context.tree_text,
        alias_to_block_id=context.alias_to_block_id,
        alias_metadata=context.alias_metadata,
    )
    return state


# ===== 줄 나누기 =====


def test_split_expands_tables_by_header_and_drops_separator():
    document = split_document(
        "| 항목 | 내용 |\n|---|---|\n| 기간 | 2024.01 ~ 2024.05 |\n"
        "| 문제 | 해결 | 결과 |\n| 노쇼 30% | 학생증 인증 | 노쇼 9% |"
    )

    assert document.lines == [
        "항목 | 내용",
        "기간: 2024.01 ~ 2024.05",
        "문제 | 해결 | 결과",
        "문제: 노쇼 30%",
        "해결: 학생증 인증",
        "결과: 노쇼 9%",
    ]


def test_split_joins_pdf_wrapped_line_only_after_connective_ending():
    document = split_document(
        "- 근거: 객관적 데이터가 합의를 이끌어\n\n내는 데 효과적이라고 판단\n배운 점\n- 성장했다."
    )

    assert document.lines == [
        "- 근거: 객관적 데이터가 합의를 이끌어 내는 데 효과적이라고 판단",
        "배운 점",
        "- 성장했다.",
    ]


def test_split_keeps_question_marker_with_its_sentence():
    document = split_document("Q. 어떤 경험인가요?\nA. 카페에서 일했습니다. 직원은 4명이었습니다.")

    assert document.lines == [
        "Q. 어떤 경험인가요?",
        "A. 카페에서 일했습니다.",
        "직원은 4명이었습니다.",
    ]


def test_split_removes_code_noise():
    document = split_document(
        "홍길동 | 010-1234-5678\n- 1 -\n아래 경험을 정리해 주세요.\n- 부스 40개를 배치했다\n"
        "Confidential - 무단 배포 금지"
    )

    assert document.lines == ["- 부스 40개를 배치했다"]
    assert len(document.noise) == 4


# ===== 트리 만들기 =====


@pytest.mark.asyncio
async def test_tree_fills_prefilled_blank_blocks_with_updates():
    """미리 깔린 빈 상세정보 칸과 빈 업무 앵커는 새로 만들지 않고 update로 채운다."""
    catalog = await _catalog()
    state = await _template_state()
    lines = {"it_1": "기간: 2023.03 ~ 2023.06", "it_2": "[시장 조사]", "it_3": "- 설문 수행"}
    assignments = {
        "it_1": {"slot_id": "DETAIL.PERIOD", "episode": None},
        "it_2": {"slot_id": "TASK.SUMMARY", "episode": "it_2"},
        "it_3": {"slot_id": "TASK.BASIC.EXECUTION", "episode": "it_2"},
    }

    items = build_file_items(lines, assignments, state, catalog)

    assert [item["action"] for item in items] == ["update", "update", "update"]
    assert [item["text"] for item in items] == [
        "기간: 2023.03 ~ 2023.06",
        "[시장 조사]",
        "- 설문 수행",
    ]


@pytest.mark.asyncio
async def test_tree_adds_second_episode_with_full_template():
    """빈 앵커를 다 쓰면 새 앵커를 만들고 템플릿 칸을 모두 펼친다."""
    catalog = await _catalog()
    state = await _template_state()
    lines = {"it_1": "[A 업무]", "it_2": "- A 실행", "it_3": "[B 업무]", "it_4": "- B 실행"}
    assignments = {
        "it_1": {"slot_id": "TASK.SUMMARY", "episode": "it_1"},
        "it_2": {"slot_id": "TASK.BASIC.EXECUTION", "episode": "it_1"},
        "it_3": {"slot_id": "TASK.SUMMARY", "episode": "it_3"},
        "it_4": {"slot_id": "TASK.BASIC.EXECUTION", "episode": "it_3"},
    }

    items = build_file_items(lines, assignments, state, catalog)

    new_anchor = next(item for item in items if item.get("slot_id") == "TASK.SUMMARY")
    assert new_anchor["text"] == "[B 업무]"
    children = [item for item in items if item.get("parent_item_id") == new_anchor["item_id"]]
    assert len(children) == 4  # TASK.BASIC 템플릿 칸 4개를 모두 펼친다
    assert [child["text"] for child in children if child["text"]] == ["- B 실행"]


@pytest.mark.asyncio
async def test_tree_promotes_first_line_when_episode_has_no_summary():
    catalog = await _catalog()
    state = await _template_state()
    lines = {"it_1": "결제 지연이 반복됐다.", "it_2": "N+1 쿼리가 원인이었다."}
    assignments = {
        "it_1": {"slot_id": "PROBLEM_SOLVING.BASIC.PROBLEM", "episode": "it_1"},
        "it_2": {"slot_id": "PROBLEM_SOLVING.BASIC.CAUSE", "episode": "it_1"},
    }

    items = build_file_items(lines, assignments, state, catalog)

    assert items[0] == {
        "item_id": items[0]["item_id"],
        "action": "update",
        "target_ref": items[0]["target_ref"],
        "text": "결제 지연이 반복됐다.",
    }
    assert any(item["text"] == "N+1 쿼리가 원인이었다." for item in items[1:])


# ===== 칸 배정 =====


class _FakeLlm:
    """스키마별로 정해 둔 응답을 돌려주는 LLM 대역. 프롬프트는 통과(dict 그대로)로 바꾼다."""

    def __init__(self, gate: DocumentGate, assign, verify=None):
        self.gate = gate
        self.assign = assign
        self.verify = verify

    def with_structured_output(self, schema):
        if schema is DocumentGate:
            return RunnableLambda(lambda _payload: self.gate)
        if schema is _VerifyOutput:
            return RunnableLambda(self.verify)
        return RunnableLambda(self.assign)


@pytest.fixture
def passthrough_prompts(monkeypatch):
    for name in (
        "assign_prompt",
        "reassign_prompt",
        "document_gate_prompt",
        "exclude_verify_prompt",
    ):
        monkeypatch.setattr(assign_module, name, RunnableLambda(lambda payload: payload))


@pytest.mark.asyncio
async def test_assign_stops_when_document_is_not_an_experience(monkeypatch, passthrough_prompts):
    llm = _FakeLlm(
        DocumentGate(is_experience=False, doc_type="시험지", reason="수학 문제"),
        lambda _p: AssignOutput(items=[LineAssignment(id="it_1", slot_id="TASK.SUMMARY")]),
    )
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(["1. f'(2)를 구하시오."], await _catalog())

    assert result.is_experience is False
    assert result.doc_type == "시험지"
    assert result.assignments == {}


@pytest.mark.asyncio
async def test_assign_excludes_only_lines_confirmed_unrelated(monkeypatch, passthrough_prompts):
    def assign(payload):
        if "ids" in payload:  # 확인에서 남기기로 한 줄 재배정
            return AssignOutput(items=[LineAssignment(id="it_2", slot_id="DETAIL.PERIOD")])
        return AssignOutput(
            items=[
                LineAssignment(id="it_1", slot_id="TASK.SUMMARY", episode="it_1"),
                LineAssignment(id="it_2", slot_id="EXCLUDE", reason="무관"),
                LineAssignment(id="it_3", slot_id="EXCLUDE", reason="자기소개"),
            ]
        )

    def verify(_payload):
        return _VerifyOutput(items=[{"id": "it_2", "keep": True}, {"id": "it_3", "keep": False}])

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign, verify)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["[축제 운영]", "2023.09 ~ 2023.10", "취미는 등산입니다."], await _catalog()
    )

    assert result.excluded == ["취미는 등산입니다."]
    assert result.assignments["it_2"]["slot_id"] == "DETAIL.PERIOD"
    assert "it_3" not in result.assignments


@pytest.mark.asyncio
async def test_assign_never_drops_content_line_left_unassigned(monkeypatch, passthrough_prompts):
    """재요청까지 실패한 내용 줄은 앞 줄의 칸에 붙여 원문을 잃지 않는다."""

    def assign(payload):
        return AssignOutput(items=[LineAssignment(id="it_1", slot_id="LEARNING.GROWTH")])

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["데이터로 설득하는 법을 배웠다.", "앞으로 시각화 툴을 익히겠다."], await _catalog()
    )

    assert result.assignments["it_2"] == {"slot_id": "LEARNING.GROWTH", "episode": None}


# ===== 노드 분기 =====


@pytest.mark.asyncio
async def test_content_filter_falls_back_for_non_experience_file(monkeypatch):
    async def fake_assign(_lines, _catalog):
        return assign_module.FileAssignment(is_experience=False, doc_type="채용공고", lines={})

    class _Client:
        async def get_catalog(self):
            return await _catalog()

    monkeypatch.setattr("features.experience_map.file_pipeline.assign_lines", fake_assign)
    monkeypatch.setattr(
        "features.experience_map.templates.get_template_catalog_client", lambda: _Client()
    )
    state = await _template_state()
    state["extracted_text"] = "[채용] 백엔드 개발자\n- 자격 요건: Java 3년 이상"

    result = await filter_node.filter_content(state)

    assert result["fallback_reason"] == "not_experience_file"
    assert result["new_items"] == []


@pytest.mark.asyncio
async def test_content_filter_and_structure_build_items_for_file(monkeypatch):
    async def fake_assign(lines, _catalog):
        return assign_module.FileAssignment(
            is_experience=True,
            doc_type="경험",
            lines={"it_1": lines[0]},
            assignments={"it_1": {"slot_id": "DETAIL.PERIOD", "episode": None}},
            excluded=["취미"],
        )

    class _Client:
        async def get_catalog(self):
            return await _catalog()

    monkeypatch.setattr("features.experience_map.file_pipeline.assign_lines", fake_assign)
    monkeypatch.setattr(
        "features.experience_map.templates.get_template_catalog_client", lambda: _Client()
    )
    monkeypatch.setattr(structure_node, "get_template_catalog_client", lambda: _Client())
    state = await _template_state()
    state["extracted_text"] = "기간: 2023.03 ~ 2023.06"

    filtered = await filter_node.filter_content(state)
    structured = await structure_node.structure_blocks(filtered)

    assert filtered["file_excluded_count"] == 1
    assert filtered["new_items"][0]["item_id"] == "it_1"
    assert structured["structured_items"][0]["action"] == "update"
    assert structured["structured_items"][0]["text"] == "기간: 2023.03 ~ 2023.06"


# ===== PDF 텍스트 레이어 =====


@pytest.mark.asyncio
async def test_pdf_uses_text_layer_and_ocrs_only_unusable_pages(monkeypatch):
    layer = ["가" * 50, "", "나" * 50]
    ocr_calls: list[list[int]] = []

    async def fake_ocr(_data, indexes):
        ocr_calls.append(indexes)
        return ["스캔 페이지" for _ in indexes]

    monkeypatch.setattr(extractors, "_pdf_text_layer", lambda _data: list(layer))
    monkeypatch.setattr(extractors, "_ocr_pdf_pages", fake_ocr)
    monkeypatch.setattr(extractors, "_pdf_total_page_count", lambda _data: 3)

    text = await extractors.extract_with_ocr(b"pdf", "a.pdf", "application/pdf")

    assert ocr_calls == [[1]]
    assert text.split("\n\n") == ["가" * 50, "스캔 페이지", "나" * 50]


def test_garbled_text_layer_is_not_used():
    assert extractors._usable_text_layer("경험 정리 " * 10) is True
    assert extractors._usable_text_layer("(cid:12)" * 40 + "가" * 40) is False
    assert extractors._usable_text_layer("짧음") is False
