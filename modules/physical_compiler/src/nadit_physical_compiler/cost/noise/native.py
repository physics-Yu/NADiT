"""JSON records of upstream results, including channels outside Pauli models."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from typing import Any


def encode_native(value: Any) -> Any:
    """Encode upstream dataclasses, mappings and arrays without dropping fields.

    Complex arrays retain dtype/shape and real/imaginary planes. Dataclasses
    retain their type and fields, including the native quantum-action type.
    Unknown objects raise TypeError instead of becoming an unparseable repr.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            **{
                field.name: encode_native(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, Mapping):
        return {key: encode_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_native(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if hasattr(value, "dtype") and hasattr(value, "shape"):
        if value.ndim == 0:
            return encode_native(value.item())
        record = {
            "encoding": "ndarray",
            "dtype": str(value.dtype),
            "shape": list(value.shape),
        }
        if value.dtype.kind == "c":
            record.update(real=value.real.tolist(), imag=value.imag.tolist())
        else:
            record["data"] = value.tolist()
        return record
    raise TypeError(f"Unsupported upstream result value: {type(value).__name__}.")
