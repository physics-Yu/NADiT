"""Two integration levels share the same native inputs and compilation result."""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..compiler import CompilationResult
from ..device import Device
from ..execution import MeasurementBackend
from ..physical_ir import PhysicalDAG
from ..placement.catalog import GatePairCatalog
from ..state import AtomLayout


@dataclass(frozen=True, slots=True)
class CompilerContext:
    """Immutable common inputs for admission and construction."""

    dag: PhysicalDAG
    device: Device
    aod_id: str
    catalog: GatePairCatalog | None


@dataclass(frozen=True, slots=True)
class ProgramContext(CompilerContext):
    """Whole-program inputs include the resolved initial atom layout."""

    entry_state: AtomLayout


@runtime_checkable
class ProgramCompiler(Protocol):
    """External complete-schedule adapter; output undergoes mandatory validation."""

    def compile(
        self, context: ProgramContext, options: dict[str, Any], case: MeasurementBackend
    ) -> CompilationResult:
        """Produce a complete result using only supplied inputs and measurement case."""
        ...


class InvalidProgramResult(ValueError):
    """An external schedule failed the common semantic result validator."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(str(report))
