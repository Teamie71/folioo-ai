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
from features.experience_map.file_pipeline.split import (
    heading_like,
    project_number,
    strip_markers,
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


def test_split_drops_system_truncation_note():
    document = split_document(
        "- 부스 40개를 배치했다\n\n[페이지가 많아 앞 10페이지만 사용했습니다. 전체 15페이지 중 일부입니다.]"
    )

    assert document.lines == ["- 부스 40개를 배치했다"]


def test_split_keeps_portfolio_layout_pieces_together():
    """포트폴리오 PDF에서 깨지던 모양: 번호 조각, 목록 번호, 사진 설명, 조사로 시작하는 줄."""
    document = split_document(
        "SALES (판매) P1. 축제 주점 MD\n"
        "▲ 주점 운영 현장\n문제 진단 (Analysis)\n"
        "1\n.\nHook: 호기심 자극\n"
        "실제 착용샷을 배치하여 클릭률(CTR)\n을 높였습니다.\n"
        "정보 전달자가 되어야 함을\n배웠습니다."
    )

    assert document.lines == [
        "SALES (판매) P1. 축제 주점 MD",
        "▲ 주점 운영 현장",
        "문제 진단 (Analysis)",
        "Hook: 호기심 자극",
        "실제 착용샷을 배치하여 클릭률(CTR) 을 높였습니다.",
        "정보 전달자가 되어야 함을 배웠습니다.",
    ]


def test_strip_markers_removes_decorations_but_keeps_content():
    assert strip_markers("• 매출 달성: 300만원") == "매출 달성: 300만원"
    assert strip_markers("▲ 2일차 저녁 8시") == "2일차 저녁 8시"
    assert strip_markers("💡 해결: Win-Win") == "해결: Win-Win"
    assert strip_markers("-15%") == "-15%"
    assert strip_markers("1. 제안") == "1. 제안"
    assert strip_markers('"소싱의 핵심"') == '"소싱의 핵심"'


def test_project_number_reads_roadmap_and_body_titles():
    assert project_number("P2. 굿즈 공동구매") == 2
    assert project_number("Project 2. 학과 굿즈 공동구매") == 2
    assert project_number("Pain Point: 비싸다") is None
    assert project_number("2023년 축제") is None


def test_chunks_do_not_cut_inside_project_listing():
    roadmap = [f"P{n}. 프로젝트 {n}" for n in range(1, 7)]
    body = [f"내용 {i}." for i in range(30)]
    texts = ["표지"] + [line for title in roadmap for line in (title, "한 줄 요약")]
    texts += ["Project 1. 프로젝트 1"] + body + ["Project 2. 프로젝트 2"] + body
    lines = {f"it_{i}": text for i, text in enumerate(texts, 1)}

    chunks = assign_module._chunks(list(lines), lines)

    starts = [lines[chunk[0]] for chunk in chunks]
    assert not any(start.startswith("P") and "Project" not in start for start in starts[1:])
    assert "Project 2. 프로젝트 2" in starts


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
        "설문 수행",
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
    assert [child["text"] for child in children if child["text"]] == ["B 실행"]


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


def test_episode_keeps_only_first_summary_line():
    """프로젝트 안 소제목에 붙은 SUMMARY는 바로 다음 내용 줄 칸으로 옮긴다."""
    by_id = {
        "it_1": LineAssignment(id="it_1", slot_id="TASK.SUMMARY", episode="it_1"),
        "it_2": LineAssignment(id="it_2", slot_id="TASK.BASIC.EXECUTION", episode="it_1"),
        "it_3": LineAssignment(id="it_3", slot_id="TASK.SUMMARY", episode="it_1"),
        "it_4": LineAssignment(id="it_4", slot_id="TASK.BASIC.RESULT", episode="it_1"),
    }

    assign_module._single_summary(list(by_id), by_id)

    assert [item.slot_id for item in by_id.values()] == [
        "TASK.SUMMARY",
        "TASK.BASIC.EXECUTION",
        "TASK.BASIC.RESULT",
        "TASK.BASIC.RESULT",
    ]


def test_short_fact_lines_are_not_headings():
    """지표 카드("250만원")·인용 줄은 짧아도 제목으로 버리지 않는다."""
    assert not heading_like("250만원")
    assert not heading_like("99.9°C")
    assert not heading_like('Q 배경: "공부할 곳이 없어요"')
    assert heading_like("총 매출")
    assert heading_like("Project 2. 학과 굿즈 공동구매")


def test_project_document_attaches_stray_task_episodes_to_previous_project():
    """프로젝트 제목이 둘 이상이면 프로젝트 밖 담당업무 묶음은 바로 앞 프로젝트에 붙는다."""
    lines = {
        "it_1": "Project 1. 축제 주점",
        "it_2": "Key: 회전율",
        "it_3": "메뉴를 4종으로 줄였다.",
        "it_4": "• Hook: 1초 만에 반응 유도.",
        "it_5": "Project 2. 공동구매",
        "it_6": "업체 15곳을 비교했다.",
    }
    by_id = {
        "it_1": LineAssignment(id="it_1", slot_id="TASK.SUMMARY", episode="it_1"),
        "it_2": LineAssignment(id="it_2", slot_id="TASK.SUMMARY", episode="it_2"),
        "it_3": LineAssignment(id="it_3", slot_id="TASK.BASIC.EXECUTION", episode="it_2"),
        "it_4": LineAssignment(id="it_4", slot_id="TASK.BASIC.PURPOSE", episode="it_4"),
        "it_5": LineAssignment(id="it_5", slot_id="TASK.SUMMARY", episode="it_5"),
        "it_6": LineAssignment(id="it_6", slot_id="TASK.BASIC.EXECUTION", episode="it_5"),
    }

    assign_module._attach_to_projects(lines, list(lines), by_id)

    assert [by_id[i].episode for i in lines] == ["it_1"] * 4 + ["it_5"] * 2
    assert by_id["it_2"].slot_id == "TASK.BASIC.EXECUTION"
    assert [i for i in lines if by_id[i].slot_id == "TASK.SUMMARY"] == ["it_1", "it_5"]


@pytest.mark.asyncio
async def test_tree_merges_same_project_from_roadmap_and_repeated_headers():
    """목차("P2.")·본문("Project 2.")·반복 머리글로 갈라진 같은 프로젝트를 한 업무로 합친다."""
    catalog = await _catalog()
    state = await _template_state()
    lines = {
        "it_1": "P2. 굿즈 공동구매",
        "it_2": "원가 절감",
        "it_3": "Project 2. 학과 굿즈 공동구매",
        "it_4": "- 업체 15곳을 비교했다",
        "it_5": "Project 2. 학과 굿즈 공동구매",
        "it_6": "- 물량을 늘려 단가를 낮췄다",
    }
    assignments = {
        "it_1": {"slot_id": "TASK.SUMMARY", "episode": "it_1"},
        "it_2": {"slot_id": "TASK.BASIC.PURPOSE", "episode": "it_1"},
        "it_3": {"slot_id": "TASK.SUMMARY", "episode": "it_3"},
        "it_4": {"slot_id": "TASK.BASIC.EXECUTION", "episode": "it_3"},
        "it_5": {"slot_id": "TASK.SUMMARY", "episode": "it_5"},
        "it_6": {"slot_id": "TASK.BASIC.EXECUTION", "episode": "it_5"},
    }

    items = build_file_items(lines, assignments, state, catalog)

    texts = [item["text"] for item in items if item.get("text")]
    assert texts.count("Project 2. 학과 굿즈 공동구매") == 1
    assert "P2. 굿즈 공동구매" not in texts
    assert "업체 15곳을 비교했다\n물량을 늘려 단가를 낮췄다" in texts
    assert "원가 절감" in texts


# ===== 칸 배정 =====


class _FakeLlm:
    """스키마별로 정해 둔 응답을 돌려주는 LLM 대역. 프롬프트는 통과(dict 그대로)로 바꾼다."""

    def __init__(self, gate: DocumentGate, assign, verify=None):
        self.gate = gate
        self.assign = assign
        self.verify = verify

    def _gate(self, payload):
        # 경험 문서라고 답할 때는 실제 모델처럼 원문 문장을 근거로 든다.
        if self.gate.is_experience and not self.gate.evidence:
            longest = max(payload["document"].splitlines(), key=len)
            return self.gate.model_copy(update={"evidence": [longest]})
        return self.gate

    def with_structured_output(self, schema):
        if schema is DocumentGate:
            return RunnableLambda(self._gate)
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
        "requested_exclude_verify_prompt",
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
async def test_gate_without_grounded_evidence_is_not_experience(monkeypatch, passthrough_prompts):
    """관문이 경험이라고 해도 작성자가 한 일을 쓴 원문 문장을 못 대면 경험 문서가 아니다."""
    gate = DocumentGate(
        evidence=["5. lim x→∞ f(x)의 값을 구하시오."],
        is_experience=True,
        doc_type="문제집",
        reason="중간고사 대비 학습",
    )
    llm = _FakeLlm(gate, lambda _payload: AssignOutput(items=[]))
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["2026년 2학기 중간고사 대비", "5. lim x→∞ f(x)의 값을 구하시오."], await _catalog()
    )

    assert result.is_experience is False


