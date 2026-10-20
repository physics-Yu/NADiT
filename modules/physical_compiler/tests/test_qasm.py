"""Behavioral tests for the static OpenQASM frontend."""

from math import pi
from pathlib import Path

import pytest

from nadit_physical_compiler import (
    PhysicalOperation,
    Register,
    load_qasm,
    loads_qasm,
)


def _source(version: int, body: str, qubits: int = 3, clbits: int = 2) -> str:
    """Build a circuit with the standard gate include for either version."""
    if version == 2:
        return (
            'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
            f"qreg q[{qubits}];\ncreg c[{clbits}];\n{body}"
        )
    return (
        'OPENQASM 3.0;\ninclude "stdgates.inc";\n'
        f"qubit[{qubits}] q;\nbit[{clbits}] c;\n{body}"
    )


@pytest.mark.parametrize("version", [2, 3])
def test_independent_operations_keep_source_ids_and_wire_dependencies(
    version: int,
) -> None:
    dag = loads_qasm(_source(version, "h q[1];\nx q[0];\nz q[1];"))

    assert dag.nodes == (
        PhysicalOperation(0, "h", (1,), (), ()),
        PhysicalOperation(1, "x", (0,), (), ()),
        PhysicalOperation(2, "z", (1,), (), ()),
    )
    assert dag.edges == ((0, 2),)


@pytest.mark.parametrize("version", [2, 3])
def test_two_qubit_gate_joins_dependencies_and_edges_are_unique(version: int) -> None:
    dag = loads_qasm(_source(version, "h q[0];\nx q[1];\ncx q[0],q[1];\ncz q[0],q[1];"))

    assert [node.name for node in dag.nodes] == ["h", "x", "cx", "cz"]
    assert dag.nodes[2].qubits == (0, 1)
    assert dag.edges == ((0, 2), (1, 2), (2, 3))


@pytest.mark.parametrize("version", [2, 3])
def test_partial_barrier_does_not_serialize_unrelated_qubits(version: int) -> None:
    dag = loads_qasm(
        _source(version, "h q[0];\nx q[1];\nbarrier q[0];\nz q[0];\ny q[1];")
    )

    assert [node.name for node in dag.nodes] == ["h", "x", "barrier", "z", "y"]
    assert dag.nodes[2].qubits == (0,)
    assert dag.edges == ((0, 2), (1, 4), (2, 3))


@pytest.mark.parametrize("version", [2, 3])
def test_measurement_reset_and_repeated_classical_write_dependencies(
    version: int,
) -> None:
    measurement = (
        "measure q[0] -> c[0];\nmeasure q[1] -> c[0];"
        if version == 2
        else "c[0] = measure q[0];\nc[0] = measure q[1];"
    )
    dag = loads_qasm(_source(version, measurement + "\nreset q[0];"))

    assert dag.nodes == (
        PhysicalOperation(0, "measure", (0,), (0,), ()),
        PhysicalOperation(1, "measure", (1,), (0,), ()),
        PhysicalOperation(2, "reset", (0,), (), ()),
    )
    assert dag.edges == ((0, 1), (0, 2))


@pytest.mark.parametrize("version", [2, 3])
def test_idle_wires_and_multiple_registers_preserve_flat_indices(version: int) -> None:
    if version == 2:
        source = (
            'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
            "qreg a[2]; qreg b[3]; creg first[1]; creg second[2];\n"
            "h b[1]; measure a[1] -> second[1];"
        )
    else:
        source = (
            'OPENQASM 3.0;\ninclude "stdgates.inc";\n'
            "qubit[2] a; qubit[3] b; bit[1] first; bit[2] second;\n"
            "h b[1]; second[1] = measure a[1];"
        )
    dag = loads_qasm(source)

    assert (dag.num_qubits, dag.num_clbits) == (5, 3)
    assert dag.quantum_registers == (Register("a", (0, 1)), Register("b", (2, 3, 4)))
    assert dag.classical_registers == (
        Register("first", (0,)),
        Register("second", (1, 2)),
    )
    assert dag.nodes[0].qubits == (3,)
    assert dag.nodes[1].qubits == (1,)
    assert dag.nodes[1].clbits == (2,)
    assert dag.edges == ()


@pytest.mark.parametrize("version", [2, 3])
def test_numeric_parameter_expressions_are_evaluated(version: int) -> None:
    dag = loads_qasm(_source(version, "rx(pi/2) q[0];\nrz(-pi/4 + pi/8) q[0];"))

    assert dag.nodes[0].params == pytest.approx((pi / 2,))
    assert dag.nodes[1].params == pytest.approx((-pi / 8,))
    assert dag.edges == ((0, 1),)


