# 0032: Unified compiler selection and conformance

Status: Core interfaces implemented; consolidation remains open. Validation
scope clarified on 2026-10-22. Test results below are historical evidence,
not a claim that every interface requirement is complete.

Implementation baseline: ADR 0033 already accepts named Search Smarter and
adds optional fixed-reference QEC mode. Preserve that mode and its 1,690-test
baseline. The findings below describe the earlier review snapshot. Resolved
jobs now gain an explicit compiler kind; tests asserting the former document
shape will be updated to this approved contract, preserving action assertions.

## Context

ADR 0030 added a second compiler, `nonzoned`, beside the Search Smarter stage
pipeline, and ADR 0031 added the `enola_initial` entry provider. A review on
2026-10-22 checked whether compilers can be swapped behind one interface, for
single jobs and for experiments (ADR 0027).

### What already works

- Both compilers implement `CompilationStrategy` (`select`, `lower`,
  `lookahead_enabled`). `compile_physical` owns DAG progress, feedback,
  measurement sampling, and timing for both.
- Outputs are compiler-neutral. Result documents, semantic validation,
  metrics, cost models, and the viewer work unchanged. The `nonzoned`,
  `nonzoned-enola-initial`, and `multipair` example jobs all compile and pass
  `nadit validate` on their saved results.
- Unsupported device/compiler pairs return exit code 4 with reasons. For
  example, `nonzoned` on a zoned device without `cz.allow_spectators`, and the
  Search Smarter `multipair` placer on the shared-array device, are both
  rejected before compilation.
- The full suite passes (1,661 tests in the review environment; the remaining
  failures depend on an installed package and are environmental).

### Findings

**F1: compiler selection is a hard-coded branch, not a registry.**
`strategy_selection.select_strategy` treats a strategy object without `kind`
as Search Smarter and accepts only `kind == "nonzoned"` otherwise. The default
compiler has no name: `{"kind": "search_smarter", ...stages}` is rejected, with
the misleading error `strategy.scheduler: unknown field.` Adding a third
compiler requires editing `select_strategy`, the `SelectedStrategy` type,
`schemas/job.py`, and `discovery.py`.

**F2: compiler-specific admission is scattered.**
`job.py` has two `if ... == "nonzoned"` blocks: a capability pre-check and an
entry-provider restriction. `select_strategy` repeats the same capability
check. The Search Smarter stages declare support through `Registration.supports`;
`nonzoned` uses ad-hoc `UnsupportedJob` construction. In `describe()`, the
`compiler_strategies` section is a hand-written dictionary with free-text
support descriptions, separate from the registry that generates the stage
options.

**F3: entry-provider compatibility is undeclared and not per compiler.**
- `nonzoned` requires `explicit` or `enola_initial`, and this rule is
  hard-coded in `job.py`.
- Search Smarter silently accepts `enola_initial`. That combination has no
  documented meaning.
- Experiments carry one global `entry`, so every compiler in a study gets the
  same provider:
  - with the default `initial_storage`, every `nonzoned` run is unsupported;
  - with `enola_initial`, `nonzoned` runs, but Search Smarter's behaviour
    changes. On the feedback circuit with `ids_strict`, duration went from
    1500.0 to 1644.9 µs (bit 0) and atom loads from 13 to 17.

  The entry provider is therefore a confounder in any cross-compiler study.

**F4: experiment budgets assume Search Smarter's internal layout.**
`budget["placement.max_nodes"]` is written by path into every strategy. Any
experiment that includes `nonzoned` fails at load with
`sweep.placement.max_nodes: parent path does not exist.` The error is also
mislabelled as `sweep`. Without that key, `nonzoned` has no search budget at
all; the experiment does not record this.

**F5: no architecture family for the non-zoned compiler.**
Experiments accept only generated families, and only `zoned/v1` exists. The
shared-array device is a hand-written file (`examples/devices/nonzoned.json`),
so its parameters cannot be swept. A zoned device with `cz.allow_spectators`
can host `nonzoned`, but with the home cells inside the gate row. That is not
the architecture ADR 0030 describes.

