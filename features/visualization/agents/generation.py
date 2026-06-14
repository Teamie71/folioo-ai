"""Phase 1 초기 생성용 slide plan/fill LLM 어댑터."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from common.llm.client import get_llm_uncached
from features.visualization.agents.schemas import FillPayloadOutput, SlidePlanOutput
from features.visualization.templates import require_template_v2_metadata

_FALLBACK_RUNTIME_CATEGORIES = (
    "toc",
    "overview",
    "problem",
    "process",
    "outcome",
    "chart",
    "visual",
    "text",
)

_PLAN_SYSTEM_PROMPT = """포트폴리오 PPT 구성을 설계하는 전문가입니다.
응답은 반드시 JSON 객체 하나로만 작성하세요.
규칙:
- 총 7~12장
- cover 1장과 closing 1장을 반드시 포함
- 같은 category 를 연속 배치하지 않음
- 각 슬라이드는 Source Slide 풀의 id 중 하나만 선택
- 각 슬라이드에는 content_brief 와 reason 을 작성
"""

_FILL_SYSTEM_PROMPT = """PPTX 슬라이드 Slot 을 채우는 편집 데이터 생성기입니다.
응답은 반드시 JSON 객체 하나로만 작성하세요.
스키마: {"fills": {"shape_id": {"action": "text"|"remove"|"chart", "text": string, "font_size_override": number|null, "is_title": boolean|null, "data": object|null}}}
지침:
- 임의 코드나 XML 을 만들지 말고 fills 데이터만 산출하세요.
- provided slots 중 editable=true 이고 required=true 인 shape_id 만 필수로 채우세요.
- required=false, editable=false, kind=decorative/background/layout slot 은 필수 채움 대상이 아닙니다.
- 제공된 shape_id 만 key 로 사용하고, 각 slot 의 allowed_actions 범위 안에서만 action 을 선택하세요.
- chart slot 은 action=chart 와 data.categories, data.series[].values 를 함께 제공하세요.
- example_text 는 복사할 정답이 아니라 형식, 길이, 줄 수 참고용입니다.
- placeholder_text, max_lines, nowrap, length_hint 를 우선해 slot 용량에 맞는 텍스트를 작성하세요.
- 텍스트가 길면 먼저 폰트를 줄이되 원본의 60% 미만이나 10pt 미만으로 내리지 마세요.
- 텍스트 요약이 필요하면 고유명사, 수치, 기술 스택, 성과 지표를 보존하세요.
"""

_REGENERATE_SYSTEM_PROMPT = """PPTX 단일 슬라이드 수정 요청을 fill 변경 지시로 해석하는 편집자입니다.
응답은 반드시 JSON 객체 하나로만 작성하세요.
스키마: {"fills": {"shape_id": {"action": "text", "text": string|null, "font_size_override": number|null, "is_title": boolean|null}}}
지침:
- 사용자가 지정한 도형만 fills 에 포함하세요. 지정되지 않은 도형은 절대 포함하지 마세요.
- 제공된 shape_id 만 key 로 사용하세요.
- 현재 구현은 텍스트 도형의 text/font_size_override/is_title 변경만 지원합니다.
- 폰트 크기는 10pt 이상 48pt 이하로만 조정하세요.
- 사용자가 텍스트/문구/표현 변경을 명시한 경우만 text 를 변경하세요.
- 스타일 요청만 있으면 text 는 null 로 두고 font_size_override 같은 스타일 필드만 반환하세요.
- 색상, 도형 크기, 위치, 레이아웃, 차트 데이터, 슬라이드 추가/삭제, 도형 이동,
  슬라이드 밖 배치, 임의 shape 생성은 허용되지 않습니다.
