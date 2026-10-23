# ADR 0036: Packaged saved-result viewer and experiment exports

Status: Implemented and verified.

## Decision

Add `nadit view RESULT.json [RESULT2.json ...] -o viewer.html [--device ...]`.
Keep the visualization package's three public exports: build_trace,
build_result_trace and write_html. File loading/CLI orchestration lives in an
internal visualization.cli module. Viewing never reads QASM, recompiles or
samples results. Optional `--json` writes the same trace data separately.

Version-2 results use embedded Device geometry unless explicitly overridden.
Version-1 results resolve inline job.device or a path relative to the result.
Each HTML remains self-contained, including all renderer assets and trace data.

`experiment summarize --viewers` exports only the existing anomaly selection.
Each selected run records an output path or an explicit missing/error status.
A bad result does not prevent exporting other runs or writing the summary.
viewer_command uses installed `nadit view`; viewer_argv is the machine-readable
invocation. Generation reuses the same saved-result exporter as the CLI.

Examples generate demo inputs and compile via job/registry, save v2 results,
then invoke the packaged CLI. Remove saved-result viewing from the demo script;
migrate its saved-result tests without weakening their data/feedback assertions.

## Validation

Cover multiple results, v1 geometry resolution, v2 independence from original
input paths, overrides, JSON export and output/input collision rejection.
Cover anomaly export success, absent files, unsaved results, invalid documents
and default no-export behavior. Test an installed wheel from an unrelated cwd,
with no examples package. Preserve demo feedback, static circuit, legality and
replay coverage. Baseline: 1,770 Python tests and 33 frontend tests.

Verification: 1,776 Python tests passed, with the original assertions retained
or migrated to the new CLI's saved-result contract. All 33 frontend tests and
ruff/mypy checks passed. Both feedback demos and the 60-atom demo were exported
through the job/registry-to-result-to-CLI path.

A wheel was built and installed into an isolated target. Its actual nadit.exe
exported two saved results from an unrelated temporary working directory with
examples unavailable. Assets were present in the wheel. The two 98-atom traces
(1,301 and 836 steps) produced 26,323,785 HTML bytes in one 2.83 s observation.
This measures export only, not browser load/frame/seek performance. Evidence:
`modules/physical_compiler/artifacts/view-cli-package/verification.json`.

## MSD scale boundary

Full inline HTML remains the implemented mode. Do not introduce an unmeasured
atom-count threshold or claim d=7 factory scalability from the small demos.
Future projection/loading belongs between result replay and the renderer:

- Time windows need a checkpoint at their start, intersecting actions (including
  moves/pulses already in progress), original timestamps and feedback availability.
- Patch selection needs explicit atom/qubit membership and treatment of cross-
  patch gates, AOD lines and off-patch context. Never infer patches from IDs alone.
- Loss at a window start must retain accumulated exposure; a subset must label
  its probability denominator. Cropping must not recompute physical execution.
- Chunk manifests need schema/source identity, deterministic boundaries and
  cached checkpoints. Optional chunked delivery is separate from offline HTML;
  file-origin fetching is not assumed to work.

Before implementing these modes, measure export time, peak memory, HTML size,
initial load, frame time and seek latency using representative factory results.
No streaming, patch/time clipping or compiler algorithm changes are part of
this change.
