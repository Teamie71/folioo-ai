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
- 제공된 shape_id 만 key 로 사용하세요.
- 텍스트가 길면 먼저 폰트를 줄이되 원본의 60% 미만이나 10pt 미만으로 내리지 마세요.
- 텍스트 요약이 필요하면 고유명사, 수치, 기술 스택을 보존하세요.
- 제공된 모든 shape_id 에 대해 text/remove/chart 중 하나를 반환하세요.
"""


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


@dataclass(frozen=True, slots=True)
class SourceSlide:
    """템플릿 Source Slide 메타데이터."""

    slide_index: int
    source_slide_id: str
    category: str
    description: str
    best_for: str

    @property
    def slide_filename(self) -> str:
        """PPTX 내부 slide XML 파일명."""
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
        messages = [
            SystemMessage(content=_FILL_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "다음 슬라이드 요지와 Slot 정보를 보고 fills 를 작성하세요.\n"
                    f"{json.dumps({'content_brief': content_brief, 'slots': list(slots)}, ensure_ascii=False, default=str)}"
                )
            ),
        ]
        response = llm.invoke(messages)
        response_text = _normalize_response_text(getattr(response, "content", response))
        payload = _loads_json_object(response_text)
        return _parse_fills_payload(payload, slots)


def parse_template_metadata(metadata: Mapping[str, Any]) -> tuple[SourceSlide, ...]:
    """meta.json 객체에서 SourceSlide 목록을 추출한다."""
    raw_slides = metadata.get("slides")
    if not isinstance(raw_slides, list):
        raise ValueError("template meta.json 에 slides 배열이 필요합니다.")

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
        fill = raw_fill.model_dump(exclude_none=True)
        action = raw_fill.action
        if action == "text":
            fill["text"] = str(raw_fill.text or "")
        if fill.get("font_size_override") is not None:
            fill["font_size_override"] = _guard_font_size(
                fill["font_size_override"],
                slots_by_id[shape_key].get("font_size_pt"),
            )
        normalized[shape_key] = fill

    missing_shape_ids = sorted(set(slots_by_id) - set(normalized))
    if missing_shape_ids:
        raise ValueError(f"fills 에 누락된 shape_id 가 있습니다: {', '.join(missing_shape_ids)}")

    return normalized


def _keep_source_slide(
    slide: SourceSlide,
    *,
    has_numeric_data: bool,
    has_visual_asset: bool,
) -> bool:
    if slide.category in {"cover", "closing"}:
        return True
    if slide.category == "chart" and not has_numeric_data:
        return False
    if slide.category == "visual" and not has_visual_asset:
        return False
    return True


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