**F6: compiler identity is missing from results.**
A resolved Search Smarter strategy has no `kind`. Results, run records, and
summaries identify the compiler only through the user's label in the
experiment spec. Neither compiler records a version.

**F7: one integration level only.**
`CompilationStrategy` is a layer-by-layer protocol driven by the NADiT
controller. Its signature carries Search Smarter concepts (`following`,
`retained_atoms`, `lookahead_enabled`). External whole-program compilers,
such as wrapped ZAC, QMAP, or Enola runs that produce a complete schedule,
have no adapter. They can only be integrated by re-implementing
`select`/`lower`.

**F8: open items from the 0023–0027 review affect cross-compiler comparison.**
- Validation still does not check action durations against Device timing
  (`duration_mismatch`). A program whose transport durations are set to zero,
  with consistent records, passes when no feedback latency exposes it.
- An IDS search with no completed goal is still recorded as `compile_error`
  with `budget_hit: null`.

When compilers are compared on duration, the first item decides the
comparison: an incorrect compiler can win.

## Decision

### Construction and validation boundary

Built-in compilers construct legal actions through shared movement rules,
conflict maps, capacity, and occupancy constraints. Normal compilation trusts
those construction guarantees. Do not add a second full legality pass inside
layer lowering or transport generation. Full checkers serve tests, debugging,
and explicitly requested result audits.

The compiler consumes the supplied measurement backend and selects the branch
from that result. Do not resample or call the backend again at runtime merely
to verify its answer. Same-input output comparison tests determinism; fixed
override cases test that an adapter consumes the supplied measurement case.

The subsequent audit's case-mismatch and conflicting-parallel-transport
concerns are external-adapter conformance test cases. They were not observed
failures of the built-in constructors and do not require new mandatory runtime
checks on their output. Keep the existing whole-program import validation
boundary distinct from the built-in construction path; this clarification
does not change its current implementation.

Consolidation should address concrete interface issues: entry-aware summary
grouping, entry-optimization timing, duplicate capability admission, budget-hit
reporting, and compiler-independent conformance fixtures. Correct those issues
without expanding runtime validation to cover hypothetical adapter mistakes.

### Compiler registry

Add `compilers/registry.py`:

```python
@dataclass(frozen=True)
class CompilerRegistration:
    name: str                      # "search_smarter", "nonzoned"
    version: str                   # bumped when emitted programs can change
    options_schema: Callable[[], dict[str, Any]]
    defaults: dict[str, Any]
    entry_providers: tuple[str, ...]        # allowed, first is the default
    admit: Callable[[CompilerContext, dict[str, Any]], Support]
    build: Callable[[CompilerContext, dict[str, Any]], CompilationStrategy]
    budgets: dict[str, str]        # neutral budget name -> option path
```

`CompilerContext` holds the DAG, Device, AOD ID, and catalogue. `admit`
evaluates every capability and returns all failed reasons, using the existing
`Support` type. `build` constructs the strategy only after admission passes.

Register:
- `search_smarter`, with the five stages as options (validated by the
  existing stage `REGISTRY`), entry providers `("initial_storage",
  "explicit")`, and budgets `{"search_nodes": "placement.max_nodes"}` for
  `ids` and `astar`;
- `nonzoned`, with option `max_cz_gates`, entry providers
  `("enola_initial", "explicit")`, and no budgets.

### Job format

`strategy.kind` names the compiler. A strategy without `kind` is read as
`search_smarter` for compatibility, and the resolved job always writes `kind`.
The canonical forms are:

```json
{"kind": "search_smarter", "scheduler": {...}, "placement": {...}, "transport": {...}, "emitter": {...}, "reuse": {...}}
{"kind": "nonzoned", "max_cz_gates": 4}
```

When `entry` is omitted, the compiler's default provider is used. An entry
provider not in the compiler's list is reported by `admit` as unsupported,
with exit code 4. All compiler-specific branches leave `job.py`. Unknown
compiler names report `strategy.kind`, not a nested field.

