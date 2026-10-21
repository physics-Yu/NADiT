# 0026: Cost model adapter and hsim-aqc integration

Status: Implemented on 2026-10-21 for the reviewed adapter boundary below.

## Context

Result metrics (ADR 0021) count steps, transfers, batches, and durations.
Comparing architectures and strategies needs an estimate of execution
quality that combines these with gate errors, atom loss during transfers,
decoherence while idle, and motion-induced heating. The group's simulator
[hsim-aqc](https://github.com/zhang-wenjun/hsim-aqc) is the intended cost
model. Its input format and parameters have not been reviewed for this ADR.

Cost evaluation should not change compilation. The same saved result must be
re-evaluable under different noise parameters without recompiling, so a
parameter sweep over noise does not multiply compilation work.

## Decision

### Boundary

A cost model is a post-processing step on a semantically valid
`nadit.result/2` document (ADR 0023):

```python
class CostModel(Protocol):
    name: str      # e.g. "additive", "hsim-aqc"
    version: str   # model code version or pinned commit
    def evaluate(self, activity: AtomActivity, result: Mapping[str, Any],
                 params: Mapping[str, Any]) -> CostReport: ...
```

Models never import compiler internals. They read `result` (the Atom IR, DAG,
and Device snapshot) and a shared `AtomActivity` summary. A model may refuse
a result that has not been validated: `evaluate` raises `UnvalidatedResult`
unless `result["validation"]["ok"]` is true and the pulse and timing checks
ran.

### AtomActivity

`activity.py` derives a model-independent summary from the timeline:

| Field | Meaning |
| --- | --- |
| `per_atom.busy` | Intervals with action kind: CZ, rotation, transfer, move, measure, reset, wait |
| `per_atom.idle_us` | Total time not covered by its own actions, split by holder (SLM or AOD) and zone role |
| `per_atom.transfers` | LOAD and STORE count |
| `per_atom.move_distance_um` | Total path length, and count of moves |
| `per_atom.cz_count`, `rotation_count` | Gates experienced |
| `global.duration_us` | As in metrics |
| `global.pulse_illumination` | Per CZ pulse: covered area and atom count |

This is useful by itself: a basic decoherence estimate needs only idle time
and transfer counts. Every model consumes the same summary, so models stay
comparable.

### CostReport

```json
{"model": "hsim-aqc", "version": "<commit>", "params_hash": "...",
 "metrics": {"log_fidelity": -0.31, "fidelity": 0.733,
             "contributions": {"cz": -0.12, "single": -0.02, "transfer": -0.08,
                               "idle": -0.07, "heating": -0.02}},
 "warnings": []}
```

`log_fidelity` and `contributions` are required; models add their own keys.
Log-additive contributions let experiments attribute a trade-off to its
source, for example fewer transfers bought with longer idle time.

### Built-in additive model

`additive/v1` is a reference model with explicit parameters:

- per-gate infidelity for CZ, single-qubit gates, and readout;
- per-transfer loss probability;
- T2 for SLM-held and AOD-held atoms, giving idle dephasing as
  `exp(-t/T2)` per atom;
- a heating term per move, as a function of distance and duration.

It runs without hsim-aqc, serves as the test oracle for `AtomActivity`, and is
the default when no model is named.

### hsim-aqc adapter

The adapter lives in `cost/hsim.py`. It is an optional dependency group
`cost-hsim` pinned to a commit and imported lazily. It translates
`AtomActivity` and the result into hsim-aqc's input, runs it, and maps the
outputs into `CostReport`. The adapter owns all format knowledge; nothing
else imports hsim-aqc. Adding the dependency follows agent.md: it needs
approval and a justification in the change.

### Parameters and CLI

Cost parameters are JSON files under `cost_params/` and are hashed into
`params_hash`. Commands:

- `nadit cost RESULT.json --model additive --params cost_params/baseline.json`
  writes the report to stdout;
- `--into RESULT.json` instead appends it under `cost[]` in the result
  document. Several models and parameter sets can coexist; each entry carries
  model, version, and params hash.

## Open questions

To be settled by reading hsim-aqc before step 3:

1. Input granularity: does it consume per-atom intervals, a gate list with
   timings, or Atom IR-level actions with positions?
2. Is it analytic (error budget) or state-based (density matrix or
   trajectories)? If state-based, what are its qubit-count and runtime limits,
   and does it need the circuit semantics from `dag`?
3. Which physical parameters does it take (T1, T2, T2*, Rydberg decay, loss,
   heating, crosstalk), and in which units?
4. Language, Python version compatibility with 3.12, license, and whether
   distribution is restricted.
5. Does it model spectator illumination or crosstalk? If so, `AtomActivity`
   must carry pulse geometry, not only counts.

If hsim-aqc needs data that `AtomActivity` lacks, extend `AtomActivity` rather
than letting the adapter read Atom IR directly. This keeps one derivation of
"what each atom experienced".

## Alternatives

Computing cost inside the compiler would require recompiling for every noise
setting and would let cost estimation influence construction implicitly.
Calling hsim-aqc directly on Atom IR, without `AtomActivity`, would duplicate
timeline derivation for each model.

## Consequences

Every result can carry several cost reports, and experiments compare
strategies under a fixed, hashed noise model. The quality of the conclusions
depends on the model; the additive baseline makes disagreements between
models visible instead of hiding them.

## Implementation order

1. `AtomActivity`. Accept: hand-built programs with known idle, transfer, and
   move totals; per-atom busy plus idle time equals the duration.
2. `additive/v1`, `CostReport`, and `nadit cost`. Accept: analytic small
   cases; contributions sum to `log_fidelity`; the same result and parameters
   give the same report.
3. Review hsim-aqc and answer the open questions in this ADR.
4. The hsim-aqc adapter and pinned optional dependency. Accept: fixtures from
   hsim-aqc's own examples are reproduced through the adapter; in the
   additive limit stated by its maintainers, it agrees with `additive/v1`
   within a stated tolerance.

## Reviewed implementation boundary

The accessible hsim-aqc repository is Python package `uftqc`, pinned to
`8e71f80282e70fc03e771d3edee41a30dcb07dfa`. It requires Python 3.12 and exposes
`OperationRequest.execute() -> OperationResult`, with average gate fidelity,
quantum channel, duration in seconds, diagnostics, and validity domain.
Four-level CZ parameters use MHz; dissipation lifetimes use seconds. Its
`CircuitRunner` propagates a density matrix and is not used in this adapter.
No repository license file was found; the dependency is referenced, not vendored.

The first adapter aggregates independent operation fidelities from ideal or
coherent-overrotation Raman backends and ideal/coherent/open four-level CZ
backends. It uses the shared activity's actual single-qubit targets and
CZ pairs. Nonideal pulse duration must match the compiled pulse duration.
CZ interactions use the explicitly supplied common calibration; the adapter
does not infer a C6 coefficient from atom separation.
Transfer loss, idle T2, readout, and distance heating use explicitly named
additive background parameters. This is a gate-product estimate, not circuit
density-matrix fidelity; coherent accumulation, microscopic transport dynamics,
and spectators beyond the validated illumination set are not simulated.
The zero-noise/ideal limit is tested; upstream does not state a general
equivalence with the additive model.

Activity stores action intervals, endpoints, parameters, atom IDs, gate pairs,
and illumination geometry. Busy time is the union of physical-action intervals.
WAIT and pending feedback time count as idle; explicit wait events are also
retained. A short action's remaining parallel-step time uses its completed
holder and position. Idle categories are (holder, set of zone roles), avoiding
double counting in shared zones. All current atoms have retained readout.

The additive model uses `log(1-p)` per event, `-idle_us/T2_us`, and
`-(alpha_per_um*d + beta_us_per_um2*d*d/duration_us)` per atom MOVE. Zero
coefficients disable their terms; probabilities are in [0,1), T2 is positive
or null. Baseline parameters are illustrative, not calibrated hardware values.
Cost admission revalidates a saved v2 result once before deriving activity;
multiple models reuse that activity. Changing noise parameters never invokes
the compiler. A cost entry includes a result-content hash as well as the
parameter hash. `--into` updates only the evaluated result, preserving IR.

## Verification

The combined ADR 0026/0027 suite passes 1,610 tests against a 1,575-test
baseline; formatting, lint, and mypy pass. Activity cases have hand-calculated
parallel tails, holder changes, waiting, distance, and busy-plus-idle totals.
Post-processing rejects stale or missing semantic evidence and does not invoke
compilation. Optional integration runs against the pinned package: ideal CZ
and Raman examples, analytic overrotation, direct coherent/open four-level
results, and nonideal pulse-duration mismatch rejection. No existing test
assertions were changed.
