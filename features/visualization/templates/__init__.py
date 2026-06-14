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
from .compiler_v2 import TemplateV2CompileResult, compile_template_v2
from .pptx import (
    SlideText,
    count_pptx_slides,
    extract_slide_texts,
    extract_template_v2_from_pptx,
)
from .v2 import (
    SCHEMA_VERSION_V2,
    TemplateV2Extraction,
    TemplateV2Payloads,
    build_template_v2_payloads,
    canonical_json_text,
    json_file_normalized_equal,
    json_normalized_equal,
    normalize_json,
    read_json_payload,
    write_json_payload,
)
from .validation import TemplateValidationResult, validate_template_directory

__all__ = [
    "BuildMetaOptions",
    "BuildMetaResult",
    "CategoryDefinition",
    "CategorySchema",
    "LlmSlideDraftGenerator",
    "MarkitdownSlideTextExtractor",
    "SCHEMA_VERSION_V2",
    "SlideDraft",
    "SlideDraftInput",
    "SlideText",
    "TemplateValidationResult",
    "TemplateV2CompileResult",
    "TemplateV2Extraction",
    "TemplateV2Payloads",
    "TextExtractionResult",
    "build_template_v2_payloads",
    "build_template_metadata",
    "canonical_json_text",
    "compile_template_v2",
    "count_pptx_slides",
    "extract_slide_texts",
    "extract_template_v2_from_pptx",
    "json_file_normalized_equal",
    "json_normalized_equal",
    "load_category_schema",
    "normalize_json",
    "read_json_payload",
    "validate_template_directory",
    "write_json_payload",
]
