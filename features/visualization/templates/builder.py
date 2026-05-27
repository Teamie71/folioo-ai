"""템플릿 PPTX에서 meta.json 초안을 생성하는 오프라인 빌더."""

import base64
import json
import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from .categories import DEFAULT_CATEGORY_SCHEMA_PATH, CategorySchema, load_category_schema
from .pptx import SlideText, count_pptx_slides, extract_slide_texts
from .thumbnail import PillowThumbnailBuilder

_DRAFT_NOTICE = (
    "이 파일은 LLM이 생성한 템플릿 메타데이터 초안입니다. "
    "운영자는 category, description, best_for, id를 검토한 뒤 배포해야 합니다. "
    "slide_index와 template_file은 자동 생성 필드이므로 수정하지 마세요."
)
_LLM_IMAGE_MAX_SIZE = (1024, 1024)
_LLM_IMAGE_QUALITY = 72
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildMetaOptions:
    """meta.json 초안 생성 옵션."""

    pptx_path: Path
    template_id: str
    primary_color: str
    output_path: Path
    theme_name: str | None = None
    category_schema_path: Path | None = None
    slides_dir: Path | None = None
    thumbnail_path: Path | None = None
    text_output_path: Path | None = None


@dataclass(frozen=True)
class SlideDraftInput:
    """LLM 초안 생성에 입력할 단일 Source Slide 정보."""

    slide_index: int
    image_path: Path
    text: str


@dataclass(frozen=True)
class SlideDraft:
    """단일 Source Slide 의미 메타데이터 초안."""

    category: str
    description: str
    best_for: str


@dataclass(frozen=True)
class TextExtractionResult:
    """markitdown/OOXML 기반 임시 텍스트 추출 결과."""

    deck_text: str
    slide_texts: tuple[SlideText, ...]


@dataclass(frozen=True)
class BuildMetaResult:
    """meta.json 초안 생성 결과."""

    meta_path: Path
    slide_image_paths: tuple[Path, ...]
    thumbnail_path: Path
    text_output_path: Path


class Renderer(Protocol):
    """PPTX 렌더러 프로토콜."""

    def render(self, pptx_path: Path | str, output_dir: Path | str):
        """PPTX를 PDF/JPG로 렌더링한다."""


class ThumbnailBuilder(Protocol):
    """그리드 썸네일 생성기 프로토콜."""

    def build(self, slide_images: Sequence[Path], output_path: Path) -> Path:
        """슬라이드 이미지 목록으로 thumbnail.jpg를 생성한다."""


class TextExtractor(Protocol):
    """슬라이드 텍스트 추출기 프로토콜."""

    def extract(self, pptx_path: Path) -> TextExtractionResult:
        """PPTX에서 임시 텍스트를 추출한다."""


class SlideDraftGenerator(Protocol):
    """Source Slide 의미 메타데이터 초안 생성기 프로토콜."""

    def generate(self, slide: SlideDraftInput, categories: CategorySchema) -> SlideDraft:
        """단일 Source Slide 의미 필드 초안을 생성한다."""


class MarkitdownSlideTextExtractor:
    """markitdown 결과와 OOXML 슬라이드 텍스트를 함께 저장하는 추출기."""

    def extract(self, pptx_path: Path) -> TextExtractionResult:
        """markitdown으로 덱 텍스트를 만들고, OOXML에서 슬라이드별 텍스트를 추출한다."""
        try:
            from markitdown import MarkItDown
        except ImportError as exc:
            raise RuntimeError(
                "markitdown이 설치되지 않았습니다. "
                "uv sync --group template-tools 후 다시 실행하세요."
            ) from exc

        converted = MarkItDown().convert(str(pptx_path))
        deck_text = str(getattr(converted, "text_content", "") or "").strip()
        return TextExtractionResult(
            deck_text=deck_text,
            slide_texts=extract_slide_texts(pptx_path),
        )


class _SlideDraftOutput(BaseModel):
    """LLM 구조화 출력."""

    category: str = Field(description="표준 카테고리 키 또는 unknown")
    description: str = Field(description="레이아웃과 구성을 요약한 한 줄 설명")
    best_for: str = Field(description="어떤 콘텐츠에 적합한지 설명하는 한 줄 가이드")


