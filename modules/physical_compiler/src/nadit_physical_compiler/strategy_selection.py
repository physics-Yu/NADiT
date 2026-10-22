"""Public compiler selection delegated to the registered compiler catalogue."""

from typing import Any

from .compilers.contracts import CompilerContext
from .compilers.registry import Compiler, select
from .device import Device
from .physical_ir import PhysicalDAG
from .placement import GatePairCatalog

type SelectedStrategy = Compiler


def select_strategy(
    spec: dict[str, Any],
    *,
    dag: PhysicalDAG,
    device: Device,
    aod_id: str,
    catalog: GatePairCatalog | None,
    entry: dict[str, Any] | None = None,
) -> tuple[SelectedStrategy, dict[str, Any]]:
    """Admit and build a compiler with resolved options and an explicit kind.

    Raises:
        ValueError: Invalid compiler name or options.
        UnsupportedJob: Compiler, device, entry, or feedback is unsupported.
    """
    return select(CompilerContext(dag, device, aod_id, catalog), spec, entry=entry)