def test_split_drops_private_use_math_glyphs():
    document = split_document("함수 \ue044\ue045 의 값은?")

    assert document.lines == ["함수  의 값은?"]


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
    async def fake_assign(_lines, _catalog, _message_lines=None):
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
    async def fake_assign(lines, _catalog, _message_lines=None):
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


# ===== 파일 + 채팅 메시지 =====


def test_message_split_keeps_request_phrase_for_instruction():
    document = split_document("2번 프로젝트만 정리해줘", drop_request_phrases=False)

    assert document.lines == ["2번 프로젝트만 정리해줘"]
    assert document.noise == []


@pytest.mark.asyncio
async def test_assign_applies_message_instruction_with_verification(
    monkeypatch, passthrough_prompts
):
    """메시지 지시는 INSTRUCTION, 범위 밖 파일 줄은 지시 확인을 거쳐 '사용자 요청'으로 뺀다."""
    seen_instructions: list[str] = []

    def assign(payload):
        if "ids" in payload:
            return AssignOutput(items=[])
        return AssignOutput(
            items=[
                LineAssignment(id="m_1", slot_id="INSTRUCTION"),
                LineAssignment(id="m_2", slot_id="DETAIL.ROLE"),
                LineAssignment(id="it_1", slot_id="LEARNING.GROWTH"),
                LineAssignment(id="it_2", slot_id="EXCLUDE", reason="사용자요청"),
            ]
        )

    def verify(payload):
        seen_instructions.append(payload.get("instructions", ""))
        return _VerifyOutput(items=[{"id": "it_2", "keep": False}])

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign, verify)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["데이터로 설득하는 법을 배웠다.", "2023년 축제 운영을 했다."],
        await _catalog(),
        ["배운 점만 정리해줘", "발표는 내가 맡았어"],
    )

    assert result.requested_excluded == ["2023년 축제 운영을 했다."]
    assert result.excluded == []
    assert set(result.assignments) == {"m_2", "it_1"}
    assert seen_instructions == ["배운 점만 정리해줘"]


