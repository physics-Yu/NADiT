"""A third, file-backed program adapter needs no job or experiment branches."""

import json
from pathlib import Path
from typing import Any

import pytest
from test_job import MODULE, spec

from nadit_physical_compiler.compiler import CompilationResult
from nadit_physical_compiler.compilers.contracts import (
    CompilerContext,
    InvalidProgramResult,
    ProgramContext,
)
from nadit_physical_compiler.compilers.registry import (
    COMPILERS,
    Compiler,
    CompilerRegistration,
)
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.execution import MeasurementBackend
from nadit_physical_compiler.job import compile_job, loads_job
from nadit_physical_compiler.schemas.job import job_schema
from nadit_physical_compiler.serialization import decode_result, encode_result
from nadit_physical_compiler.support import UnsupportedJob


class FileCompiler:
    def compile(
        self, context: ProgramContext, options: dict[str, Any], case: MeasurementBackend
    ) -> CompilationResult:
        return decode_result(json.loads(Path(options["schedule"]).read_text()))


def test_whole_program_adapter_and_mandatory_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    circuit = tmp_path / "static.qasm"
    circuit.write_text('OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; cz q[0],q[1];')
    document = spec(pair=True)
    document["circuit"]["qasm"] = str(circuit)
    job = loads_job(json.dumps(document), base_dir=MODULE)
    result = compile_job(job)
    saved = encode_result(
        result,
        entry_state=job.entry_state,
        job=job.document,
        dag=job.dag,
        device=job.device,
        inputs=job.inputs,
    )
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(saved))

    def build(
        context: CompilerContext, options: dict[str, Any]
    ) -> tuple[Compiler, dict[str, Any]]:
        return FileCompiler(), options

    monkeypatch.setitem(
        COMPILERS,
        "file_adapter",
        CompilerRegistration(
            "file_adapter",
            "fixture-1",
            lambda: {
                "type": "object",
                "properties": {
                    "kind": {"const": "file_adapter"},
                    "schedule": {"type": "string"},
                },
                "required": ["kind", "schedule"],
                "additionalProperties": False,
            },
            ("explicit",),
            lambda context, options: [],
            build,
            supports_feedback=False,
        ),
    )
    document["strategy"] = {"kind": "file_adapter", "schedule": str(path)}
    adapted = loads_job(json.dumps(document), base_dir=MODULE)
    assert compile_job(adapted) == result
    assert "file_adapter" in describe()["compilers"]
    assert any(
        s["properties"]["kind"].get("const") == "file_adapter"
        for s in job_schema()["properties"]["strategy"]["oneOf"]
    )
    for step in saved["program"]["steps"]:
        for action in step["actions"]:
            action["duration_us"] = 0
    path.write_text(json.dumps(saved))
    with pytest.raises(InvalidProgramResult, match="duration_mismatch"):
        compile_job(adapted)
    document["circuit"]["qasm"] = str(MODULE / "examples/pair_feedback.qasm3")
    with pytest.raises(UnsupportedJob, match="feedback"):
        loads_job(json.dumps(document), base_dir=MODULE)
