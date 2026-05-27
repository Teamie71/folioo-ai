from pkgutil import extend_path

from features.visualization.main_client import VisualizationMainClient

__path__ = extend_path(__path__, __name__)

__all__ = ["VisualizationMainClient"]
