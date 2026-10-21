# 0023: Semantic validation and self-contained results

Status: Implemented on 2026-10-21.

## Context

ADR 0021 added `validate_result`, which checks Load, Move, and Store against
endpoint motion rules, replays the program, and checks source coverage. It
does not check what a CZ pulse illuminates, whether local operations address
legal zones and holders, or whether planned times respect dependencies and
feedback latency. Two manual edits of the multipair example's saved result
both pass `nadit validate`: moving every CZPulse coverage to an empty region,
and deleting the feedback WAIT so a conditional operation starts before its
result is available. architecture.md lists pulse addressing and dependency
timing among the test/debug checks; these are not yet implemented.

A saved result also depends on files outside it. `job.device` and
`job.circuit.qasm` are absolute paths. `nadit validate` reloads the Device from
that path, so an edited Device file changes what is checked. `job_hash` hashes
those path strings: the same job compiled from another directory gets a
different hash, while edited QASM or Device content at the same path keeps the
old hash. The result does not contain the native DAG, so validation cannot
know which qubits a CZ or measurement source operation addresses.

Experiments that compare architectures and strategies (ADR 0027) will search
for configurations with lower cost. Without semantic checks, that search can
favour programs that are shorter only because they are wrong.

## Decision

### Result document version 2

Add `nadit.result/2`. It contains every version-1 field plus:

| Field | Content |
| --- | --- |
| `device` | Expanded Device snapshot from `encode_device`, accepted by `loads_device` |
| `dag` | Native PhysicalDAG: nodes with `id`, `name`, `qubits`, `clbits`, `params`, `condition`, `source_id`, `result`; `edges`; `num_qubits`; `num_clbits`; `global_phase` |
| `inputs` | `{"qasm_sha256", "device_sha256"}` over canonical content |

The QASM hash covers the exact bytes read when the job is loaded. The Device
hash covers its expanded canonical JSON snapshot, so formatting and equivalent
compact-grid declarations do not change its identity. Both hashes are captured
at job loading, before later edits to the input files can affect the result.

`provenance.job_hash` is computed over the job with both input paths replaced
by their content hashes, so it is independent of file location and changes
when content changes. Provenance adds `git_dirty` (true when tracked files
differ from `HEAD`, null without git). Canonical JSON uses sorted keys, compact
separators, and `allow_nan=False`.

`decode_result` reads versions 1 and 2. Version-1 documents receive the
existing structural checks only; `nadit validate` reports
`"semantic": "unavailable"` for them. `nadit validate` uses the embedded
Device for version 2; version 1 retains its external Device lookup.
`--device PATH` replaces it explicitly, and the report records its absolute
path as `device_override`. CLI compilation writes version 2. The existing
Python `encode_result` call without DAG, Device, and input hashes still writes
version 1; supplying all three selects version 2.

### Semantic checks

`validate_result` takes the decoded DAG and Device. Checks run in step order
against the step-entry layout, as the motion checks do.

**CZ pulse.** For each CZPulse, the illuminated set is every atom whose
step-entry position lies in the coverage. Build the interaction graph over
that set using `cz.radius_um`. The edges must equal exactly the gates named by
the pulse's `source_ids`, mapped through `qubit_to_atom`. Further checks:

- each pair's holder types must be in `cz.holder_pairs`;
- each pair must lie in a zone whose operations include `cz`;
- illuminated atoms outside those pairs fail as `illuminated_spectator`;
- an atom moved by another action in the same step must stay outside the
  coverage at both endpoints and along the straight segment between them.

Same-channel CZ pulses in one step also check their combined illuminated
set: splitting a pulse into separate coverage objects cannot hide a cross-pair
interaction. Point-addressed coverage uses the addressed atoms' initial
positions without inventing a beam width.

Rules: `missing_interaction`, `unintended_interaction`,
`illuminated_spectator`, `holder_pair`, `zone_capability`, `moving_in_pulse`.

**Local, global, reset, and readout actions.** Addressed atoms must be in a
zone permitting the operation, with a holder allowed by the capability. The
addressed atoms must equal the qubits of the source operations. For GlobalRY,
the atoms inside the coverage must equal the qubits of its sources. A Measure
must pair each atom with the ResultRef of the measurement that acts on that
atom's qubit. Concurrently measured atoms must be at least
`readout.min_spacing_um` apart. Rules: `zone_capability`, `holder`,
`addressing`, `result_binding`, `readout_spacing`.

