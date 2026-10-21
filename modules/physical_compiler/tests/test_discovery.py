"""Discovery tracks registrations and CLI JSON Schema output."""

import json

import pytest

from nadit_physical_compiler.cli import main
from nadit_physical_compiler.discovery import describe
from nadit_physical_compiler.registry import REGISTRY
from nadit_physical_compiler.schemas.job import job_schema


def test_description_covers_every_registration_without_shared_mutable_defaults() -> (
    None
):
    document = describe()
    assert document["schemas"] == {
        "job": "nadit.job/1",
        "result": "nadit.result/2",
        "readable_results": ["nadit.result/1", "nadit.result/2"],
    }
    assert set(document["architectures"]) == {"zoned/v1"}
    for stage, kinds in REGISTRY.items():
        assert set(document["strategies"][stage]) == set(kinds)
        for kind, registered in kinds.items():
            described = document["strategies"][stage][kind]
            assert described["defaults"] == registered.defaults
            assert described["supports"]
            assert set(described["options"]["properties"]) == {
                "kind",
                *registered.defaults,
            }
    document["strategies"]["placement"]["ids"]["defaults"]["trials"] = -20
    assert REGISTRY["placement"]["ids"].defaults["trials"] == 4


@pytest.mark.parametrize("schema", [False, True])
def test_describe_cli_emits_exact_document(
    schema: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["describe", *(["--json-schema", "job"] if schema else [])]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == (job_schema() if schema else describe())


def test_job_schema_has_all_device_forms_and_optional_catalogue() -> None:
    schema = job_schema()
    assert "gate_zones" not in schema["required"]
    assert len(schema["properties"]["device"]["oneOf"]) == 3
    assert schema["additionalProperties"] is False
    assert schema["$schema"].endswith("2020-12/schema")
