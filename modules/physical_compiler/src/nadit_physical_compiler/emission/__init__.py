"""Direct native-operation binding to Atom IR actions."""

from .actions import emit_layer
from .layer import WholeLayerEmitter

__all__ = ["WholeLayerEmitter", "emit_layer"]
