"""시각화 생성 LLM 어댑터 테스트."""

from dataclasses import dataclass

import pytest

from features.visualization.agents import (
    LLMContentFillGenerator,
    LLMSlidePlanGenerator,
    parse_template_metadata,
    prefilter_source_slides,
)
from features.visualization.agents.schemas import SlidePlanOutput


@dataclass
class LLMResponse:
    """테스트용 LLM 응답."""

    content: object


class FakeLLM:
    """순서대로 응답하는 LLM 대역."""

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.messages: list[list[object]] = []
        self.model = "fake-model"

    def invoke(self, messages: list[object]) -> LLMResponse:
        self.messages.append(messages)
        if not self.responses:
            raise AssertionError("LLM 응답이 소진되었습니다.")
        return LLMResponse(self.responses.pop(0))


def test_slide_plan_generator_validates_required_rules() -> None:
    """cover/closing 포함, 7~12장, 연속 카테고리 회피 plan 을 생성한다."""
    llm = FakeLLM(
        [
            {
                "total_slides": 7,
                "slide_plan": [
                    _plan_item(1, "cover_A"),
                    _plan_item(2, "overview_A"),
                    _plan_item(3, "process_A"),
                    _plan_item(4, "outcome_A"),
                    _plan_item(5, "chart_A"),
                    _plan_item(6, "text_A"),
                    _plan_item(7, "closing_A"),
                ],
            }
        ]
    )
    generator = LLMSlidePlanGenerator(llm=llm)

    plan = generator.create_plan(
        portfolio_text="Folioo 프로젝트에서 전환율을 42% 개선했습니다.",
        template_metadata=_template_meta(),
    )

    assert plan.total_slides == 7
    assert plan.llm_model == "fake-model"
    assert plan.selected_slides[0].source_slide_id == "cover_A"
    assert plan.selected_slides[-1].category == "closing"
    assert plan.selected_slides[-1].slide_filename == "slide7.xml"


def test_slide_plan_generator_rejects_consecutive_categories() -> None:
    """같은 카테고리를 연속 선택한 LLM 응답은 거부한다."""
    llm = FakeLLM(
        [
            {
                "slide_plan": [
                    _plan_item(1, "cover_A"),
                    _plan_item(2, "overview_A"),
                    _plan_item(3, "overview_B"),
                    _plan_item(4, "process_A"),
                    _plan_item(5, "outcome_A"),
                    _plan_item(6, "text_A"),
                    _plan_item(7, "closing_A"),
                ]
            }
        ]
    )
    generator = LLMSlidePlanGenerator(llm=llm)

    with pytest.raises(ValueError, match="연속"):
        generator.create_plan(
            portfolio_text="프로젝트 내용",
            template_metadata=_template_meta(include_overview_b=True),
        )


@pytest.mark.parametrize(
    ("source_slide_ids", "total_slides", "match"),
    [
        (
            ["cover_A", "overview_A", "process_A", "outcome_A", "chart_A", "text_A", "closing_A"],
            8,
            "total_slides",
        ),
        (
            [
                "cover_A",
                "overview_A",
                "process_A",
                "overview_A",
                "outcome_A",
                "text_A",
                "closing_A",
            ],
            None,
            "source_slide_id",
        ),
    ],
)
def test_slide_plan_generator_rejects_structural_contract_errors(
    source_slide_ids: list[str],
    total_slides: int | None,
    match: str,
) -> None:
    """LLM slide_plan 의 항목 수와 source slide 중복을 검증한다."""
    response: dict[str, object] = {
        "slide_plan": [
            _plan_item(index, source_slide_id)
            for index, source_slide_id in enumerate(source_slide_ids, start=1)
        ]
    }
    if total_slides is not None:
        response["total_slides"] = total_slides
    llm = FakeLLM([response])
    generator = LLMSlidePlanGenerator(llm=llm)

    with pytest.raises(ValueError, match=match):
        generator.create_plan(
            portfolio_text="프로젝트 내용",
            template_metadata=_template_meta(),
        )


def test_content_fill_generator_clamps_font_size_to_spec_floor() -> None:
    """font_size_override 는 원본 60%와 10pt 하한을 지킨다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "요약 텍스트",
                        "font_size_override": 8,
                    }
                }
            }
        ]
    )
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="요약",
        slots=[{"shape_id": "2", "font_size_pt": 20, "kind": "text"}],
    )

    assert fills["2"]["font_size_override"] == 12


def test_content_fill_generator_keeps_global_font_floor_when_base_is_small() -> None:
    """원본 폰트가 작아도 font_size_override 는 10pt 미만으로 내려가지 않는다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "작은 텍스트",
                        "font_size_override": 1,
                    }
                }
            }
        ]
    )
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="요약",
        slots=[{"shape_id": "2", "font_size_pt": 8, "kind": "text"}],
    )

    assert fills["2"]["font_size_override"] == 10


