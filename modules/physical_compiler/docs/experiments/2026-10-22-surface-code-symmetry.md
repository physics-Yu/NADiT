# Surface-code experiment: structure and the placement search space

Recorded: 2026-10-22. Runs completed: 2026-10-21.
Status: Experiment recorded. Structure-preserving embedding is the agreed next
research direction; the non-zoned implementation has not started.

## Input and completed runs

The input contains two independent distance-5 rotated surface-code patches,
five syndrome-extraction rounds per patch, 98 atoms, 800 native CZ operations,
and 240 measurement identities. The Stim 1.16.0 rotated-memory-Z schedule was
densely reindexed with its tick boundaries preserved. Final data readout was
omitted. Outcomes came from FakeBackend; no decoder or logical-error study was
run. QASM SHA-256:
`216b9970fd2886341e2f51945fb3afcd16ab473ff70fa57d33aa1598c43d85c3`.

All cases used flexible AOD motion with unlimited line counts, 4,096 storage
sites, IDS with 16 trials and queue capacity 2,000, strict transport, matching
reuse, and a 1,800-second worker budget on `yyq@10.133.24.178`.

| Configuration | Total modeled time (ms) | Readout-zone MOVE time (ms) | Exposures |
| --- | ---: | ---: | ---: |
| 8x32 gate pairs, 101 um rows, readout starts at y=1280 | 564.751 | 466.000 | 240 |
| 8x40 gate pairs, 6 um rows, readout starts at y=602 | 244.548 | 144.080 | 240 |
| Same compact geometry, batch readout | 109.855 | 16.932 | 28 |

The geometry comparison changes several dimensions together. The final two
rows are a controlled readout comparison on commit `a878fed`, with the same
geometry, timing, strategy, DAG, and 240 outcomes fixed by measurement identity.
Only readout capacity changes from 1 to null. Compile times were 210.938 and
213.293 seconds. Both results pass semantic validation and final replay.
Each readout batch returns to its exact entry layout.

Artifacts, relative to this module:

- `artifacts/surface-d5x2-r5/`: original large-span geometry and input provenance.
- `artifacts/surface-d5x2-r5-compact6/`: compact geometry and its comparison.
- `artifacts/surface-d5x2-r5-batch-readout/`: controlled experiment spec,
  per-run results, `comparison.json`, and the two-trace `index.html`.

Implementation and verification details are in
[ADR 0029](../../../../docs/decisions/0029-batched-aod-readout.md).

## What the saved layouts reveal

Read-only reconstruction reproduced the 28 batches exactly. All rejected
additions in that greedy construction failed source pickup closure; none
passed closure and then failed readout placement. Data atoms are interspersed
with ancillas at the storage positions reached after the CZ layers.

| Round | Source rows occupied by ancillas | Extra data atoms picked up by loading all 48 | Actual batches | Feasible source-row grouping |
| --- | ---: | ---: | ---: | ---: |
| 1 | 4 | 22 | 4 | 4 |
| 2 | 5 | 25 | 5 | 5 |
| 3 | 5 | 27 | 6 | 5 |
| 4 | 6 | 33 | 7 | 6 |
| 5 | 5 | 35 | 6 | 5 |

For example, first-round ancillas at (15,310) and (25,300) cannot share the
current direct pickup: their active rows and columns also cover a data atom
at (15,300). Already measured ancillas returned to storage also block some
later groups because the planner does not reload them.

Grouping by source row passed the existing pickup and placement constructors
in a read-only comparison: 25 batches and 14.860 ms of readout MOVE time,
versus 28 batches and 16.932 ms in the saved run. This is a feasible grouping
comparison, not a new complete compilation or an optimum. The readout planner's
5 um axis spacing admits five rows in the 20 um high region; actual batches
used at most four rows, so that limit did not cause the observed splits.

## Research interpretation

The circuit has repeated local stabilizer patterns, two copies of a patch,
distinct data/ancilla roles, and repeated rounds. A useful embedding can encode
these relationships directly. Independent atom assignments at every layer
discard this description and leave the search to rediscover coordinated
placements through local costs.

The working hypothesis is that good solutions occupy a small structured subset
of the nominal assignment space. A narrow high-quality region may be difficult
for a generic heuristic to enter and preserve, even with more search effort.
The next question is therefore how to represent that subset: stable role
arrangements, relative patch placement, and reusable round layouts should be
embedding choices rather than accidental outcomes of unrelated assignments.

The experiment establishes a mismatch between locally chosen storage layouts
and later pickup costs, and provides a better structured grouping. It does not
measure the size of the good-solution subset. A controlled comparison of
structured embeddings and free assignment search should test that hypothesis.
Structure preservation must retain X/Z roles, CNOT direction/order, boundary
stabilizers, and measurement identities; repeated patterns do not make every
atom or operation interchangeable.

## Next direction

Study and embed an ENOLA-style non-zoned mode in the existing physical compiler.
First inspect the reference implementation's actual input, movement, and
interaction assumptions. The intended mode allows storage and entangling
operations to share a region instead of requiring every gate to visit a fixed
entanglement-zone catalogue. Any readout isolation remains an explicit Device
capability. AOD/SLM ownership, interaction distance, unwanted interactions,
transport rules, and timing still apply.

Keep the existing Physical DAG, Device capability boundary, Atom IR, measurement
identity/latency handling, replay validation, and viewer as the common contract.
Use this experiment as the zoned baseline. The next comparison should separate
the effect of removing mandatory zone transfers from the effect of preserving
circuit structure in the embedding. Implementation choices remain to be
discussed; this record does not introduce a new algorithm or run.