"""

_TEXT_FIELD_HINTS = (
    "텍스트",
    "문구",
    "표현",
    "문장",
    "오타",
    "카피",
    "워딩",
    "text",
    "copy",
    "wording",
    "typo",
)
_TEXT_TRANSFORM_HINTS = (
    "바꿔",
    "바꾸",
    "고쳐",
    "임팩트",
    "짧게",
    "요약",
    "rename",
    "rephrase",
    "rewrite",
)
_GENERIC_TEXT_CHANGE_HINTS = (
    "수정",
    "변경",
    "업데이트",
    "update",
)
_STYLE_ONLY_TEXT_CHANGE_PATTERN = re.compile(
    r"크기|폰트|font|pt|사이즈|size|키워\s*(줘|주세요)|키우|크게|작게|줄여|줄이|확대|축소|굵게|볼드|bold",
    re.I,
)
_CHART_DATA_CHANGE_PATTERN = re.compile(
    r"(차트|그래프|chart|graph).{0,16}(데이터|수치|값|series|categories|values|data)"
    r"|(데이터|수치|값|series|categories|values|data).{0,16}(차트|그래프|chart|graph)",
    re.I,
)
_UNSUPPORTED_REGENERATE_PATTERNS = (
    ("색상/채우기 변경", re.compile(r"색|색상|컬러|채우기|배경색|테두리|선색|color", re.I)),
    ("위치/배치 변경", re.compile(r"위치|이동|옮겨|배치|정렬|밖으로|position|move|align", re.I)),
    ("레이아웃 변경", re.compile(r"레이아웃|템플릿|layout|template", re.I)),
    (
        "도형 크기 변경",
        re.compile(r"(도형|박스|상자|shape|box).{0,8}(크기|확대|축소|size|resize)", re.I),
    ),
    ("차트 데이터 변경", _CHART_DATA_CHANGE_PATTERN),
    (
        "슬라이드/도형 생성",
        re.compile(r"슬라이드\s*(추가|삭제)|도형\s*(추가|삭제)|shape\s*(add|delete)", re.I),
    ),
)
_METRIC_SLIDE_PATTERN = re.compile(
    r"chart|graph|metric|metrics|kpi|차트|그래프|지표|성과\s*지표|수치|통계|전환율|증감|비율",
    re.I,
)
_INLINE_LABEL_LAYOUT_TYPES = {"inline_label_group"}
_INLINE_LABEL_FIT_POLICIES = {"resize_label", "inline_label_group"}
_BASIC_TEXT_LAYOUT_TYPES = {"basic_text_area"}
_BASIC_TEXT_FIT_POLICIES = {"basic_text_area", ""}


class GenerationLLM(Protocol):
    """LangChain compatible LLM interface."""

    def invoke(self, messages: list[object]) -> object:
        """메시지를 처리하고 응답 객체를 반환한다."""
        ...


class SlidePlanGenerator(Protocol):
    """portfolioText 와 템플릿 metadata 로 slide_plan 을 생성한다."""

    def create_plan(
        self,
        *,
        portfolio_text: str,
        template_metadata: Mapping[str, Any],
    ) -> SlidePlan:
        """검증된 slide_plan 을 반환한다."""
        ...


class ContentFillGenerator(Protocol):
    """슬라이드 slot 디스크립터를 fill 데이터로 변환한다."""

    def create_fills(
        self,
        *,
        content_brief: str,
        slots: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """SlideEditor.apply_fills 입력 형식의 fill 맵을 반환한다."""
        ...


class SlideChangeGenerator(Protocol):
    """사용자 재생성 요청을 부분 fill 변경 지시로 변환한다."""

    def create_changes(
        self,
        *,
        user_request: str,
        slots: Sequence[Mapping[str, Any]],
        current_fills: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """수정 대상 shape_id 만 포함한 fill 맵을 반환한다."""
        ...


@dataclass(frozen=True, slots=True)
class SourceSlide:
    """템플릿 Source Slide 메타데이터."""

    slide_index: int
    source_slide_id: str
    category: str
    description: str
    best_for: str
    slide_filename_override: str = ""

    @property
    def slide_filename(self) -> str:
        """PPTX 내부 slide XML 파일명."""
        if self.slide_filename_override:
            return self.slide_filename_override
        return f"slide{self.slide_index + 1}.xml"


@dataclass(frozen=True, slots=True)
class PlannedSlide:
    """초기 생성에서 선택된 단일 슬라이드."""

    slide_order: int
    source_slide_id: str
    category: str
    slide_filename: str
    content_brief: str
    reason: str = ""

    def to_plan_item(self) -> dict[str, Any]:
        """slidePlan JSONB 에 저장할 snake_case 객체."""
        return {
            "order": self.slide_order,
            "source_slide_id": self.source_slide_id,
            "category": self.category,
            "slide_filename": self.slide_filename,
            "content_brief": self.content_brief,
            "reason": self.reason,
        }

    def to_callback_item(self) -> dict[str, Any]:
        """slide-plan 콜백의 slides 배열 항목."""
        return {
            "slide_order": self.slide_order,
            "source_slide_id": self.source_slide_id,
            "slide_filename": self.slide_filename,
        }


@dataclass(frozen=True, slots=True)
class SlidePlan:
    """검증된 초기 생성 slide_plan."""

    selected_slides: tuple[PlannedSlide, ...]
    llm_model: str | None = None

    @property
    def total_slides(self) -> int:
        """선택된 슬라이드 수."""
        return len(self.selected_slides)

    def to_blob(self) -> dict[str, Any]:
        """메인 백엔드에 저장할 slidePlan JSONB."""
        blob: dict[str, Any] = {
            "selected_slides": [slide.to_plan_item() for slide in self.selected_slides]
        }
        if self.llm_model:
            blob["llm_model"] = self.llm_model
        return blob


class LLMSlidePlanGenerator:
    """LLM Call #1: 포트폴리오 구조 분석 + Source Slide 선택."""

    def __init__(self, llm: GenerationLLM | None = None) -> None:
        self._llm = llm

    def create_plan(
        self,
        *,
        portfolio_text: str,
        template_metadata: Mapping[str, Any],
    ) -> SlidePlan:
        """LLM 응답을 slide_plan 으로 파싱하고 규칙을 검증한다."""
        source_slides = parse_template_metadata(template_metadata)
        if not portfolio_text.strip():
            raise ValueError("portfolioText 가 비어 있습니다.")
        candidate_slides = prefilter_source_slides(
            portfolio_text=portfolio_text,
            source_slides=source_slides,
        )

        llm = self._llm or get_llm_uncached(temperature=0.2, timeout=120)
        messages = [
            SystemMessage(content=_PLAN_SYSTEM_PROMPT),
            HumanMessage(
                content=_build_plan_prompt(
                    portfolio_text=portfolio_text,
                    source_slides=candidate_slides,
                )
            ),
        ]
        response = llm.invoke(messages)
        response_text = _normalize_response_text(getattr(response, "content", response))
        payload = _loads_json_object(response_text)
        plan = _parse_plan_payload(payload, candidate_slides, _model_name(llm))
        _validate_slide_plan(plan)
        return plan