def test_content_fill_generator_rejects_unknown_shape_id() -> None:
    """LLM 이 제공받지 않은 shape_id 를 쓰면 적용 전에 거부한다."""
    llm = FakeLLM([{"fills": {"999": {"action": "text", "text": "오류"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="shape_id"):
        generator.create_fills(
            content_brief="요약",
            slots=[{"shape_id": "2", "font_size_pt": 20, "kind": "text"}],
        )


def test_content_fill_generator_rejects_missing_slot_fill() -> None:
    """LLM 이 제공받은 slot 일부를 누락하면 템플릿 문구 노출 방지를 위해 거부한다."""
    llm = FakeLLM([{"fills": {"2": {"action": "text", "text": "요약"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="누락"):
        generator.create_fills(
            content_brief="요약",
            slots=[
                {"shape_id": "2", "font_size_pt": 20, "kind": "text"},
                {"shape_id": "3", "font_size_pt": 18, "kind": "text"},
            ],
        )


def test_content_fill_generator_rejects_invalid_action_with_pydantic_schema() -> None:
    """Fill action 은 Pydantic schema 단계에서 허용값으로 제한된다."""
    llm = FakeLLM([{"fills": {"2": {"action": "script", "text": "오류"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="action"):
        generator.create_fills(
            content_brief="요약",
            slots=[{"shape_id": "2", "font_size_pt": 20, "kind": "text"}],
        )


def test_rule_prefilter_excludes_chart_and_visual_without_signals() -> None:
    """수치/이미지 신호가 없으면 chart/visual 후보를 사전 제외한다."""
    slides = parse_template_metadata(_template_meta(include_visual=True))

    filtered = prefilter_source_slides(
        portfolio_text="사용자 리서치와 문제 정의를 중심으로 진행했습니다.",
        source_slides=slides,
    )

    assert {slide.category for slide in filtered} == {
        "cover",
        "toc",
        "overview",
        "problem",
        "process",
        "outcome",
        "text",
        "closing",
    }


def test_rule_prefilter_keeps_chart_and_visual_with_signals() -> None:
    """수치/이미지 신호가 있으면 chart/visual 후보를 유지한다."""
    slides = parse_template_metadata(_template_meta(include_visual=True))

    filtered = prefilter_source_slides(
        portfolio_text="전환율 42% 개선과 앱 화면 스크린샷을 포함했습니다.",
        source_slides=slides,
    )

    assert "chart" in {slide.category for slide in filtered}
    assert "visual" in {slide.category for slide in filtered}


def test_slide_plan_output_error_message_lists_supported_keys() -> None:
    """slide_plan/selected_slides 둘 다 없을 때 허용 입력을 안내한다."""
    with pytest.raises(ValueError, match="selected_slides"):
        SlidePlanOutput.model_validate({"total_slides": 7})


def test_parse_template_metadata_derives_slide_filenames() -> None:
    """meta.json 의 0-based slide_index 에서 slideN.xml 파일명을 유도한다."""
    slides = parse_template_metadata(_template_meta())

    assert slides[0].slide_filename == "slide1.xml"
    assert slides[-1].slide_filename == "slide7.xml"


def _plan_item(order: int, source_slide_id: str) -> dict[str, object]:
    return {
        "order": order,
        "selected_slide_id": source_slide_id,
        "reason": f"{source_slide_id} 선택",
        "content_brief": f"{order}번 슬라이드 내용",
    }


def _template_meta(
    *,
    include_overview_b: bool = False,
    include_visual: bool = False,
) -> dict[str, object]:
    slides = [
        _meta_slide(0, "cover_A", "cover"),
        _meta_slide(1, "overview_A", "overview"),
    ]
    if include_overview_b:
        slides.append(_meta_slide(2, "overview_B", "overview"))
        offset = 1
    else:
        offset = 0
    slides.extend(
        [
            _meta_slide(2 + offset, "process_A", "process"),
            _meta_slide(3 + offset, "outcome_A", "outcome"),
            _meta_slide(4 + offset, "chart_A", "chart"),
            _meta_slide(5 + offset, "text_A", "text"),
            _meta_slide(6 + offset, "closing_A", "closing"),
        ]
    )
    if include_visual:
        slides.insert(2 + offset, _meta_slide(len(slides), "toc_A", "toc"))
        slides.insert(3 + offset, _meta_slide(len(slides), "problem_A", "problem"))
        slides.insert(-1, _meta_slide(len(slides), "visual_A", "visual"))
        for index, slide in enumerate(slides):
            slide["slide_index"] = index
    return {
        "template_id": "blue",
        "template_file": "template.pptx",
        "slides": slides,
    }


def _meta_slide(index: int, slide_id: str, category: str) -> dict[str, object]:
    return {
        "slide_index": index,
        "id": slide_id,
        "category": category,
        "description": f"{slide_id} 설명",
        "best_for": f"{slide_id} 적합",
    }
