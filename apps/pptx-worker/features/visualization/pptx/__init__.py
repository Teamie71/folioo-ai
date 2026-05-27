"""PPTX 렌더링 유틸리티."""

from importlib import import_module
from pathlib import Path

from features.visualization import __path__ as _visualization_paths

for _visualization_path in _visualization_paths:
    _candidate = Path(_visualization_path) / "pptx"
    if _candidate.is_dir() and str(_candidate) not in __path__:
        __path__.append(str(_candidate))

_soffice_render = import_module("features.visualization.pptx.soffice_render")

DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE = _soffice_render.DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE
InMemoryConversionCounter = _soffice_render.InMemoryConversionCounter
PptxRenderError = _soffice_render.PptxRenderError
PptxRenderer = _soffice_render.PptxRenderer
RenderOptions = _soffice_render.RenderOptions
RenderResult = _soffice_render.RenderResult
RenderedSlide = _soffice_render.RenderedSlide
should_recycle_worker = _soffice_render.should_recycle_worker

__all__ = [
    "DEFAULT_MAX_CONVERSIONS_BEFORE_RECYCLE",
    "InMemoryConversionCounter",
    "PptxRenderError",
    "PptxRenderer",
    "RenderOptions",
    "RenderResult",
    "RenderedSlide",
    "should_recycle_worker",
]
