# Pinned full static pipeline reference

The reference executes MQT QMAP revision
`cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d` in this order:

1. `ASAPScheduler::schedule`, including architecture-derived gate capacity.
2. `VertexMatchingReuseAnalyzer::analyzeReuse`.
3. Complete `HeuristicPlacer::place`, including every initial, gate, and storage
   placement and its internal lookahead.
4. Complete `IndependentSetRouter::route`.
5. Complete `CodeGenerator::generate`, including local rotations, global RY,
   multi-zone CZ, and every rearrangement.

The adapter calls the original stages in the order of `Compiler::compile` and
the place-and-route synthesizer. It does not compile the outer Compiler template,
upstream JSON import, the full QC/NA object libraries, `NAComputation::validate`,
or NAViz export. The QC adapter stores native standard operations and their
targets, controls, and parameters; the NA adapter records operations emitted by
the original code generator. Neither adapter contains a scheduler, matching
algorithm, placement search, router, or rearrangement construction.

All stage implementation bodies and their class declarations are extracted from
fingerprinted source files. Only unused base-class inheritance overrides and
JSON/logging integration are adapted. The source geometry, container hash
expression, candidate ordering, numeric types, search methods, and source bugs
are retained. Explicit target-all RY represents the original global standard
operation form; compound-operation input is not supplied by these fixtures.

## Inputs and trace

`pipeline_fixtures.json` supplies native operations, storage/paired-entanglement
SLMs, and named source options. A paired group contains the two interleaved SLMs
of an entanglement zone. Geometry uses integer micrometres, with all staging
offsets representable in the original integer coordinate calculations.
Explicit `rydberg_range` rectangles are passed unchanged to the original
architecture fields consumed by `CodeGenerator::generate`. Only fixtures without
that field derive coverage from gate-site bounds. The widened-coverage fixture
checks that neither adapter silently substitutes trap bounds.

The paper's `arch_range` is export metadata in the source methods executed here;
it is not treated as an AOD movement-workspace restriction. NADiT still binds
constructed movement plans to its explicitly configured AOD workspace.

The output contains:

- Single-qubit layer input-operation indices and ordered two-qubit gate pairs.
- Original reuse sets, with unordered sets sorted only for serialization.
- The full placement sequence `[initial, gate, storage, gate, storage, ...]`.
- Ordered routing batches between consecutive placements.
- Original zone extents, initial atom positions, and complete generated actions.
- Per-stage elapsed time, measured outside trace serialization, and architecture
  preprocessing time separately.

Raw reports retain the exact numeric input, stdout, stderr, compiler invocation,
source fingerprints, and generated translation-unit fingerprint. Stage snapshots
are also emitted immediately to stderr. A source exception or timeout retains
completed stages and identifies the first stage that did not return. Portable
oracles exclude wall-clock timings.

The default fixture executable uses `-O0`. `--optimization 3` produces a separate
`pipeline-o3` executable for bounded studies; that binary still uses the same
data-only adapters and is not a release build of the complete MQT application.

## Cases and source outcomes

Eleven cases exercise repeated pairs, crossed partners, local U/RZ/RX/RY,
architecture capacity, global RY and barriers, A* strict, IDS strict/relaxed,
local-only circuits, two entanglement zones, and explicitly wider pulse coverage.

Nine complete through original code generation. This count means execution
coverage, not equality with NADiT. Local first-divergence comparisons are reported
separately by the candidate runner.

The original crossed-partner reuse analyzer chooses `{0}` between
`[(0,1),(2,3)]` and `[(0,2),(1,3)]`; the complete-shared-edge local policy can
select more atoms. The oracle preserves the original result.

The global RY fixture also preserves the source generator's storage-only global
zone extent. At the second global RY, the original placement retains atom 0 in
an entanglement zone outside that extent. This is a source output-region
difference, not a reason to rewrite its trace.

Two source outcomes are retained separately:

- `source_initial_slm_row_reset` crosses storage arrays and raises
  `unordered_map::at: key not found` during complete placement. Scheduling and
  reuse completed. The preceding component oracle separately demonstrates the
  source's reverse-fill row-reset error; this full-pipeline trace is not replaced
  with the corrected local result.
- `source_exhausted_multizone_window` does not complete placement within the
  recorded 30-second bound. Source inspection shows its window loop cannot
  terminate after the nearest gate array is exhausted. The same multi-zone
  input with the original `useWindow=false` option completes through generation
  and appears separately as `two_gate_zones`.

These are source failure outcomes, not infeasibility results. Full published
benchmark-scale reproduction is a separate evaluation task.

## Reproduce

Run from `modules/physical_compiler` with the external C++20 compiler:

```powershell
uv run --locked python -m reference.pipeline_reference `
  --cache-dir "$env:TEMP/nadit-qmap-cd8ec913" `
  --cxx "$env:TEMP/nadit-qmap-cd8ec913/compiler-tool/ziglang/zig.exe" `
  --output "$env:TEMP/nadit-qmap-cd8ec913/pipeline-results.json"
```

`--write-oracle PATH` writes the portable oracle. The regular test suite consumes
the actual executed output without requiring network access or a C++ compiler.
`test_pipeline_reference_data.py` checks source identity, stage structure, source
failure preservation, generated movement replay, and zone coverage. These checks
do not replace the source-versus-local stage comparison or physical validation.