@pytest.mark.parametrize("version", [2, 3])
def test_nested_parameterized_custom_gates_expand_without_native_lowering(
    version: int,
) -> None:
    body = (
        "gate rotate(theta) a { rz(theta) a; }\n"
        "gate pair(theta) a,b { rotate(theta) a; cx a,b; }\n"
        "pair(pi/2) q[0],q[1];"
    )
    dag = loads_qasm(_source(version, body))

    assert [node.name for node in dag.nodes] == ["rz", "cx"]
    assert dag.nodes[0].params == pytest.approx((pi / 2,))
    assert dag.nodes[1].qubits == (0, 1)
    assert dag.edges == ((0, 1),)


@pytest.mark.parametrize("version", [2, 3])
def test_custom_gate_named_like_standard_gate_keeps_its_actual_definition(
    version: int,
) -> None:
    declaration = "qreg q[1];" if version == 2 else "qubit[1] q;"
    source = (
        f"OPENQASM {version}.0;\ngate h a {{ U(0,0,pi) a; }}\n{declaration}\nh q[0];"
    )
    dag = loads_qasm(source)

    assert len(dag.nodes) == 1
    assert dag.nodes[0].name == "u"
    assert dag.nodes[0].params == pytest.approx((0, 0, pi))


@pytest.mark.parametrize("version", [2, 3])
def test_register_wide_operations_expand_into_individual_operations(
    version: int,
) -> None:
    measurement = "measure q -> c;" if version == 2 else "c = measure q;"
    dag = loads_qasm(_source(version, f"h q;\n{measurement}", qubits=2, clbits=2))

    assert [node.name for node in dag.nodes] == ["h", "h", "measure", "measure"]
    assert [node.qubits for node in dag.nodes] == [(0,), (1,), (0,), (1,)]
    assert [node.clbits for node in dag.nodes] == [(), (), (0,), (1,)]
    assert dag.edges == ((0, 2), (1, 3))


@pytest.mark.parametrize("version", [2, 3])
def test_empty_circuit_preserves_declared_wires(version: int) -> None:
    dag = loads_qasm(_source(version, ""))

    assert (dag.num_qubits, dag.num_clbits) == (3, 2)
    assert dag.nodes == ()
    assert dag.edges == ()
    assert dag.global_phase == 0.0


def test_qasm3_global_phase_is_preserved() -> None:
    dag = loads_qasm(_source(3, "gphase(pi/4); h q[0];"))

    assert dag.global_phase == pytest.approx(pi / 4)
    assert [node.name for node in dag.nodes] == ["h"]


