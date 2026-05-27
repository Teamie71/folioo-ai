"""PPTX OOXML 편집, 패키징, 렌더링 유틸리티."""

from importlib import import_module
from pkgutil import extend_path

from features.visualization.pptx.slide_editor import SlideEditor

from .toolchain import (
    ANTHROPIC_PPTX_SKILL_ENV,
    PptxToolchain,
    PptxToolchainError,
    PptxToolchainResult,
    ValidationResult,
)

__path__ = extend_path(__path__, __name__)

_RENDER_EXPORTS = {
    "DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE",
    "InMemoryConversionCounter",
    "PptxRenderError",
    "PptxRenderer",
    "RenderOptions",
    "RenderResult",
    "RenderedSlide",
    "should_recycle_worker",
}


def __getattr__(name: str):
    """worker 전용 렌더러 export 를 필요할 때만 로드한다."""
    if name not in _RENDER_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module("features.visualization.pptx.soffice_render")
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "ANTHROPIC_PPTX_SKILL_ENV",
    "DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE",
    "InMemoryConversionCounter",
    "PptxRenderError",
    "PptxRenderer",
    "PptxToolchain",
    "PptxToolchainError",
    "PptxToolchainResult",
    "RenderOptions",
    "RenderResult",
    "RenderedSlide",
    "SlideEditor",
    "ValidationResult",
    "should_recycle_worker",
]
