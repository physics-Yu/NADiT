# ADR 0034: Modular physical noise and loss estimates

Status: Accepted

## Context

The existing hsim adapter reduces every upstream gate result to average fidelity.
It discards Pauli channels and leakage diagnostics and has no transport-loss model.
The compiler already provides validated, timed atom actions and shared AtomActivity.

## Decision

Add `noise/v1` as an independent cost model. It consumes validated results through
the existing cost CLI and experiment interface. Changing noise parameters never
recompiles or resamples measurements. Existing additive and hsim reports retain
their semantics.

Separate modules own gate channels, exposure/dephasing, transport loss, and report
assembly. Export a `nadit.noise/1` document inside the cost report, with event IDs,
source IDs, atom/qubit targets, timing, and Stim-style channel names/probabilities.
This is a channel configuration, not a complete Stim circuit or detector model.

Gate backends include configured Pauli probabilities and pinned hsim ideal,
overrotation, magnetic detuning, calibrated/profiled Raman, and coherent/open
four-level CZ models. Per-axis single-gate overrides allow explicit virtual-Z
models alongside physical Raman pulses. Upstream nonzero pulse durations must
match compiled durations. Cache distinct gate requests within an evaluation.
Upstream target-relative channels act before the ideal gate. Pauli twirling is an
approximation; coherent accumulation is not propagated.

Transport uses a `TransportNoiseModel` protocol. The implemented `rates/v1` model
has configurable load/store/move loss probabilities, distance and time hazards,
holder lifetime hazards, and a per-move Pauli channel. MOVE distance comes from
IR endpoints, independent of viewer animation. Detailed AOD/XY8 dynamics may later
implement the protocol; they are not implemented or silently substituted now.

For a moving atom, survival is `(1-p_move)*exp(-alpha*d-beta*t)`; each transfer
adds its own survival factor. Holder lifetime hazard covers busy and idle time,
with transfer intervals assigned to their source holder. It is independent of
the optional additional move hazard. Per-atom survival multiplies across events;
expected lost atoms is `sum(1-survival_i)`. Reset does not replace a lost atom.
Explicit calibrated CZ atom-loss probability is per participating atom per pulse.

Idle T2 produces `Z_ERROR((1-exp(-t/T2))/2)`. By default exposure also includes
MOVE/LOAD/STORE latency, but not gate/readout/reset duration. Gate models own their
own decoherence. Readout flips preserve measurement identity; reset errors are X
after Z reset. CZ spectator channels apply only to illuminated nonparticipants.

Upstream CZ computational leakage/unresolved loss stays a joint pair diagnostic;
it is not split into independent atom loss and is not exported as HERALDED_ERASE.
Configured physical loss and upstream computational survival remain separate.

Preserve the complete upstream `OperationResult` alongside the compact channels:
error-model parameters, diagnostics, physical observables, validity domain,
uncertainty, provenance, coherent unitary and quantum instrument. Encode matrices
as shape/dtype plus real/imaginary arrays. Preserve native unitary, Kraus or
superoperator representation and back-action data. No simulator is executed on
these saved records during compilation.

Store one `noise.upstream_records` entry per distinct request (backend config,
target and duration). Event/channel records refer to it with `upstream_record_id`.
This avoids repeating matrices for every atom pair. Reports use version `v1.1`
so `cost --into` retains the new record even if an older v1 report exists.
The public selector remains `noise/v1`; its physical estimator is unchanged.

This records everything the upstream operation returns, not all solver-internal
state. In particular, the CZ action is a projected computational-space channel;
the upstream does not return its full 16-level evolution, leakage-resolved
instrument, or individual spontaneous-emission trajectories. Those require an
upstream extension before stateful leakage propagation can be implemented.

Report two estimates: a no-fault probability from channel identity weights,
computational survival and physical survival; and a physical fidelity product
proxy using average gate fidelity and exponential T2 coherence factors. Average
upstream CZ fidelity already includes computational survival, so the latter is
not multiplied into that proxy again. Neither estimate is logical fidelity or a
state simulation. Report contributions and assumptions explicitly.

## Reference and alternatives

[FT-Weave B.2.1](https://arxiv.org/html/2609.20573v1#A2.SS2.SSS1)
uses an unencoded multiplicative gate/move/loss/idle estimate. We use actual
compiled event counts and configurable rates, without its logical-error model.
Its numerical parameters are not hardware defaults here.

Do not expand the old additive schema or run a microscopic transport simulation
inside compilation. Do not infer physical atom loss from gate infidelity.

## Verification

Baseline: 1719 tests passed. Final full suite: 1743 passed (1733 core and 10
optional hsim tests). New tests cover analytic exposure/loss/channel formulas,
immutable validated-result and CLI exports, both feedback branches, and pinned
upstream comparisons including CZ leakage, magnetic detuning and Raman channels.
Ruff formatting/lint and mypy passed across source, tests and examples.

Native-record follow-up: baseline 1743 passed; final 1750 passed. Seven new
optional-upstream cases cover JSON restoration of coherent/open CZ Kraus maps,
all four nonideal single-gate backends, record deduplication/references, and
coexistence with an older v1 report. Ruff and mypy also passed.

## Viewer integration

The offline viewer derives schedule lanes from existing action resource IDs,
kinds and individual durations, following the resource-lane/follow-window pattern
in the neutral-atom platform simulation viewer. Clicking a bar seeks playback.
It does not reschedule actions or infer exclusive resource reservations.

Attach the latest saved noise report after matching its execution hash. Show
cumulative expected physical loss, mean and maximum per-atom loss, probability colors,
and final fidelity proxy. Book each loss factor at its recorded interval end;
use prefix log-survival to support exact backward seeking and certain loss.
Draw a staircase through completion boundaries. This is an estimate timeline,
not loss sampling or a continuous trajectory model. Leave atoms in the replay.
Show current-step channels, leakage diagnostics and native records separately.

Verification: 1750 baseline tests; 1752 final Python tests, 22 JavaScript tests,
ruff and mypy passed. Browser visual inspection remains pending: the browser
tool rejected local file URLs. A standalone two-layout d=5 demonstration is
available in `modules/physical_compiler/artifacts/schedule-loss/index.html`.

### Loss source comparison

Replace the all-survive UI metric with mean per-atom loss and maximum per-atom
loss. Mark the maximum atom in the layout and allow selecting it. Average over
all input atoms. Resolve positive-risk ties by lowest atom ID and report the tie
count; show no maximum atom before risk accumulates.

Use `loss.js` for independent loss indexing/attribution and `loss-panel.js` for
rendering. For each atom, group factors by recorded completion time. Given
prior survival S and total group hazard H, allocate the increment
`S * (1 - exp(-H))` among sources proportionally to their hazards. For multiple
certain-loss causes at the same boundary, divide the increment equally among
those causes. This avoids depending on input record order. Prefix snapshots
retain exact backward queries without resampling.

Compare three additive columns: source expected losses, source mean per-atom
probability, and source probability for the currently highest-risk atom. Load,
store, movement and residence are always shown; physical CZ and other sources
are retained when present. The mean and maximum-atom bars share one probability
scale. All columns sum to their corresponding total. Attribution follows the
existing completion-booking convention, not an inferred physical loss trajectory.

Source-comparison verification: 1752 Python tests remain passing; JavaScript
tests increased from 22 to 26. Ruff and mypy passed. Both retained d=5 traces
were checked at start, midpoint, end and reverse seek: source totals match
per-atom probabilities, their mean and expected lost count. Current standalone
HTML was regenerated from the saved traces; compilation and noise parameters
were unchanged. Browser visual inspection remains pending under the previously
reported local-file access restriction.
