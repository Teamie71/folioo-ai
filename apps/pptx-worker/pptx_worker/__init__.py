"""PPTX 시각화 워커 애플리케이션."""

from importlib import import_module


def __getattr__(name: str):
    """앱 진입점은 명시적으로 요청될 때만 로드한다."""
    if name != "create_app":
        raise AttributeError(f"{__name__!r} 모듈에 {name!r} 속성이 없습니다.")
    module = import_module("pptx_worker.main")
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = ["create_app"]