class LLMContentFillGenerator:
    """LLM Call #2: Slot 디스크립터를 fill 데이터로 변환."""

    def __init__(self, llm: GenerationLLM | None = None) -> None:
        self._llm = llm

    def create_fills(
        self,
        *,
        content_brief: str,
        slots: Sequence[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """LLM 응답 fill 맵을 파싱하고 shape/font guardrail 을 적용한다."""
        if not slots:
            return {}

        llm = self._llm or get_llm_uncached(temperature=0.1, timeout=120)
        prompt_payload = _build_fill_prompt_payload(content_brief=content_brief, slots=slots)
        messages = [
            SystemMessage(content=_FILL_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "다음 슬라이드 요지와 Slot 정보를 보고 fills 를 작성하세요.\n"
                    f"{json.dumps(prompt_payload, ensure_ascii=False, default=str)}"
                )
            ),
        ]
        response = llm.invoke(messages)
        response_text = _normalize_response_text(getattr(response, "content", response))
        payload = _loads_json_object(response_text)
        return _parse_fills_payload(payload, slots)


class LLMSlideChangeGenerator:
    """Phase 2 일반 재생성: 사용자 요청을 안전한 부분 fill 변경으로 변환."""

    def __init__(self, llm: GenerationLLM | None = None) -> None:
        self._llm = llm

    def create_changes(
        self,
        *,
        user_request: str,
        slots: Sequence[Mapping[str, Any]],
        current_fills: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """사용자 요청과 현재 slot/fill 을 기반으로 수정 대상만 산출한다."""
        if not user_request.strip():
            raise ValueError("재생성 요청이 비어 있습니다.")
        _reject_unsupported_regenerate_request(user_request)
        if not slots:
            return {}

        llm = self._llm or get_llm_uncached(temperature=0.1, timeout=120)
        prompt_payload = json.dumps(
            {
                "user_request": user_request,
                "slots": list(slots),
                "current_fills": current_fills,
            },
            ensure_ascii=False,
            default=str,
        )
        messages = [
            SystemMessage(content=_REGENERATE_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "현재 슬라이드 Slot, 적용 중인 current_fills, 사용자 요청을 보고 "
                    "수정 대상 shape_id 만 fills 로 반환하세요.\n"
                    f"{prompt_payload}"
                )
            ),
        ]
        response = llm.invoke(messages)
        response_text = _normalize_response_text(getattr(response, "content", response))
        payload = _loads_json_object(response_text)
        return _parse_regenerate_changes_payload(
            payload,
            slots=slots,
            current_fills=current_fills,
            allow_text_change=_text_change_requested(user_request),
        )


def parse_template_metadata(metadata: Mapping[str, Any]) -> tuple[SourceSlide, ...]:
    """meta.json 객체에서 SourceSlide 목록을 추출한다."""
    require_template_v2_metadata(metadata)

    raw_slides = metadata.get("slides")
    if isinstance(raw_slides, list):
        return _parse_source_slides(raw_slides)

    raw_runtime_slides = metadata.get("runtime_slides")
    if not isinstance(raw_runtime_slides, list):
        raise ValueError("template meta.json 에 runtime_slides 배열이 필요합니다.")

    return _parse_runtime_source_slides(raw_runtime_slides)


def _parse_source_slides(raw_slides: list[Any]) -> tuple[SourceSlide, ...]:
    """slides 배열에서 SourceSlide 목록을 추출한다."""

    slides: list[SourceSlide] = []
    for index, raw_slide in enumerate(raw_slides):
        if not isinstance(raw_slide, Mapping):
            raise ValueError(f"slides[{index}] 항목은 객체여야 합니다.")
        try:
            slide_index = int(raw_slide["slide_index"])
            source_slide_id = str(raw_slide["id"])
            category = str(raw_slide["category"])
            description = str(raw_slide["description"])
            best_for = str(raw_slide["best_for"])
        except KeyError as exc:
            raise ValueError(f"slides[{index}] 필수 필드가 없습니다: {exc.args[0]}") from exc

        slides.append(
            SourceSlide(
                slide_index=slide_index,
                source_slide_id=source_slide_id,
                category=category,
                description=description,
                best_for=best_for,
            )
        )

    return tuple(slides)


def _parse_runtime_source_slides(raw_slides: list[Any]) -> tuple[SourceSlide, ...]:
    """v2 runtime_slides 배열에서 최소 SourceSlide 목록을 추출한다."""
    slides: list[SourceSlide] = []
    last_index = len(raw_slides) - 1
    for index, raw_slide in enumerate(raw_slides):
        if not isinstance(raw_slide, Mapping):
            raise ValueError(f"runtime_slides[{index}] 항목은 객체여야 합니다.")
        try:
            slide_index = int(raw_slide["slide_index"])
        except KeyError as exc:
            raise ValueError(
                f"runtime_slides[{index}] 필수 필드가 없습니다: {exc.args[0]}"
            ) from exc

        slide_number = slide_index + 1
        slide_filename = _runtime_slide_filename(raw_slide, slide_number)
        category = _metadata_text(raw_slide, "category") or _fallback_runtime_category(
            index,
            last_index,
        )
        slides.append(
            SourceSlide(
                slide_index=slide_index,
                source_slide_id=(
                    _metadata_text(raw_slide, "id")
                    or _metadata_text(raw_slide, "source_slide_id")
                    or f"slide{slide_number}"
                ),
                category=category,
                description=(
                    _metadata_text(raw_slide, "description") or f"Runtime slide {slide_number}"
                ),
                best_for=_metadata_text(raw_slide, "best_for") or category,
                slide_filename_override=slide_filename,
            )
        )
    return tuple(slides)


def _metadata_text(metadata: Mapping[str, Any], field: str) -> str:
    value = metadata.get(field)
    if isinstance(value, str):
        return value.strip()
    return ""


def _runtime_slide_filename(metadata: Mapping[str, Any], slide_number: int) -> str:
    slide_filename = _metadata_text(metadata, "slide_filename")
    if slide_filename:
        return _basename(slide_filename)

    slide_part = _metadata_text(metadata, "slide_part")
    if slide_part:
        return _basename(slide_part)

    return f"slide{slide_number}.xml"


def _basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", maxsplit=1)[-1]


def _fallback_runtime_category(index: int, last_index: int) -> str:
    if index == 0:
        return "cover"
    if index == last_index:
        return "closing"
    return _FALLBACK_RUNTIME_CATEGORIES[(index - 1) % len(_FALLBACK_RUNTIME_CATEGORIES)]


def prefilter_source_slides(
    *,
    portfolio_text: str,
    source_slides: Sequence[SourceSlide],
) -> tuple[SourceSlide, ...]:
    """확실한 신호 기반으로 Source Slide 후보를 사전 필터링한다."""
    if not source_slides:
        return ()

    has_numeric_data = _has_numeric_signal(portfolio_text)
    has_visual_asset = _has_visual_signal(portfolio_text)
    filtered = [
        slide
        for slide in source_slides
        if _keep_source_slide(
            slide,
            has_numeric_data=has_numeric_data,
            has_visual_asset=has_visual_asset,
        )
    ]
    # 7~12장 plan 생성을 방해할 정도로 후보가 줄면 원본 풀을 사용한다.
    if len(filtered) < 7:
        return tuple(source_slides)
    return tuple(filtered)


def _build_plan_prompt(*, portfolio_text: str, source_slides: Sequence[SourceSlide]) -> str:
    payload = {
        "portfolio_text": portfolio_text,
        "selection_signals": {
            "has_numeric_data": _has_numeric_signal(portfolio_text),
            "has_visual_asset": _has_visual_signal(portfolio_text),
        },
        "selection_guidance": (
            "has_numeric_data=true 이면 chart 또는 metric-oriented Source Slide 를 "
            "후보로 고려하고, 선택 시 reason 에 수치/성과 지표 근거를 남기세요."
        ),
        "source_slides": [
            {
                "id": slide.source_slide_id,
                "category": slide.category,
                "description": slide.description,
                "best_for": slide.best_for,
            }
            for slide in source_slides
        ],
        "output_schema": {
            "total_slides": 8,
            "slide_plan": [
                {
                    "order": 1,
                    "selected_slide_id": "cover_A",
                    "reason": "선택 근거",
                    "content_brief": "이 슬라이드에 담을 내용 요지",
                }
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def _build_fill_prompt_payload(
    *,
    content_brief: str,
    slots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """LLM Call #2에 전달할 slot capacity 중심 payload를 구성한다."""
    return {
        "content_brief": content_brief,
        "slot_guidance": {
            "placeholder_text": "slot 의 의미를 알려주는 입력 힌트입니다.",
            "example_text": "복사 대상이 아니라 형식, 길이, 줄 수 참고용입니다.",
            "length_hint": "텍스트 생성 시 우선 준수해야 하는 길이 제약입니다.",
        },
        "slots": [_build_fill_slot_prompt_payload(slot) for slot in slots],
    }


def _build_fill_slot_prompt_payload(slot: Mapping[str, Any]) -> dict[str, Any]:
    """원본 slot descriptor에 prompt용 capacity hint를 보강한다."""
    prompt_slot = dict(slot)
    if not _slot_uses_text_capacity(slot):
        return prompt_slot

    placeholder_text = _slot_text(slot, "placeholder_text") or _slot_text(slot, "current_text")
    example_text = _slot_text(slot, "example_text")
    example_line_count = _positive_int(slot.get("example_line_count")) or _line_count(example_text)
    max_lines = (
        _positive_int(slot.get("max_lines"))
        or example_line_count
        or _line_count(placeholder_text)
        or 1
    )
    nowrap = slot.get("nowrap")
    if not isinstance(nowrap, bool):
        nowrap = max_lines == 1

    prompt_slot["placeholder_text"] = placeholder_text or None
    prompt_slot["example_text"] = example_text or None
    prompt_slot["example_line_count"] = example_line_count
    prompt_slot["max_lines"] = max_lines
    prompt_slot["nowrap"] = nowrap
    if _positive_int(slot.get("example_char_count")) is None and example_text:
        prompt_slot["example_char_count"] = len(example_text.replace("\n", ""))
    prompt_slot["length_hint"] = _slot_length_hint(
        slot=slot,
        example_text=example_text,
        example_line_count=example_line_count,
        max_lines=max_lines,
        nowrap=nowrap,
    )
    return prompt_slot


def _slot_uses_text_capacity(slot: Mapping[str, Any]) -> bool:
    return _slot_is_editable(slot) and "text" in _allowed_actions_for_slot(slot)


def _slot_length_hint(
    *,
    slot: Mapping[str, Any],
    example_text: str,
    example_line_count: int | None,
    max_lines: int,
    nowrap: bool,
) -> str:
    """layout 유형별 LLM 길이 지침을 생성한다."""
    layout_type = _normalized_slot_text(slot, "layout_type")
    layout_group_type = _normalized_slot_text(slot, "layout_group_type")
    fit_policy = _normalized_slot_text(slot, "fit_policy")

    if (
        layout_type in _INLINE_LABEL_LAYOUT_TYPES
        or layout_group_type in _INLINE_LABEL_LAYOUT_TYPES
        or fit_policy in _INLINE_LABEL_FIT_POLICIES
    ):
        return _inline_label_length_hint(example_text=example_text, nowrap=nowrap)

    if (
        layout_type in _BASIC_TEXT_LAYOUT_TYPES
        or layout_group_type in _BASIC_TEXT_LAYOUT_TYPES
        or fit_policy in _BASIC_TEXT_FIT_POLICIES
    ):
        return _basic_text_area_length_hint(
            example_text=example_text,
            example_line_count=example_line_count,
            max_lines=max_lines,
            nowrap=nowrap,
        )

    return _fallback_length_hint(
        example_text=example_text,
        example_line_count=example_line_count,
        max_lines=max_lines,
        nowrap=nowrap,
    )


def _inline_label_length_hint(*, example_text: str, nowrap: bool) -> str:
    example_clause = _example_length_clause(example_text)
    nowrap_clause = " 줄바꿈하지 말고 한 줄로 유지하세요." if nowrap else ""
    return (
        "짧은 chip/label 문구로 작성하세요. "
        f"{example_clause}공백이 포함되어도 단일 label 처럼 읽혀야 합니다.{nowrap_clause}"
    )


def _basic_text_area_length_hint(
    *,
    example_text: str,
    example_line_count: int | None,
    max_lines: int,
    nowrap: bool,
) -> str:
    example_clause = _example_length_clause(example_text)
    line_clause = f"최대 {max_lines}줄 안에서 작성하세요."
    if example_line_count is not None:
        line_clause = f"예시는 {example_line_count}줄이며, 최대 {max_lines}줄 안에서 작성하세요."
    nowrap_clause = " 줄바꿈하지 말고 한 줄로 요약하세요." if nowrap else ""
    return f"일반 텍스트 영역입니다. {example_clause}{line_clause}{nowrap_clause}"


def _fallback_length_hint(
    *,
    example_text: str,
    example_line_count: int | None,
    max_lines: int,
    nowrap: bool,
) -> str:
    if nowrap:
        return f"{_example_length_clause(example_text)}최대 {max_lines}줄, 한 줄 문구로 작성하세요."
    if example_line_count is not None:
        return (
            f"{_example_length_clause(example_text)}"
            f"예시는 {example_line_count}줄이며, 최대 {max_lines}줄 안에서 작성하세요."
        )
    return f"{_example_length_clause(example_text)}최대 {max_lines}줄 안에서 작성하세요."


def _example_length_clause(example_text: str) -> str:
    if not example_text:
        return "예시가 없으면 placeholder_text의 의미를 기준으로 간결하게 작성하세요. "
    char_count = len(example_text.replace("\n", ""))
    return f"example_text는 복사하지 말고 {char_count}자 안팎의 형식과 길이만 참고하세요. "


def _parse_plan_payload(
    payload: Mapping[str, Any],
    source_slides: Sequence[SourceSlide],
    llm_model: str | None,
) -> SlidePlan:
    output = SlidePlanOutput.model_validate(payload)
    if output.total_slides is not None and output.total_slides != len(output.items):
        raise ValueError(
            "total_slides 와 slide_plan 항목 수가 일치해야 합니다. "
            f"(total_slides={output.total_slides}, items={len(output.items)})"
        )
    source_by_id = {slide.source_slide_id: slide for slide in source_slides}
    planned: list[PlannedSlide] = []
    for raw_item in output.items:
        source_slide_id = raw_item.resolved_source_slide_id
        source = source_by_id.get(source_slide_id)
        if source is None:
            raise ValueError(f"알 수 없는 source_slide_id 입니다: {source_slide_id}")
        planned.append(
            PlannedSlide(
                slide_order=raw_item.order,
                source_slide_id=source.source_slide_id,
                category=source.category,
                slide_filename=source.slide_filename,
                content_brief=raw_item.content_brief.strip(),
                reason=raw_item.reason,
            )
        )

    return SlidePlan(selected_slides=tuple(planned), llm_model=llm_model)


def _validate_slide_plan(plan: SlidePlan) -> None:
    if not 7 <= plan.total_slides <= 12:
        raise ValueError(f"slide_plan 은 7~12장이어야 합니다. (현재: {plan.total_slides})")

    orders = [slide.slide_order for slide in plan.selected_slides]
    expected_orders = list(range(1, plan.total_slides + 1))
    if orders != expected_orders:
        raise ValueError(f"slide_order 는 1부터 연속이어야 합니다. (기대: {expected_orders})")

    categories = [slide.category for slide in plan.selected_slides]
    if "cover" not in categories:
        raise ValueError("slide_plan 에 cover 카테고리가 필요합니다.")
    if "closing" not in categories:
        raise ValueError("slide_plan 에 closing 카테고리가 필요합니다.")

    source_slide_ids = [slide.source_slide_id for slide in plan.selected_slides]
    if len(set(source_slide_ids)) != len(source_slide_ids):
        raise ValueError("같은 source_slide_id 를 중복 선택할 수 없습니다.")

    for previous, current in zip(categories, categories[1:], strict=False):
        if previous == current:
            raise ValueError(f"같은 카테고리를 연속 선택할 수 없습니다: {current}")


def _parse_fills_payload(
    payload: Mapping[str, Any],
    slots: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    output = FillPayloadOutput.from_payload(payload)
    slots_by_id = {str(slot.get("shape_id")): slot for slot in slots if slot.get("shape_id")}
    normalized: dict[str, dict[str, Any]] = {}
    for shape_id, raw_fill in output.fills.items():
        shape_key = str(shape_id)
        if shape_key not in slots_by_id:
            raise ValueError(f"제공되지 않은 shape_id 입니다: {shape_key}")
        slot = slots_by_id[shape_key]
        if not _slot_is_editable(slot):
            raise ValueError(f"비편집 slot 은 fills 대상이 아닙니다: {shape_key}")

        fill = raw_fill.model_dump(exclude_none=True)
        action = raw_fill.action
        allowed_actions = _allowed_actions_for_slot(slot)
        if action not in allowed_actions:
            allowed_text = ", ".join(sorted(allowed_actions))
            raise ValueError(
                f"slot {shape_key} 은 action={action!r} 을 지원하지 않습니다. 허용: {allowed_text}"
            )
        if action == "text":
            fill["text"] = str(raw_fill.text or "")
        elif action == "chart":
            _validate_chart_fill_data(fill, shape_key)
        if fill.get("font_size_override") is not None:
            fill["font_size_override"] = _guard_font_size(
                fill["font_size_override"],
                slot.get("font_size_pt"),
            )
        normalized[shape_key] = fill

    missing_shape_ids = sorted(
        shape_id
        for shape_id, slot in slots_by_id.items()
        if _slot_requires_fill(slot) and shape_id not in normalized
    )
    if missing_shape_ids:
        raise ValueError(f"fills 에 누락된 shape_id 가 있습니다: {', '.join(missing_shape_ids)}")

    return normalized


def _parse_regenerate_changes_payload(
    payload: Mapping[str, Any],
    *,
    slots: Sequence[Mapping[str, Any]],
    current_fills: Mapping[str, Any],
    allow_text_change: bool,
) -> dict[str, dict[str, Any]]:
    fills = payload.get("fills")
    if not isinstance(fills, Mapping):
        raise ValueError("재생성 변경 지시에는 fills 객체가 필요합니다.")

    slots_by_id = {str(slot.get("shape_id")): slot for slot in slots if slot.get("shape_id")}
    normalized: dict[str, dict[str, Any]] = {}
    for shape_id, raw_fill in fills.items():
        shape_key = str(shape_id)
        if shape_key not in slots_by_id:
            raise ValueError(f"제공되지 않은 shape_id 입니다: {shape_key}")
        if not isinstance(raw_fill, Mapping):
            raise ValueError(f"fill 은 객체여야 합니다: {shape_key}")

        slot = slots_by_id[shape_key]
        if not _slot_is_editable(slot) or "text" not in _allowed_actions_for_slot(slot):
            raise ValueError(f"재생성 변경은 텍스트 도형만 지원합니다: {shape_key}")

        action = str(raw_fill.get("action") or "text")
        if action != "text":
            raise ValueError(f"재생성 변경은 text action 만 허용합니다: {shape_key}")

        fill: dict[str, Any] = {"action": "text"}
        requested_text = raw_fill.get("text")
        if allow_text_change and requested_text is not None:
            fill["text"] = str(requested_text)
        else:
            fill["text"] = _existing_text_for_shape(shape_key, slot, current_fills)

        if raw_fill.get("font_size_override") is not None:
            fill["font_size_override"] = _guard_regenerate_font_size(
                raw_fill.get("font_size_override")
            )
        if raw_fill.get("is_title") is not None:
            fill["is_title"] = bool(raw_fill.get("is_title"))
        normalized[shape_key] = fill

    return normalized


def _existing_text_for_shape(
    shape_id: str,
    slot: Mapping[str, Any],
    current_fills: Mapping[str, Any],
) -> str:
    current_fill = current_fills.get(shape_id)
    if isinstance(current_fill, Mapping) and current_fill.get("text") is not None:
        return str(current_fill.get("text"))
    if slot.get("current_text") is not None:
        return str(slot.get("current_text"))
    return ""


def _guard_regenerate_font_size(value: Any) -> float:
    try:
        size = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"font_size_override 는 숫자여야 합니다: {value!r}") from exc
    return min(max(size, 10.0), 48.0)


def _text_change_requested(user_request: str) -> bool:
    lowered = user_request.casefold()
    if _STYLE_ONLY_TEXT_CHANGE_PATTERN.search(user_request):
        return False
    if any(hint in lowered for hint in _TEXT_FIELD_HINTS):
        return True
    if any(hint in lowered for hint in _TEXT_TRANSFORM_HINTS):
        return True
    if any(hint in lowered for hint in _GENERIC_TEXT_CHANGE_HINTS):
        return True
    return bool(re.search(r"[\"'“”‘’][^\"'“”‘’]+[\"'“”‘’]\s*(로|으로)", user_request))


def _reject_unsupported_regenerate_request(user_request: str) -> None:
    for label, pattern in _UNSUPPORTED_REGENERATE_PATTERNS:
        if pattern.search(user_request):
            raise ValueError(
                "지원하지 않는 수정 범위입니다: "
                f"{label}. 현재 Phase 2 는 지정한 텍스트 도형의 문구, 폰트 크기, "
                "제목 강조만 지원합니다."
            )


def _keep_source_slide(
    slide: SourceSlide,
    *,
    has_numeric_data: bool,
    has_visual_asset: bool,
) -> bool:
    if slide.category in {"cover", "closing"}:
        return True
    if _is_metric_or_chart_slide(slide) and not has_numeric_data:
        return False
    if slide.category == "visual" and not has_visual_asset:
        return False
    return True


def _slot_is_editable(slot: Mapping[str, Any]) -> bool:
    if slot.get("editable") is False:
        return False
    kind = str(slot.get("kind") or "").casefold()
    return kind not in {"decorative", "background", "layout", "non_editable"}


def _slot_requires_fill(slot: Mapping[str, Any]) -> bool:
    return _slot_is_editable(slot) and slot.get("required") is not False


def _allowed_actions_for_slot(slot: Mapping[str, Any]) -> set[str]:
    raw_actions = slot.get("allowed_actions")
    if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, str):
        actions = {str(action) for action in raw_actions}
        if actions:
            return actions

    kind = str(slot.get("kind") or "text").casefold()
    if kind == "chart":
        return {"chart"}
    return {"text", "remove"}


def _validate_chart_fill_data(fill: Mapping[str, Any], shape_id: str) -> None:
    data = fill.get("data")
    if not isinstance(data, Mapping):
        raise ValueError(f"chart fill 에는 data 객체가 필요합니다: {shape_id}")

    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError(f"chart fill 에는 data.categories 배열이 필요합니다: {shape_id}")

    series_list = data.get("series")
    if not isinstance(series_list, list) or not series_list:
        raise ValueError(f"chart fill 에는 data.series 배열이 필요합니다: {shape_id}")

    for index, series in enumerate(series_list):
        if not isinstance(series, Mapping):
            raise ValueError(f"chart data.series[{index}] 는 객체여야 합니다: {shape_id}")
        values = series.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError(f"chart data.series[{index}].values 배열이 필요합니다: {shape_id}")
        if len(values) != len(categories):
            raise ValueError(
                f"chart categories 와 series[{index}].values 길이가 일치해야 합니다: {shape_id}"
            )


def _is_metric_or_chart_slide(slide: SourceSlide) -> bool:
    text = f"{slide.category} {slide.description} {slide.best_for}"
    return bool(_METRIC_SLIDE_PATTERN.search(text))


def _has_numeric_signal(text: str) -> bool:
    return bool(re.search(r"\d|%|퍼센트|배|건|명|원|만원|억원|kpi", text, flags=re.IGNORECASE))


def _has_visual_signal(text: str) -> bool:
    return bool(
        re.search(
            r"스크린샷|화면|이미지|목업|mockup|시안|사진|캡처|prototype|프로토타입",
            text,
            flags=re.IGNORECASE,
        )
    )


def _slot_text(slot: Mapping[str, Any], field: str) -> str:
    value = slot.get(field)
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _normalized_slot_text(slot: Mapping[str, Any], field: str) -> str:
    return _slot_text(slot, field).casefold()


def _line_count(text: str) -> int | None:
    if not text:
        return None
    return max(1, len(text.splitlines()))


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _guard_font_size(value: Any, base_font_size: Any) -> float:
    try:
        size = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"font_size_override 는 숫자여야 합니다: {value!r}") from exc

    try:
        base = float(base_font_size)
    except (TypeError, ValueError):
        base = 0.0

    lower = 10.0
    upper = 48.0
    if base > 0:
        base_lower = base * 0.6
        base_upper = base * 1.2
        lower = max(base_lower, 10.0)
        upper = min(base_upper, 48.0)
        if upper < lower:
            upper = lower
    return min(max(size, lower), upper)


def _loads_json_object(response_text: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(response_text)
    decoder = json.JSONDecoder()
    for start in [index for index, char in enumerate(cleaned) if char == "{"]:
        try:
            payload, _ = decoder.raw_decode(cleaned, idx=start)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("JSON 객체를 찾을 수 없습니다.")


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _normalize_response_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Mapping):
        return json.dumps(content, ensure_ascii=False)
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                texts.append(item.strip())
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
        return "\n".join(texts).strip()
    return str(content).strip()


def _model_name(llm: GenerationLLM) -> str | None:
    value = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    return str(value) if value else None
