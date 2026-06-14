"""시각화 생성 LLM 어댑터 테스트."""

import json
from dataclasses import dataclass

import pytest

from features.visualization.agents import (
    LLMContentFillGenerator,
    LLMSlideChangeGenerator,
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


def _last_fill_prompt_payload(llm: FakeLLM) -> dict[str, object]:
    """마지막 fill 생성 호출의 HumanMessage JSON payload를 반환한다."""
    return json.loads(llm.messages[-1][1].content.rsplit("\n", maxsplit=1)[1])


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
    assert plan.to_blob()["selected_slides"][4]["reason"] == "chart_A 선택"

    prompt_payload = json.loads(llm.messages[0][1].content)
    assert prompt_payload["selection_signals"]["has_numeric_data"] is True
    assert any(slide["id"] == "chart_A" for slide in prompt_payload["source_slides"])


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


def test_content_fill_generator_does_not_require_optional_decorative_slot() -> None:
    """optional/decorative slot 은 누락되어도 fill 생성 실패로 보지 않는다."""
    llm = FakeLLM([{"fills": {"2": {"action": "text", "text": "핵심 요약"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="요약",
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 20,
                "kind": "text",
                "editable": True,
                "required": True,
                "allowed_actions": ["text", "remove"],
            },
            {
                "shape_id": "9",
                "kind": "decorative",
                "editable": False,
                "required": False,
                "allowed_actions": [],
            },
        ],
    )

    assert fills == {"2": {"action": "text", "text": "핵심 요약"}}
    system_prompt = llm.messages[0][0].content
    prompt_payload = _last_fill_prompt_payload(llm)
    assert "required=false" in system_prompt
    assert "성과 지표" in system_prompt
    assert prompt_payload["slots"][1]["editable"] is False


def test_content_fill_prompt_includes_capacity_hint_payload() -> None:
    """LLM Call #2 payload에는 slot capacity hint와 예시 사용 안내가 포함된다."""
    llm = FakeLLM([{"fills": {"26": {"action": "text", "text": "Folioo AI"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    generator.create_fills(
        content_brief="프로젝트 이름을 작성",
        slots=[
            {
                "shape_id": "26",
                "kind": "text",
                "editable": True,
                "required": True,
                "placeholder_text": "프로젝트명",
                "example_text": "Folioo AI",
                "example_char_count": 9,
                "example_line_count": 1,
                "max_lines": 1,
                "nowrap": True,
                "fit_policy": "basic_text_area",
                "allowed_actions": ["text"],
            }
        ],
    )

    system_prompt = llm.messages[0][0].content
    prompt_payload = _last_fill_prompt_payload(llm)
    slot_payload = prompt_payload["slots"][0]
    assert "example_text 는 복사할 정답이 아니라" in system_prompt
    assert prompt_payload["slot_guidance"]["example_text"] == (
        "복사 대상이 아니라 형식, 길이, 줄 수 참고용입니다."
    )
    assert slot_payload["placeholder_text"] == "프로젝트명"
    assert slot_payload["example_text"] == "Folioo AI"
    assert slot_payload["example_line_count"] == 1
    assert slot_payload["max_lines"] == 1
    assert slot_payload["nowrap"] is True
    assert "length_hint" in slot_payload


def test_content_fill_prompt_uses_inline_label_one_line_length_hint() -> None:
    """짧은 chip 예시는 inline_label_group 한 줄 label 길이 힌트로 전달된다."""
    llm = FakeLLM([{"fills": {"26": {"action": "text", "text": "OpenAI API"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    generator.create_fills(
        content_brief="사용 기술을 chip 문구로 작성",
        slots=[
            {
                "shape_id": "26",
                "kind": "text",
                "editable": True,
                "required": True,
                "placeholder_text": "사용 기술",
                "example_text": "OpenAI API",
                "example_line_count": 1,
                "max_lines": 1,
                "nowrap": True,
                "layout_type": "inline_label_group",
                "fit_policy": "resize_label",
                "allowed_actions": ["text", "remove"],
            }
        ],
    )

    slot_payload = _last_fill_prompt_payload(llm)["slots"][0]
    assert slot_payload["example_text"] == "OpenAI API"
    assert slot_payload["length_hint"].startswith("짧은 chip/label 문구")
    assert "한 줄" in slot_payload["length_hint"]
    assert "줄바꿈하지 말고" in slot_payload["length_hint"]


def test_content_fill_prompt_does_not_add_text_capacity_hint_to_chart_slot() -> None:
    """chart slot 에는 text length hint를 붙여 action 계약을 흐리지 않는다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "8": {
                        "action": "chart",
                        "data": {
                            "categories": ["전", "후"],
                            "series": [{"name": "전환율", "values": [10, 42]}],
                        },
                    }
                }
            }
        ]
    )
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="전환율 차트를 작성",
        slots=[
            {
                "shape_id": "8",
                "kind": "chart",
                "editable": True,
                "required": True,
                "allowed_actions": ["chart"],
            }
        ],
    )

    assert fills["8"]["action"] == "chart"
    slot_payload = _last_fill_prompt_payload(llm)["slots"][0]
    assert slot_payload["allowed_actions"] == ["chart"]
    assert "length_hint" not in slot_payload
    assert "max_lines" not in slot_payload
    assert "nowrap" not in slot_payload


def test_content_fill_generator_accepts_v2_capacity_slot_without_role_hint() -> None:
    """role_hint 없는 v2 capacity slot 도 prompt payload 와 fill 검증에서 그대로 통과한다."""
    llm = FakeLLM([{"fills": {"26": {"action": "text", "text": "OpenAI API"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="사용 기술을 간결하게 작성",
        slots=[
            {
                "shape_id": "26",
                "kind": "text",
                "editable": True,
                "required": True,
                "placeholder_text": "사용 기술",
                "example_text": "OpenAI API",
                "example_char_count": 10,
                "example_line_count": 1,
                "output_text_color": "#000000",
                "min_font_pt": 10.0,
                "max_font_pt": 10.0,
                "max_lines": 1,
                "nowrap": True,
                "fit_policy": "basic_text_area",
                "allowed_actions": ["text", "remove"],
            },
        ],
    )

    assert fills == {"26": {"action": "text", "text": "OpenAI API"}}
    prompt_payload = _last_fill_prompt_payload(llm)
    slot_payload = prompt_payload["slots"][0]
    assert "role_hint" not in slot_payload
    assert slot_payload["placeholder_text"] == "사용 기술"
    assert slot_payload["example_text"] == "OpenAI API"
    assert "일반 텍스트 영역" in slot_payload["length_hint"]
    assert "최대 1줄" in slot_payload["length_hint"]


def test_content_fill_prompt_falls_back_for_legacy_slot_without_capacity_fields() -> None:
    """legacy slot 에 capacity 필드가 없어도 prompt payload를 안전하게 만든다."""
    llm = FakeLLM([{"fills": {"2": {"action": "text", "text": "Folioo AI"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="프로젝트명 작성",
        slots=[
            {
                "shape_id": "2",
                "font_size_pt": 20,
                "kind": "text",
                "current_text": "여기에 프로젝트명",
            }
        ],
    )

    assert fills == {"2": {"action": "text", "text": "Folioo AI"}}
    slot_payload = _last_fill_prompt_payload(llm)["slots"][0]
    assert slot_payload["placeholder_text"] == "여기에 프로젝트명"
    assert slot_payload["example_text"] is None
    assert slot_payload["example_line_count"] is None
    assert slot_payload["max_lines"] == 1
    assert slot_payload["nowrap"] is True
    assert "placeholder_text" in slot_payload["length_hint"]


def test_content_fill_generator_accepts_v2_capacity_remove_without_role_hint() -> None:
    """role_hint 없는 v2 capacity slot 에서 remove action 도 계약대로 허용한다."""
    llm = FakeLLM([{"fills": {"26": {"action": "remove"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="사용 기술이 없으면 제거",
        slots=[
            {
                "shape_id": "26",
                "kind": "text",
                "editable": True,
                "required": False,
                "placeholder_text": "사용 기술",
                "example_text": "OpenAI API",
                "example_char_count": 10,
                "example_line_count": 1,
                "output_text_color": "#000000",
                "min_font_pt": 10.0,
                "max_font_pt": 10.0,
                "max_lines": 1,
                "nowrap": True,
                "fit_policy": "basic_text_area",
                "allowed_actions": ["text", "remove"],
            },
        ],
    )

    assert fills == {"26": {"action": "remove"}}


def test_content_fill_generator_rejects_fill_for_non_editable_slot() -> None:
    """LLM 이 비편집 slot 을 채우려 하면 적용 전에 거부한다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {"action": "text", "text": "핵심 요약"},
                    "9": {"action": "text", "text": "장식 오염"},
                }
            }
        ]
    )
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="비편집"):
        generator.create_fills(
            content_brief="요약",
            slots=[
                {"shape_id": "2", "font_size_pt": 20, "kind": "text"},
                {"shape_id": "9", "kind": "decorative", "editable": False, "required": False},
            ],
        )


def test_content_fill_generator_requires_chart_data_for_chart_slot() -> None:
    """chart slot 은 chart action 과 data 구조가 함께 있어야 한다."""
    llm = FakeLLM([{"fills": {"8": {"action": "chart"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="data 객체"):
        generator.create_fills(
            content_brief="성과 지표 차트",
            slots=[
                {
                    "shape_id": "8",
                    "kind": "chart",
                    "required": True,
                    "allowed_actions": ["chart"],
                }
            ],
        )


def test_content_fill_generator_accepts_valid_chart_fill() -> None:
    """chart fill 은 categories 와 series values 길이를 검증한 뒤 보존한다."""
    chart_data = {
        "categories": ["전", "후"],
        "series": [{"name": "전환율", "values": [12, 42]}],
    }
    llm = FakeLLM([{"fills": {"8": {"action": "chart", "data": chart_data}}}])
    generator = LLMContentFillGenerator(llm=llm)

    fills = generator.create_fills(
        content_brief="성과 지표 차트",
        slots=[
            {
                "shape_id": "8",
                "kind": "chart",
                "required": True,
                "allowed_actions": ["chart"],
            }
        ],
    )

    assert fills == {"8": {"action": "chart", "data": chart_data}}


def test_content_fill_generator_rejects_invalid_action_with_pydantic_schema() -> None:
    """Fill action 은 Pydantic schema 단계에서 허용값으로 제한된다."""
    llm = FakeLLM([{"fills": {"2": {"action": "script", "text": "오류"}}}])
    generator = LLMContentFillGenerator(llm=llm)

    with pytest.raises(ValueError, match="action"):
        generator.create_fills(
            content_brief="요약",
            slots=[{"shape_id": "2", "font_size_pt": 20, "kind": "text"}],
        )


def test_slide_change_generator_preserves_text_for_style_only_request() -> None:
    """스타일 요청에서는 LLM 이 text 를 줘도 기존 텍스트를 보존하고 폰트 범위를 clamp 한다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "임의로 바뀐 제목",
                        "font_size_override": 72,
                    }
                }
            }
        ]
    )
    generator = LLMSlideChangeGenerator(llm=llm)

    changes = generator.create_changes(
        user_request="제목 크기 키워줘",
        slots=[{"shape_id": "2", "current_text": "기존 제목", "font_size_pt": 20, "kind": "text"}],
        current_fills={"2": {"action": "text", "text": "현재 제목"}},
    )

    assert changes == {"2": {"action": "text", "text": "현재 제목", "font_size_override": 48.0}}


def test_slide_change_generator_does_not_treat_size_change_as_text_change() -> None:
    """크기 변경 같은 스타일 요청은 '변경' 표현이 있어도 기존 텍스트를 보존한다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "임의로 바뀐 제목",
                        "font_size_override": 24,
                    }
                }
            }
        ]
    )
    generator = LLMSlideChangeGenerator(llm=llm)

    changes = generator.create_changes(
        user_request="제목 크기를 24pt로 변경해줘",
        slots=[{"shape_id": "2", "current_text": "기존 제목", "font_size_pt": 20, "kind": "text"}],
        current_fills={"2": {"action": "text", "text": "현재 제목"}},
    )

    assert changes["2"]["text"] == "현재 제목"
    assert changes["2"]["font_size_override"] == 24.0


@pytest.mark.parametrize("user_request", ["제목을 수정해줘", "내용 변경해줘"])
def test_slide_change_generator_allows_generic_text_change_request(user_request: str) -> None:
    """수정/변경 요청은 스타일 맥락이 없으면 텍스트 변경으로 처리한다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "핵심 성과 요약",
                    }
                }
            }
        ]
    )
    generator = LLMSlideChangeGenerator(llm=llm)

    changes = generator.create_changes(
        user_request=user_request,
        slots=[{"shape_id": "2", "current_text": "기존 제목", "font_size_pt": 20, "kind": "text"}],
        current_fills={"2": {"action": "text", "text": "기존 제목"}},
    )

    assert changes["2"]["text"] == "핵심 성과 요약"


def test_slide_change_generator_allows_explicit_text_request() -> None:
    """표현 변경 요청처럼 명시적인 텍스트 수정은 요청 도형에 한해 허용한다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "핵심 성과 요약",
                        "font_size_override": 18,
                    }
                }
            }
        ]
    )
    generator = LLMSlideChangeGenerator(llm=llm)

    changes = generator.create_changes(
        user_request="제목 표현을 더 간결하게 바꿔줘",
        slots=[{"shape_id": "2", "current_text": "기존 제목", "font_size_pt": 20, "kind": "text"}],
        current_fills={"2": {"action": "text", "text": "기존 제목"}},
    )

    assert changes["2"]["text"] == "핵심 성과 요약"
    assert changes["2"]["font_size_override"] == 18.0


def test_slide_change_generator_allows_chart_adjacent_text_style_request() -> None:
    """차트 주변 텍스트 스타일 요청은 chart 데이터 변경으로 오인해 사전 거부하지 않는다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "임의로 바뀐 차트 제목",
                        "font_size_override": 28,
                    }
                }
            }
        ]
    )
    generator = LLMSlideChangeGenerator(llm=llm)

    changes = generator.create_changes(
        user_request="차트 제목 크기 키워줘",
        slots=[
            {"shape_id": "2", "current_text": "기존 차트 제목", "font_size_pt": 20, "kind": "text"}
        ],
        current_fills={"2": {"action": "text", "text": "현재 차트 제목"}},
    )

    assert changes["2"]["text"] == "현재 차트 제목"
    assert changes["2"]["font_size_override"] == 28.0


def test_slide_change_generator_allows_graph_caption_text_request() -> None:
    """그래프 설명 문구 요청은 인접 텍스트 slot 변경으로 허용한다."""
    llm = FakeLLM(
        [
            {
                "fills": {
                    "2": {
                        "action": "text",
                        "text": "전환율 개선 설명",
                    }
                }
            }
        ]
    )
    generator = LLMSlideChangeGenerator(llm=llm)

    changes = generator.create_changes(
        user_request="그래프 설명 문구를 바꿔줘",
        slots=[{"shape_id": "2", "current_text": "기존 설명", "font_size_pt": 14, "kind": "text"}],
        current_fills={"2": {"action": "text", "text": "기존 설명"}},
    )

    assert changes["2"]["text"] == "전환율 개선 설명"


def test_slide_change_generator_rejects_chart_data_request_before_llm() -> None:
    """차트 데이터 변경 요청은 현재 Phase 2 지원 범위 밖이므로 LLM 호출 전에 거부한다."""
    llm = FakeLLM([])
    generator = LLMSlideChangeGenerator(llm=llm)

    with pytest.raises(ValueError, match="차트 데이터 변경"):
        generator.create_changes(
            user_request="차트 수치를 변경해줘",
            slots=[{"shape_id": "2", "current_text": "기존 차트 제목", "kind": "text"}],
            current_fills={"2": {"action": "text", "text": "기존 차트 제목"}},
        )

    assert llm.messages == []


def test_slide_change_generator_rejects_unsupported_color_request_before_llm() -> None:
    """현재 구현 범위 밖인 색상 변경 요청은 LLM 호출 전에 명시적으로 거부한다."""
    llm = FakeLLM([])
    generator = LLMSlideChangeGenerator(llm=llm)

    with pytest.raises(ValueError, match="지원하지 않는 수정 범위"):
        generator.create_changes(
            user_request="제목 색을 빨간색으로 바꿔줘",
            slots=[{"shape_id": "2", "current_text": "기존 제목", "kind": "text"}],
            current_fills={"2": {"action": "text", "text": "기존 제목"}},
        )

    assert llm.messages == []


def test_slide_change_generator_rejects_chart_shape_changes() -> None:
    """Phase 2 일반 재생성은 chart slot 변경을 지원하지 않는다."""
    llm = FakeLLM([{"fills": {"8": {"action": "chart", "data": {"series": []}}}}])
    generator = LLMSlideChangeGenerator(llm=llm)

    with pytest.raises(ValueError, match="텍스트 도형만"):
        generator.create_changes(
            user_request="이 영역 수치를 업데이트해줘",
            slots=[{"shape_id": "8", "kind": "chart", "current_text": "기존 차트"}],
            current_fills={"8": {"action": "chart", "data": {"series": []}}},
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


def test_rule_prefilter_uses_description_and_best_for_metric_signals() -> None:
    """category 뿐 아니라 description/best_for 의 metric 신호도 필터링 기준으로 사용한다."""
    slides = parse_template_metadata(_template_meta(include_visual=True, include_metric=True))

    without_numbers = prefilter_source_slides(
        portfolio_text="사용자 리서치와 문제 정의를 중심으로 진행했습니다.",
        source_slides=slides,
    )
    with_numbers = prefilter_source_slides(
        portfolio_text="전환율 42% 개선과 KPI 추적 결과를 정리했습니다.",
        source_slides=slides,
    )

    assert "metric_A" not in {slide.source_slide_id for slide in without_numbers}
    assert {"chart_A", "metric_A"} <= {slide.source_slide_id for slide in with_numbers}


def test_slide_plan_output_error_message_lists_supported_keys() -> None:
    """slide_plan/selected_slides 둘 다 없을 때 허용 입력을 안내한다."""
    with pytest.raises(ValueError, match="selected_slides"):
        SlidePlanOutput.model_validate({"total_slides": 7})


def test_parse_template_metadata_derives_slide_filenames() -> None:
    """meta.json 의 0-based slide_index 에서 slideN.xml 파일명을 유도한다."""
    slides = parse_template_metadata(_template_meta())

    assert slides[0].slide_filename == "slide1.xml"
    assert slides[-1].slide_filename == "slide7.xml"


@pytest.mark.parametrize("metadata", [{"slides": []}, {"schema_version": 1, "slides": []}])
def test_parse_template_metadata_rejects_non_v2_schema(metadata: dict[str, object]) -> None:
    """schema_version 누락 또는 v2가 아닌 meta.json은 런타임에서 즉시 거부한다."""
    with pytest.raises(ValueError, match="schema_version은 2"):
        parse_template_metadata(metadata)


def test_parse_template_metadata_supports_v2_runtime_slides() -> None:
    """v2 compiler 산출물의 runtime_slides를 기본 SourceSlide 후보로 변환한다."""
    slides = parse_template_metadata(
        {
            "schema_version": 2,
            "template_id": "ppt-v3",
            "runtime_slides": [
                {
                    "slide_index": 1,
                    "slide_number": 2,
                    "slide_filename": "slide2.xml",
                    "slide_part": "ppt/slides/slide2.xml",
                },
                {
                    "slide_index": 3,
                    "slide_number": 4,
                    "slide_filename": "slide4.xml",
                    "slide_part": "ppt/slides/slide4.xml",
                },
            ],
        }
    )

    assert [slide.source_slide_id for slide in slides] == ["slide2", "slide4"]
    assert [slide.category for slide in slides] == ["cover", "closing"]
    assert [slide.slide_filename for slide in slides] == ["slide2.xml", "slide4.xml"]


def test_parse_template_metadata_prefers_runtime_slide_filename() -> None:
    """runtime_slides는 slide_index 유도값 대신 compiler가 저장한 실제 slide part를 쓴다."""
    slides = parse_template_metadata(
        {
            "schema_version": 2,
            "template_id": "ppt-v3",
            "runtime_slides": [
                {
                    "slide_index": 10,
                    "slide_number": 11,
                    "slide_filename": "slide2.xml",
                    "slide_part": "ppt/slides/slide2.xml",
                },
                {
                    "slide_index": 11,
                    "slide_number": 12,
                    "slide_part": "ppt/slides/slide99.xml",
                },
            ],
        }
    )

    assert [slide.source_slide_id for slide in slides] == ["slide11", "slide12"]
    assert [slide.slide_filename for slide in slides] == ["slide2.xml", "slide99.xml"]


def test_slide_plan_generator_accepts_compiler_runtime_slides_only() -> None:
    """compiler-shaped runtime_slides 만 있어도 fallback category로 7장 plan을 통과시킨다."""
    llm = FakeLLM(
        [
            {
                "total_slides": 7,
                "slide_plan": [
                    _plan_item(1, "slide2"),
                    _plan_item(2, "slide4"),
                    _plan_item(3, "slide6"),
                    _plan_item(4, "slide8"),
                    _plan_item(5, "slide10"),
                    _plan_item(6, "slide12"),
                    _plan_item(7, "slide14"),
                ],
            }
        ]
    )
    generator = LLMSlidePlanGenerator(llm=llm)

    plan = generator.create_plan(
        portfolio_text="Folioo 프로젝트에서 전환율을 42% 개선했습니다.",
        template_metadata={
            "schema_version": 2,
            "template_id": "ppt-v3",
            "runtime_slides": [
                {
                    "slide_index": index,
                    "slide_number": index + 1,
                    "slide_filename": f"slide{index + 1}.xml",
                    "slide_part": f"ppt/slides/slide{index + 1}.xml",
                }
                for index in (1, 3, 5, 7, 9, 11, 13)
            ],
        },
    )

    assert plan.total_slides == 7
    assert [slide.category for slide in plan.selected_slides] == [
        "cover",
        "toc",
        "overview",
        "problem",
        "process",
        "outcome",
        "closing",
    ]
    assert [slide.slide_filename for slide in plan.selected_slides] == [
        "slide2.xml",
        "slide4.xml",
        "slide6.xml",
        "slide8.xml",
        "slide10.xml",
        "slide12.xml",
        "slide14.xml",
    ]


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
    include_metric: bool = False,
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
    if include_metric:
        slides.insert(
            -1,
            {
                "slide_index": len(slides),
                "id": "metric_A",
                "category": "text",
                "description": "KPI metric dashboard 설명",
                "best_for": "전환율, 매출, 성과 지표 비교에 적합",
            },
        )
        for index, slide in enumerate(slides):
            slide["slide_index"] = index
    return {
        "schema_version": 2,
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