class LlmSlideDraftGenerator:
    """OpenRouter LLM 기반 Source Slide 의미 필드 초안 생성기."""

    def __init__(self, *, model: str | None = None, temperature: float = 0.1) -> None:
        self.model = model
        self.temperature = temperature

    def generate(self, slide: SlideDraftInput, categories: CategorySchema) -> SlideDraft:
        """썸네일과 임시 텍스트를 LLM에 입력해 Source Slide 초안을 생성한다."""
        from langchain_core.messages import HumanMessage, SystemMessage

        from common.llm.client import get_llm_uncached

        llm = get_llm_uncached(model=self.model, temperature=self.temperature, timeout=120)
        structured_llm = llm.with_structured_output(_SlideDraftOutput)
        output = structured_llm.invoke(
            [
                SystemMessage(
                    content=(
                        "너는 PPT Source Slide 분류 전문가야. "
                        "슬라이드 썸네일과 임시 텍스트를 보고 표준 카테고리 중 하나로 분류해. "
                        "확실하지 않으면 unknown을 사용해. "
                        "description과 best_for는 운영자가 검토할 초안으로 짧게 작성해."
                    )
                ),
                HumanMessage(
                    content=[
                        {
                            "type": "text",
                            "text": _format_llm_prompt_text(slide, categories),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": _image_data_uri(slide.image_path)},
                        },
                    ]
                ),
            ]
        )
        return SlideDraft(
            category=output.category.strip(),
            description=output.description.strip(),
            best_for=output.best_for.strip(),
        )


def build_template_metadata(
    options: BuildMetaOptions,
    *,
    renderer: Renderer | None = None,
    thumbnail_builder: ThumbnailBuilder | None = None,
    text_extractor: TextExtractor | None = None,
    draft_generator: SlideDraftGenerator | None = None,
) -> BuildMetaResult:
    """PPTX 렌더링, 텍스트 추출, LLM 초안 생성을 수행해 meta.json을 작성한다."""
    _validate_options(options)

    output_path = options.output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    slides_dir = options.slides_dir or output_path.parent / "slides"
    thumbnail_path = options.thumbnail_path or output_path.parent / "thumbnail.jpg"
    text_output_path = options.text_output_path or output_path.parent / "slide_text.md"

    schema = load_category_schema(options.category_schema_path or DEFAULT_CATEGORY_SCHEMA_PATH)
    slide_count = count_pptx_slides(options.pptx_path)
    if slide_count == 0:
        raise ValueError("PPTX에 Source Slide가 없습니다.")

    active_renderer = renderer or _default_renderer()
    render_result = active_renderer.render(options.pptx_path, slides_dir)
    slide_image_paths = tuple(Path(path) for path in render_result.image_paths)
    if len(slide_image_paths) != slide_count:
        raise ValueError(
            "렌더링된 슬라이드 이미지 수가 PPTX 슬라이드 수와 일치하지 않습니다. "
            f"(PPTX: {slide_count}, 이미지: {len(slide_image_paths)})"
        )

    active_thumbnail_builder = thumbnail_builder or PillowThumbnailBuilder()
    active_thumbnail_builder.build(slide_image_paths, thumbnail_path)

    extraction = (text_extractor or MarkitdownSlideTextExtractor()).extract(options.pptx_path)
    slide_text_map = {
        slide_text.slide_index: slide_text.text for slide_text in extraction.slide_texts
    }
    _write_text_output(
        text_output_path,
        deck_text=extraction.deck_text,
        slide_count=slide_count,
        slide_text_map=slide_text_map,
    )

    active_draft_generator = draft_generator or LlmSlideDraftGenerator()
    slide_entries = []
    for slide_index in range(slide_count):
        logger.info("Source Slide %s/%s meta 초안 생성 중", slide_index + 1, slide_count)
        draft = active_draft_generator.generate(
            SlideDraftInput(
                slide_index=slide_index,
                image_path=slide_image_paths[slide_index],
                text=slide_text_map.get(slide_index, ""),
            ),
            schema,
        )
        slide_entries.append(
            {
                "slide_index": slide_index,
                "category": draft.category,
                "description": draft.description,
                "best_for": draft.best_for,
            }
        )

    slide_entries = _assign_ids(slide_entries)
    metadata = {
        "_draft_notice": _DRAFT_NOTICE,
        "template_id": options.template_id,
        "template_file": options.pptx_path.name,
        "theme": {
            "primary_color": options.primary_color,
            "name": options.theme_name or options.template_id,
        },
        "slides": slide_entries,
    }
    output_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return BuildMetaResult(
        meta_path=output_path,
        slide_image_paths=slide_image_paths,
        thumbnail_path=thumbnail_path,
        text_output_path=text_output_path,
    )


