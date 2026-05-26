"""PPTX OOXML 편집 유틸리티."""

from importlib import import_module
from pathlib import Path

from features.visualization import __path__ as _visualization_paths

for _visualization_path in _visualization_paths:
    _candidate = Path(_visualization_path) / "pptx"
    if _candidate.is_dir() and str(_candidate) not in __path__:
        __path__.append(str(_candidate))

_slide_editor = import_module("features.visualization.pptx.slide_editor")
SlideEditor = _slide_editor.SlideEditor

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
    "DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE",
    "InMemoryConversionCounter",
    "PptxRenderError",
    "PptxRenderer",
    "RenderOptions",
    "RenderResult",
    "RenderedSlide",
    "SlideEditor",
    "should_recycle_worker",
]