@pytest.mark.asyncio
async def test_other_experience_is_kept_unless_user_asks(monkeypatch, passthrough_prompts):
    """다른 경험 줄은 무관 확인에서 남는다. 확인 모델이 후보 밖 줄까지 답해도 무시한다."""

    def assign(payload):
        if "ids" in payload:
            return AssignOutput(items=[LineAssignment(id="it_2", slot_id="TASK.BASIC.SUMMARY")])
        return AssignOutput(
            items=[
                LineAssignment(id="it_1", slot_id="LEARNING.GROWTH"),
                LineAssignment(id="it_2", slot_id="EXCLUDE", reason="다른경험"),
            ]
        )

    def verify(payload):
        return _VerifyOutput(items=[{"id": "it_1", "keep": False}, {"id": "it_2", "keep": True}])

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign, verify)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["데이터로 설득하는 법을 배웠다.", "2022년에는 카페 아르바이트를 했다."], await _catalog()
    )

    assert result.excluded == []
    assert result.requested_excluded == []
    assert set(result.assignments) == {"it_1", "it_2"}


@pytest.mark.asyncio
async def test_instruction_excludes_candidate_tagged_with_other_reason(
    monkeypatch, passthrough_prompts
):
    """지시가 있으면 이유가 '사용자요청'이 아닌 후보도 지시 확인을 받아 뺄 수 있다."""

    def assign(payload):
        if "ids" in payload:
            return AssignOutput(items=[])
        return AssignOutput(
            items=[
                LineAssignment(id="m_1", slot_id="INSTRUCTION"),
                LineAssignment(id="it_1", slot_id="LEARNING.GROWTH"),
                LineAssignment(id="it_2", slot_id="EXCLUDE", reason="다른경험"),
            ]
        )

    def verify(payload):
        if "instructions" in payload:
            return _VerifyOutput(items=[{"id": "it_2", "keep": False}])
        return _VerifyOutput(items=[{"id": "it_2", "keep": True}])

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign, verify)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["데이터로 설득하는 법을 배웠다.", "2022년에는 카페 아르바이트를 했다."],
        await _catalog(),
        ["카페 얘기는 빼줘"],
    )

    assert result.requested_excluded == ["2022년에는 카페 아르바이트를 했다."]
    assert result.excluded == []
    assert set(result.assignments) == {"it_1"}


