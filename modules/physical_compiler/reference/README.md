# Pinned QMAP rearrangement reference

The additional [relaxed-router comparison](relaxed_README.md) executes original
compatibility, group-merging, and reversed-axis staging code.

This directory compares the staged transport constructor with **executed
upstream C++**, not a second Python implementation of its algorithm.

The reference is MQT QMAP commit
`cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d` (v3.5.0).
`run_reference.py` verifies source SHA-256 digests, extracts the original
`RearrangementGenerator` declaration and method bodies without changing them,
and compiles them with a small standalone C++20 adapter.

The adapter supplies SLM coordinates, atom identities, and an operation sink.
It does not choose loading order, offsets, staging axes, or unloading order.
Those choices execute the extracted upstream methods. This comparison covers
**one supplied rearrangement batch**, not upstream scheduling, placement,
router batch selection, pulse generation, hardware execution, or quantum-state
simulation. `IndependentSetRouter.cpp` is fetched and fingerprinted as source
evidence but is not executed by this harness.

## Source assumptions

Upstream uses two regular paired SLM arrays for the entanglement zone and a
regular storage array. One batch has a common source zone and target zone, with
at least one endpoint in the entanglement zone. Its staging offsets use SLM
pitches and pair separation; they are not simply AOD minimum line spacing.
The source assumes that row and column mappings do not split or merge.

The integer-coordinate fixtures use nanometres. Storage pitch is 5000 nm;
entanglement pitch is 8000 by 10000 nm with 2000 nm pair separation.
The tested half-, quarter-, and third-pitch expressions are integral.
Normalized traces report micrometres. The fixtures cover row/row, column/column,
and equal-count row/column selection, dense and sparse pickup shapes, both
transport directions, and left/right entanglement sites.

The upstream atom operation stream does not expose every empty AOD line.
Equality of atom traces therefore does not by itself validate the additional
explicit-grid fields in NADiT Atom IR. Those fields require the module's
separate action-legality tests.

## Sparse gate release counterexample

`unsafe_sparse_gate.json` records a separate execution of the same unmodified
C++ methods. Six moving atoms target four gate columns with row masks
`{0}`, `{1}`, `{0, 1}`, and `{2, 3}`; six stationary atoms occupy their paired
gate sites. These inputs use the regular paired-SLM geometry and strictly
ordered, unsplit row and column mappings described above.

At zero-based MOVE event 8, moving atom 2 reaches `(10, 120)` micrometres,
which is occupied by stationary atom 6. Event 11 similarly overlaps moving
atom 3 with stationary atom 9 at `(10, 130)`. The overlap also occurs with
the movers sorted by descending endpoint distance, as the strict router
orders its candidates. The JSON retains the input, both mover orders,
normalized events, exact collisions, and source fingerprints without local
build paths.

This is a counterexample to unrestricted use of the reference constructor
for sparse column release with retained partners. It does not establish a
whole-compiler reproduction, since scheduling and placement are not executed.
It explains NADiT's constructive batch-shape restriction before generation;
source assumptions alone do not rule out this input. This counterexample is
excluded from the nine legal trace-comparison oracles in `expected.json`.

## Running

Use an external C++20 compiler and a temporary build/cache directory. Nothing
is added to the project's Python dependencies.

```powershell
uv run --locked python -m reference.run_candidate `
  --output "$env:TEMP/nadit-staged-candidate.json"

python reference/run_reference.py `
  --cache-dir "$env:TEMP/nadit-qmap-cd8ec913" `
  --cxx "/absolute/path/to/zig.exe" `
  --output "/absolute/path/to/reference-results.json" `
  --expected-output reference/expected.json `
  --candidate-json "$env:TEMP/nadit-staged-candidate.json"
```

Zig is invoked through its C++ driver; another compiler is invoked directly.
For example, `ziglang==0.13.0` can be installed into an independent temporary
tool directory with `uv pip install --target`, without editing `pyproject.toml`.
The harness only needs the standard Python library.

`--candidate-json` optionally accepts a JSON object mapping every fixture name
to its normalized event list. Each event has this form:

```json
{"kind": "move", "positions": [[0, 2.5, 2.5], [1, 7.5, 2.5]]}
```

Event kind/order, affected atom identities, and coordinates are compared.
Coordinates use an absolute tolerance of `1e-9` micrometres. The reference report
also retains raw C++ output, inputs, final endpoints, source fingerprints, the
compiler command, and the compiled adapter fingerprint.
`--expected-output` writes the smaller checked-in oracle: source fingerprints
and a `cases` object mapping fixture names to normalized events.

## Attribution

The extracted code is copyright 2023-2025 Chair for Design Automation, TUM,
and copyright 2025 Munich Quantum Software Company GmbH, under MIT.
The source revision and complete permission notice are recorded in
[the repository third-party notices](../../../docs/third-party-notices.md).

Primary sources:

- [CodeGenerator.cpp](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/code_generator/CodeGenerator.cpp)
- [CodeGenerator.hpp](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/include/na/zoned/code_generator/CodeGenerator.hpp)
- [IndependentSetRouter.cpp](https://github.com/munich-quantum-toolkit/qmap/blob/cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d/src/na/zoned/layout_synthesizer/router/IndependentSetRouter.cpp)