**Timing and branches.** From `program.timeline()`, each emitted operation's
interval runs from the earliest start to the latest end of the actions that
reference it. Checks:

- every DAG edge between emitted operations must satisfy
  `end(predecessor) <= start(successor)`;
- an operation conditioned on a measurement must start no earlier than that
  measurement's end plus `readout.latency_us`, and no earlier than the
  recorded `available_at_us`;
- recorded `operations[*].start_us/end_us` must match the derived intervals;
- an operation is emitted exactly when its condition holds for the recorded
  measurement value.

Rules: `dependency_order`, `feedback_latency`, `record_mismatch`,
`condition_mismatch`.

Dependency and feedback bounds pass through skipped operations and metadata
nodes such as barriers. Each emitted native gate needs exactly one executable
pulse, rotation, reset, or measurement action. Transport or WAIT provenance
alone does not establish gate execution. Gate kind, parameters, and channel
must agree with the referenced native operation and Device.

### Report shape

The report keeps `ok` and `failure`, where `failure` is the first failure in
check order, and adds:

- `failures`: up to 20 failures, in the same form;
- `failure_count`: the total;
- `checks`: which groups ran (`motion`, `coverage`, `replay`, `pulse`,
  `addressing`, `timing`).
- `semantic`: `checked` with a DAG, or `unavailable` for legacy results.

All checks run even after a failure, so an agent can see every violated
class in one run. Tolerances use `COORDINATE_TOLERANCE` for space and a new
`TIME_TOLERANCE_US = 1e-9` for time.

If an invalid layout or an unreplayable action prevents further spatial
checks, the failure is recorded and independent coverage and timing checks
continue. The report does not claim groups that could not run. Direct legacy
Python calls without a DAG retain their compact report; `detailed=True`
requests the extended structural report.

## Alternatives

Pulse checks inside the compiler would duplicate construction logic and
could share its mistakes; the validator is an independent reference.
Keeping paths without snapshots makes every result depend on the file system
at check time. Recompiling during validation would test the compiler against
itself.

## Consequences

Saved results become larger, by the DAG and the Device, and are portable.
Results compiled before this ADR remain readable but are reported as not
semantically validated. Some existing examples may fail the new checks; such
failures are compiler findings to fix in the responsible module, not test
changes. The viewer (ADR 0022) can use the embedded Device and drop its
`--device` fallback for version-2 results.

## Implementation order

1. Encode the DAG, the Device snapshot, input hashes, the location-independent
   job hash, and `git_dirty`; add `nadit.result/2` decoding and version-1
   compatibility. Accept: round-trip equality; the same job compiled from
   two directories gives equal hashes; changing one byte of QASM or a Device
   field changes the hash.
2. Pulse checks. Accept: every compiled example passes. The two manual edits
   above fail with `missing_interaction` and `feedback_latency`. Hand-built
   cases cover a spectator in coverage, a cross-pair distance within the
   radius, a disallowed holder pair, and an atom moving through a pulse.
3. Addressing and readout checks. Accept: a wrong-zone rotation, a swapped
   result binding, and readout below minimum spacing each report their rule.
4. Timing and branch checks. Accept: both feedback branches pass; a
   conditional operation shifted earlier, a reordered dependency, and an
   emitted operation whose condition is false each fail.
5. Report `failures`, `failure_count`, and `checks`; add `--device` to the CLI;
   update the README, jobs-and-results.md, architecture.md, and the CHANGELOG.

## Compiler finding: storage-only GlobalRY after reuse

The pinned `global_ry_and_barrier` source fixture retains atom 0 at `(12, 80)`
while the second GlobalRY covers `[0, 0, 64, 24]`. Its native operation names
all four qubits; only atoms 1, 2, and 3 receive the pulse. The original C++
reuse and placement interfaces see CZ layers, while code generation defines
the global pulse region from storage zones. The recorded oracle and its
existing source-defect evidence remain unchanged.

Global-rotation placement now moves required atoms into legal sites inside
the configured coverage and moves illuminated spectators outside it. It
preserves already legal atoms, follows deterministic free-site assignments,
and leaves the pulse extent unchanged. Existing transport implementations
lower those assignments.

This correction deliberately changes the next CZ placement. The first
source comparison difference is `placements[3][0][2]`, original column 1
versus corrected column 0; the action count changes from 28 to 33. The
corrected program passes semantic checks. Exact source equality is not a
correctness requirement for this documented source defect.
