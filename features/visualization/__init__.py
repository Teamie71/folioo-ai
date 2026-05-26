from importlib import import_module
from pathlib import Path

from features import __path__ as _features_paths

for _features_path in _features_paths:
    _candidate = Path(_features_path) / "visualization"
    if _candidate.is_dir() and str(_candidate) not in __path__:
        __path__.append(str(_candidate))

VisualizationMainClient = import_module(
    "features.visualization.main_client"
).VisualizationMainClient

__all__ = ["VisualizationMainClient"]
