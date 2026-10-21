"""Post-processing cost contracts and report identities."""

from collections.abc import Mapping
from typing import Any, Protocol

from ..activity import AtomActivity
from ..provenance import canonical_hash

type CostReport = dict[str, Any]


class UnvalidatedResult(ValueError):
    """A result has not passed semantic pulse and timing validation."""


class CostModel(Protocol):
    """A model reads shared activity; it never invokes a compiler."""

    name: str
    version: str

    def evaluate(
        self,
        activity: AtomActivity,
        result: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> CostReport:
        """Estimate cost for the given activity and immutable parameter set."""
        ...


def require_validated(result: Mapping[str, Any]) -> None:
    """Require v2 semantic evidence before accepting any cost calculation."""
    report = result.get("validation", {})
    if (
        result.get("schema") != "nadit.result/2"
        or report.get("ok") is not True
        or not {"pulse", "timing"} <= set(report.get("checks", []))
        or report.get("semantic") != "checked"
    ):
        raise UnvalidatedResult(
            "Cost requires a semantically validated nadit.result/2 "
            "with pulse and timing checks."
        )


def result_hash(result: Mapping[str, Any]) -> str:
    """Identify execution content, independently of appended cost/provenance."""
    return canonical_hash(
        {
            key: result[key]
            for key in (
                "schema",
                "program",
                "entry_state",
                "exit_state",
                "dag",
                "device",
                "operations",
                "measurements",
            )
        }
    )