def _validate_options(options: BuildMetaOptions) -> None:
    if not options.pptx_path.is_file():
        raise ValueError(f"PPTX 파일을 찾을 수 없습니다: {options.pptx_path}")
    if options.pptx_path.suffix.lower() != ".pptx":
        raise ValueError(f"PPTX 파일만 처리할 수 있습니다: {options.pptx_path}")
    if not options.template_id.strip():
        raise ValueError("template_id는 비어 있을 수 없습니다.")
    if not options.primary_color.strip():
        raise ValueError("primary_color는 비어 있을 수 없습니다.")


def _default_renderer() -> Renderer:
    try:
        from features.visualization.pptx import PptxRenderer
    except ImportError as exc:
        raise RuntimeError("PPTX 렌더러를 불러올 수 없습니다.") from exc
    return PptxRenderer()


def _assign_ids(slide_entries: list[dict[str, object]]) -> list[dict[str, object]]:
    category_counts: dict[str, int] = defaultdict(int)
    entries_with_ids: list[dict[str, object]] = []
    for entry in slide_entries:
        category = str(entry["category"])
        category_counts[category] += 1
        entries_with_ids.append(
            {
                "slide_index": entry["slide_index"],
                "id": f"{_id_prefix(category)}_{_alphabetic_suffix(category_counts[category])}",
                "category": entry["category"],
                "description": entry["description"],
                "best_for": entry["best_for"],
            }
        )
    return entries_with_ids


def _id_prefix(category: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    return prefix or "unknown"


def _alphabetic_suffix(number: int) -> str:
    if number <= 0:
        raise ValueError("number는 1 이상이어야 합니다.")

    chars: list[str] = []
    current = number
    while current:
        current, remainder = divmod(current - 1, 26)
        chars.append(chr(ord("A") + remainder))
    return "".join(reversed(chars))


def _write_text_output(
    output_path: Path,
    *,
    deck_text: str,
    slide_count: int,
    slide_text_map: dict[int, str],
) -> None:
    lines = [
        "# Template Text Draft",
        "",
        "이 파일은 meta.json 초안 작성을 위한 임시 텍스트입니다.",
        "",
        "## Markitdown Deck Text",
        "",
        deck_text.strip() or "(비어 있음)",
        "",
        "## Slide Text",
        "",
    ]
    for slide_index in range(slide_count):
        lines.extend(
            [
                f"### Slide {slide_index + 1}",
                "",
                slide_text_map.get(slide_index, "").strip() or "(비어 있음)",
                "",
            ]
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _format_llm_prompt_text(slide: SlideDraftInput, categories: CategorySchema) -> str:
    category_lines = "\n".join(
        f"- {definition.key}: {definition.description}".rstrip()
        for definition in categories.definitions
    )
    return (
        f"표준 카테고리 목록:\n{category_lines}\n\n"
        f"슬라이드 번호: {slide.slide_index + 1}\n"
        f"임시 텍스트:\n{slide.text or '(비어 있음)'}\n\n"
        "다음 JSON 스키마에 맞는 값을 생성해줘: "
        '{"category": "...", "description": "...", "best_for": "..."}'
    )


def _image_data_uri(image_path: Path) -> str:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "LLM vision 입력 이미지 최적화를 위해 Pillow가 필요합니다. "
            "uv sync --group template-tools 후 다시 실행하세요."
        ) from exc

    with Image.open(image_path) as image:
        preview = image.convert("RGB")
        preview.thumbnail(_LLM_IMAGE_MAX_SIZE)
        buffer = BytesIO()
        preview.save(buffer, format="JPEG", quality=_LLM_IMAGE_QUALITY, optimize=True)

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
