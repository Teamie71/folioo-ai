"""시각화 생성 LLM 어댑터 테스트."""

from dataclasses import dataclass

import pytest

from features.visualization.agents import (
    LLMContentFillGenerator,
    LLMSlidePlanGenerator,
    parse_template_metadata,
    prefilter_source_slides,
)


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


def test_content_fill_generator_rejects_unknown_shape_id() -> None:
    """LLM 이 제공받지 않은 shape_id 를 쓰면 적용 전에 거부한다."""
    llm = FakeLLM([{"fills": {"999": {"action": "text", "text": "오류"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="shape_id"):
        generator.create_fills(
            content_brief="요약",
            slots=[{"shape_id": "2", "font_size_pt": 20, "kind": "text"}],
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