### Results and discovery

Result provenance and run records gain `compiler: {"kind", "version"}`.
Summaries group and label by compiler as well as by strategy label.
`describe()["compilers"]` and the job JSON Schema are generated from the
registry: options, defaults, entry providers, budgets, and the docstrings of
the support predicates.

### Experiments

- A strategy entry in an experiment spec may be a file, as now, or
  `{"strategy": ..., "entry": ...}`. Without `entry`, the compiler's default
  provider is used. A global `entry` remains as an explicit override, and the
  plan output reports when an override changes a compiler's default.
- `budget` keys are neutral: `timeout_s` and `search_nodes`. Each compiler maps
  them through `budgets`. A budget the compiler does not use is recorded as
  `not_applicable` in the run record and is not an error. Path overrides move
  to `strategy_overrides: {label: {path: value}}`. Errors name the real field.
- Statuses gain `search_exhausted` for bounded searches that end without a
  goal (IDS) or at a node limit (A*). `budget_hit` is true for both.

### Architecture

Add architecture family `shared/v1` for fixed-home layouts: one or more pair
arrays as the shared region, readout zone, AOD, capabilities (including
`allow_spectators`), and timing. Admission then lets experiments sweep both
families, with each compiler declaring which families it supports. Hardware
metrics (`slm_sites`, `gate_pairs`, `zone_area_um2`) make cross-family
comparisons explicit about resource differences.

### Two integration levels

- **Level 1 (layer strategy)**: `CompilationStrategy`, unchanged in signature.
  `following` and `retained_atoms` are documented as optional hints that a
  strategy may ignore. Both current compilers use this level.
- **Level 2 (program backend)**: a new `ProgramCompiler` protocol,
  `compile(context, options, case) -> CompilationResult`, for external tools
  that schedule the whole circuit. Its output must be a valid `nadit.result/2`
  and passes mandatory semantic validation before any metric is used.
  Backends declare whether they support measurement feedback. Static-only
  backends are admitted only for circuits without conditions.

### Validation prerequisite

Implement `duration_mismatch` and `metrics_mismatch` as described in the
review of ADRs 0023–0027 before reporting cross-compiler comparisons:
- Load and Store durations equal Device transfer times;
- Move duration is at least `move_duration` of the largest displacement;
- CZ, rotation, reset, measurement, and global pulses equal their capability
  durations;
- stored metrics equal metrics recomputed from the program.

### Conformance suite

Add `tests/test_compiler_conformance.py`, parametrized over every registered
compiler, with one representative device and circuit set per compiler.
Every compiler must pass:

1. schema and `describe()` entries generated from its registration;
2. admission on a deliberately unsupported device returns all reasons and
   exit code 4, without constructing a strategy;
3. both outcomes of a feedback circuit (when feedback is supported) compile,
   replay, and pass full semantic validation, including duration checks;
4. identical inputs and seeds produce identical result documents;
5. the result records `compiler.kind` and `compiler.version`;
6. a search budget, when declared, is honoured and reported as
   `search_exhausted` or `budget_hit`.

A new compiler is accepted when it registers and passes this suite, with no
edits to `job.py`, `strategy_selection.py`, or experiment code.

## Alternatives

Keeping the `kind`-absent default avoids a schema change but leaves the
default compiler unnamed and makes every new compiler a special case.
Encoding entry-provider rules in each compiler's `build` would reject bad
combinations, but it would not give experiments a way to choose each
compiler's natural provider. Exposing raw option paths as budgets is flexible
but couples every study to one compiler's internal layout.

## Consequences

Existing jobs without `kind` keep working, and resolved jobs gain an explicit
`kind`. Results compiled after this change carry compiler identity, so older
results summarise under `search_smarter` by inference. Experiments that set a
global `entry` keep their behaviour, with a plan-time notice. Adding compilers
is intended to become a registration plus a conformance run. Current entry
providers and conformance fixtures still contain built-in-specific code;
summary grouping and timing still need to distinguish entry choices and cost.

## Implementation order