@pytest.mark.parametrize(
    "source",
    [
        "",
        "OPENQASM 4.0; qubit q;",
        _source(2, "h q[0]"),
        _source(3, "h q[99];"),
        _source(2, "unknown q[0];"),
    ],
)
def test_invalid_qasm_raises_contextual_value_error(source: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        loads_qasm(source)


@pytest.mark.parametrize(
    "source",
    [
        _source(2, "if(c==1) x q[0];"),
        _source(3, "if (c[0]) { x q[0]; }"),
        _source(3, "for int i in [0:1] { x q[i]; }"),
        _source(3, "input angle theta; rx(theta) q[0];"),
        "OPENQASM 2.0;\nopaque mystery a;\nqreg q[1];\nmystery q[0];",
        _source(3, "delay[10ns] q[0];"),
    ],
)
def test_unsupported_semantics_raise_value_error(source: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        loads_qasm(source)


@pytest.mark.parametrize("version", [2, 3])
def test_file_loading_matches_string_loading(tmp_path: Path, version: int) -> None:
    source = _source(version, "h q[0]; cx q[0],q[1];")
    path = tmp_path / f"circuit{version}.qasm"
    path.write_text(source, encoding="utf-8")

    assert load_qasm(path) == loads_qasm(source)
    assert load_qasm(str(path)) == loads_qasm(source)


def test_file_io_failure_remains_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_qasm(tmp_path / "missing.qasm")


def test_qasm3_malformed_statement_raises_value_error() -> None:
    with pytest.raises(ValueError, match=r".+"):
        loads_qasm(_source(3, "h q[0"))


def test_qasm2_file_rejects_custom_include_relative_to_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "circuits"
    include_dir = source_dir / "gates"
    include_dir.mkdir(parents=True)
    (include_dir / "prepare.inc").write_text(
        "gate prepare(theta) a { h a; rz(theta) a; }\n", encoding="utf-8"
    )
    source_path = source_dir / "example.qasm"
    source_path.write_text(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
        'include "gates/prepare.inc";\nqreg q[1];\nprepare(pi/4) q[0];',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError) as error:
        load_qasm(source_path)

    assert str(source_path) in str(error.value)
    assert "standard" in str(error.value).lower()
    assert "include" in str(error.value).lower()


@pytest.mark.parametrize("version", [2, 3])
def test_file_parse_error_identifies_source_path(tmp_path: Path, version: int) -> None:
    source_path = tmp_path / "invalid.qasm"
    source_path.write_text(_source(version, "h q[0"), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_qasm(source_path)

    assert str(source_path) in str(error.value)


@pytest.mark.parametrize("version", [2, 3])
def test_utf8_bom_and_leading_comments_preserve_circuit(
    tmp_path: Path, version: int
) -> None:
    source = _source(version, "h q[0];")
    decorated = "\ufeff// Circuit metadata.\n// Static input.\n" + source
    source_path = tmp_path / "bom.qasm"
    source_path.write_text(decorated, encoding="utf-8")

    assert loads_qasm(decorated) == loads_qasm(source)
    assert load_qasm(source_path) == loads_qasm(source)


def test_qasm3_hardware_qubit_addressing_is_rejected() -> None:
    source = 'OPENQASM 3.0;\ninclude "stdgates.inc";\nx $0;'

    with pytest.raises(ValueError, match=r".+"):
        loads_qasm(source)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("from_file", [False, True])
def test_invalid_parameter_arithmetic_reports_value_error_with_source_context(
    tmp_path: Path, version: int, from_file: bool
) -> None:
    source = _source(version, "rx(1/0) q[0];")
    source_path = tmp_path / "invalid_parameter.qasm"
    source_path.write_text(source, encoding="utf-8")
    context = str(source_path) if from_file else "<string>"

    with pytest.raises(ValueError) as error:
        if from_file:
            load_qasm(source_path)
        else:
            loads_qasm(source)

    assert context in str(error.value)


@pytest.mark.parametrize("version", [2, 3])
def test_existing_absolute_include_is_rejected(tmp_path: Path, version: int) -> None:
    include_path = tmp_path / "custom.inc"
    include_path.write_text("gate custom a { h a; }\n", encoding="utf-8")
    source_path = tmp_path / "absolute_include.qasm"
    source_path.write_text(
        _source(version, f'include "{include_path.as_posix()}"; custom q[0];'),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        load_qasm(source_path)

    assert str(source_path) in str(error.value)
    assert "standard" in str(error.value).lower()
    assert "include" in str(error.value).lower()


@pytest.mark.parametrize("version", [2, 3])
def test_include_text_inside_comments_is_ignored(version: int) -> None:
    comment = '// include "custom.inc";\n'
    if version == 3:
        comment += '/* include "/not/a/library.inc"; */\n'
    source = _source(version, "h q[0];")

    assert loads_qasm(comment + source) == loads_qasm(source)


@pytest.mark.parametrize("version", [2, 3])
def test_standard_include_accepts_comments_at_supported_positions(version: int) -> None:
    library = "qelib1.inc" if version == 2 else "stdgates.inc"
    source = _source(version, "h q[0];")
    include = (
        f'include // Standard gates.\n "{library}";'
        if version == 2
        else f'/* Standard gates. */ include "{library}";'
    )
    decorated = source.replace(f'include "{library}";', include)

    assert loads_qasm(decorated) == loads_qasm(source)


@pytest.mark.parametrize("version", [2, 3])
def test_string_custom_include_is_rejected_even_if_present_in_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    (tmp_path / "custom.inc").write_text("gate custom a { h a; }\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    source = _source(version, 'include "custom.inc"; custom q[0];')

    with pytest.raises(ValueError) as error:
        loads_qasm(source)

    assert "<string>" in str(error.value)
    assert "standard" in str(error.value).lower()
    assert "include" in str(error.value).lower()


def test_qasm3_standard_include_accepts_single_quotes() -> None:
    source = _source(3, "h q[0];")
    decorated = source.replace('"stdgates.inc"', "'stdgates.inc'")

    assert loads_qasm(decorated) == loads_qasm(source)


def test_qasm3_external_include_in_single_quotes_is_rejected() -> None:
    source = _source(3, "include 'custom.inc';")

    with pytest.raises(ValueError) as error:
        loads_qasm(source)

    assert "<string>" in str(error.value)
    assert "standard" in str(error.value).lower()
    assert "include" in str(error.value).lower()
