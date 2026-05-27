"""템플릿 등록 파이프라인 유틸리티."""

from .builder import (
    BuildMetaOptions,
    BuildMetaResult,
    LlmSlideDraftGenerator,
    MarkitdownSlideTextExtractor,
    SlideDraft,
    SlideDraftInput,
    TextExtractionResult,
    build_template_metadata,
)
from .categories import CategoryDefinition, CategorySchema, load_category_schema
from .pptx import SlideText, count_pptx_slides, extract_slide_texts
from .validation import TemplateValidationResult, validate_template_directory

__all__ = [
    "BuildMetaOptions",
    "BuildMetaResult",
    "CategoryDefinition",
    "CategorySchema",
    "LlmSlideDraftGenerator",
    "MarkitdownSlideTextExtractor",
    "SlideDraft",
    "SlideDraftInput",
    "SlideText",
    "TemplateValidationResult",
    "TextExtractionResult",
    "build_template_metadata",
    "count_pptx_slides",
    "extract_slide_texts",
    "load_category_schema",
    "validate_template_directory",
]
