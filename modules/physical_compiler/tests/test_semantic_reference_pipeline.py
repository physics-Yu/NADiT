"""Original pipeline fixtures also pass the independent semantic validator."""

from typing import Any

import pytest

from nadit_physical_compiler import AtomLayout, CompilationResult, Device, PhysicalDAG
from nadit_physical_compiler.compiler import CompilationStrategy, compile_physical
from nadit_physical_compiler.validation import validate_result
from reference import pipeline_candidate


@pytest.mark.parametrize(
    "case", pipeline_candidate.load_cases(), ids=lambda case: case["name"]
)
def test_source_profile_fixture_is_semantically_valid(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    checked = []

    def compile_and_validate(
        dag: PhysicalDAG,
        device: Device,
        *,
        strategy: CompilationStrategy,
        entry_state: AtomLayout,
    ) -> CompilationResult:
        result = compile_physical(
            dag, device, strategy=strategy, entry_state=entry_state
        )
        report = validate_result(
            result, dag=dag, device=device, entry_state=entry_state
        )
        assert report["ok"], report
        checked.append(report)
        return result

    monkeypatch.setattr(pipeline_candidate, "compile_physical", compile_and_validate)
    pipeline_candidate.run_case(case)
    assert len(checked) == 1