1. `duration_mismatch`, `metrics_mismatch`, and `search_exhausted`. Accept: the
   zero-duration tamper case fails; an IDS dead end records `search_exhausted`.
2. Compiler registry with `search_smarter` and `nonzoned`; `select_strategy`
   delegates to it; remove the `nonzoned` branches from `job.py`. Accept:
   existing jobs produce identical results; `{"kind": "search_smarter", ...}`
   is accepted; `{"kind": "unknown"}` reports `strategy.kind`.
3. Per-compiler entry providers and defaults. Accept: `search_smarter` with
   `enola_initial` is unsupported with a reason; `nonzoned` without `entry`
   uses `enola_initial`.
4. Compiler identity in results, run records, summaries, `describe()`, and the
   schema.
5. Experiment per-strategy entries, neutral budgets, and `strategy_overrides`.
   Accept: one experiment containing `ids_strict` (`initial_storage`) and
   `nonzoned` (`enola_initial`) runs with both compilers `ok`; Search Smarter
   results equal those of a Search Smarter-only experiment.
6. `shared/v1` family, which reproduces `examples/devices/nonzoned.json`
   exactly.
7. Conformance suite over both compilers.
8. `ProgramCompiler` protocol, with one static-circuit adapter test using a
   saved external schedule.

## Implementation notes

The registry owns both built-ins, versions, schemas, entry providers, feedback
support, and budget applicability. Admission returns a list of the existing
structured unsupported reasons so stage paths and multiple failures survive;
stage predicates continue using Support. Search Smarter admission normalizes
its owned options once, and construction consumes these admitted values.
Factories return both the compiler and resolved options. The job and selection
layers contain no compiler-name branches. Catalogue construction is optional
when the Device lacks SLM-SLM CZ; the chosen compiler decides admission.

Compiler identity is stored in result provenance and every planned run, including
rejections/timeouts. Summary JSON and CSV expose identity. Legacy rows retain
their previous group IDs and infer search_smarter/version=legacy. Capability
predicates determine architecture support; a family name alone is not a hardware
capability. shared/v1 preserves the existing unspecified zone roles for exact
Device reproduction; its operation permissions define the shared region.

Experiment overrides apply before neutral budgets. A search_nodes limit is per
placement call: IDS counts visited nodes; A* keeps the source open-set bound.
An IDS limit returns an existing goal if one has already been found. Exhaustion
without a usable goal is search_exhausted/budget_hit=true. Search-node telemetry
remains unavailable (null); limits are not equivalent work measures.

ProgramCompiler adapters return the typed CompilationResult; common serialization
produces nadit.result/2. The test adapter reads a saved reference-generated
schedule and demonstrates third-party registration and rejection of tampering.
It is an interface test, not an implemented wrapper for an external project.

## Verification

- Baseline: 1,690 passed. Full regression after implementation: 1,719 passed
  in 95.08 seconds; 29 new cases, no skipped tests or changed action assertions.
  The existing discovery assertion now includes the approved shared/v1 family.
- Ruff format/lint and mypy pass for src/tests/examples. Ruff's local cache
  produced a package-cache panic; the same checks pass with --no-cache.
- CLI experiment `examples/experiments/compiler-comparison.json`: 8 planned,
  8 completed, 4 ok and 4 unsupported. Both measurement branches pass for each
  compatible compiler/device pair. Incompatible cross-product cells remain in
  runs.jsonl. Summary JSON/CSV include compiler identity. Local evidence is in
  `modules/physical_compiler/artifacts/unified-interface-0032/`; it uses an
  explicitly allowed dirty development snapshot with a frozen source hash.
- Tests verify exact expanded shared Device equality, compiler-specific entry
  defaults, aggregated admission before build, deterministic feedback outputs,
  independent replay, neutral budgets and overrides, IDS/A* exhaustion,
  duration/metrics tampering, and mandatory whole-program output validation.
- The mixed-study test also runs Search Smarter alone: its run ID and metrics
  are unchanged when nonzoned is added with its independent entry provider.