@pytest.mark.asyncio
async def test_tree_skips_lines_already_in_activity():
    """같은 파일을 다시 올려도 이미 들어 있는 문장은 다시 넣지 않는다."""
    catalog = await _catalog()
    state = await _template_state()
    state["activity_tree_text"] = state["activity_tree_text"].replace(
        "(빈 블록 — 가이드: 전체 진행 기간은 언제부터 언제까지였나요?)",
        "기간: 2023.03 ~ 2023.06",
        1,
    )
    lines = {"it_1": "기간: 2023.03 ~ 2023.06", "it_2": "데이터로 설득하는 법을 배웠다."}
    assignments = {
        "it_1": {"slot_id": "DETAIL.PERIOD", "episode": None},
        "it_2": {"slot_id": "LEARNING.GROWTH", "episode": None},
    }

    items = build_file_items(lines, assignments, state, catalog)

    assert [item["text"] for item in items] == ["데이터로 설득하는 법을 배웠다."]


@pytest.mark.asyncio
async def test_section_scope_instruction_is_applied_by_code(monkeypatch, passthrough_prompts):
    """'문제해결만 정리해줘'는 모델이 구획 목록만 답하고, 다른 구획 파일 줄은 코드가 뺀다."""

    def assign(payload):
        return AssignOutput(
            items=[
                LineAssignment(id="m_1", slot_id="INSTRUCTION"),
                LineAssignment(id="it_1", slot_id="DETAIL.PERIOD"),
                LineAssignment(id="it_2", slot_id="PROBLEM_SOLVING.SUMMARY", episode="it_2"),
                LineAssignment(id="it_3", slot_id="PROBLEM_SOLVING.BASIC.PROBLEM", episode="it_2"),
            ],
            only_sections=["PROBLEM_SOLVING"],
        )

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["기간: 2023.03 ~ 2023.06", "1) 타깃 문제", "- 상황: 메시지가 넓었다."],
        await _catalog(),
        ["문제해결 부분만 정리해줘"],
    )

    assert result.requested_excluded == ["기간: 2023.03 ~ 2023.06"]
    assert set(result.assignments) == {"it_2", "it_3"}


@pytest.mark.asyncio
async def test_plain_request_message_does_not_trigger_requested_exclusion(
    monkeypatch, passthrough_prompts
):
    """메시지가 '정리해줘'뿐이면 모델이 '사용자요청'으로 뺀 줄도 무관 확인으로 돌린다."""

    def assign(payload):
        if "ids" in payload:
            return AssignOutput(items=[LineAssignment(id="it_1", slot_id="LEARNING.GROWTH")])
        return AssignOutput(
            items=[
                LineAssignment(id="m_1", slot_id="INSTRUCTION"),
                LineAssignment(id="it_1", slot_id="EXCLUDE", reason="사용자요청"),
            ],
            excluded_sections=["LEARNING"],
        )

    def verify(_payload):
        return _VerifyOutput(items=[{"id": "it_1", "keep": True}])

    llm = _FakeLlm(DocumentGate(is_experience=True, doc_type="경험", reason="ok"), assign, verify)
    monkeypatch.setattr(assign_module, "get_experience_map_llm", lambda **_: llm)

    result = await assign_module.assign_lines(
        ["데이터로 설득하는 법을 배웠다."], await _catalog(), ["정리해줘"]
    )

    assert result.requested_excluded == []
    assert result.assignments["it_1"]["slot_id"] == "LEARNING.GROWTH"


@pytest.mark.asyncio
async def test_tree_skips_refined_duplicate_of_existing_block():
    """활동 블록은 정제된 문장이라 글자가 달라도, 원문 글자쌍이 대부분 남아 있으면 중복이다."""
    catalog = await _catalog()
    state = await _template_state()
    state["activity_tree_text"] = state["activity_tree_text"].replace(
        "(빈 블록 — 가이드: 이 경험을 통해 새롭게 배우거나 성장한 점은 무엇이며, 향후 어떻게 활용할 계획인가요?)",
        "데이터에 기반해 논리를 전개하는 기획 역량이 성장했다.",
        1,
    )
    lines = {"it_1": "- 성장한 부분: 데이터에 기반하여 논리를 전개하는 기획 역량이 성장했다"}
    assignments = {"it_1": {"slot_id": "LEARNING.GROWTH", "episode": None}}

    assert build_file_items(lines, assignments, state, catalog) == []
