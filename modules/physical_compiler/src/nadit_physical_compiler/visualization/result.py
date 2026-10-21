"""Adapt the public result and Device JSON formats to offline playback."""

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ..device import loads_device
from ..serialization import decode_layout, decode_result
from .trace import build_trace


def build_result_trace(
    document: Mapping[str, Any],
    device_document: Mapping[str, Any],
    *,
    title: str = "Saved compilation",
    description: str = "",
) -> dict[str, Any]:
    """Build playback from saved JSON using the public codecs and replay API.

    Args:
        document: A nadit.result/1 compilation result.
        device_document: The Device JSON used for this result.
        title: Viewer label for the saved case.
        description: Optional description shown beside the label.

    Returns:
        A replay trace with copied job, provenance, metrics, and optional
        validation metadata. Loading does not read QASM or invoke compilation.

    Raises:
        ValueError: The result version or encoded data is invalid.
        KeyError: A required result or Device field is absent.
        TypeError: Encoded fields do not match their public constructors.
    """
    result = decode_result(document)
    device = loads_device(json.dumps(dict(device_document)))
    trace = build_trace(
        result.program,
        decode_layout(document["entry_state"]),
        device,
        title=title,
        description=description,
        measurements=result.measurements,
        operations=result.operations,
    )
    trace["compilation"] = deepcopy(
        {
            key: document[key]
            for key in ("job", "provenance", "metrics", "validation")
            if key in document
        }
    )
    return trace
