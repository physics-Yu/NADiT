# Third-Party Notices

## ENOLA

The non-zoned fixed-home planner follows the dual-candidate, longest-first
independent-set batching in ENOLA commit
`2944dbf4e163e8d2eeeec607add0d9139edce689`, `enola/router/router_mis.py`.
It uses NADiT Device rules and strict transfer admission, not upstream CodeGen.
The exact upstream router source is retained as a test-only text fixture in
`modules/physical_compiler/tests/fixtures/enola/router_mis.py.txt`, SHA-256
`a75e92353a74b6f3597a33b214d358b0782de2fa60400362c5e1967646cecb82`.
Tests execute only the original compatibility and sorted-greedy functions.
Copyright (c) 2024, UCLA VAST Lab. The complete BSD-3-Clause license is retained
alongside that fixture as `LICENSE`.

Optional initial placement loads the unmodified `SAPlacer` class from the same
revision's `enola/placer/placer.py`, SHA-256
`d256c84490bd72d525515f24acb081d7b21bcd5f7bbc31302a3bbf79a16997cf`.
The original text and complete license are packaged under
`nadit_physical_compiler/placement/enola_source/`. The adapter supplies private
RNG functions and a deterministic iteration budget, and handles zero-uphill
warm-up without division by zero. It does not call the dynamic partial placer.

## MQT QMAP

The candidate-generation, scoring, IDS, and A* algorithms in
`modules/physical_compiler/src/nadit_physical_compiler/placement/` are adapted
from MQT QMAP commit `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`:

- `src/na/zoned/layout_synthesizer/placer/HeuristicPlacer.cpp`
- `include/na/zoned/layout_synthesizer/placer/HeuristicPlacer.hpp`
- `src/na/zoned/Architecture.cpp`
- `include/na/zoned/Architecture.hpp`

The source static scheduler and original reuse policy in the corresponding
`scheduling/` and `reuse/` packages adapt:

- `src/na/zoned/scheduler/ASAPScheduler.cpp`
- `include/na/zoned/scheduler/ASAPScheduler.hpp`
- `src/na/zoned/reuse_analyzer/VertexMatchingReuseAnalyzer.cpp`
- `include/na/zoned/reuse_analyzer/VertexMatchingReuseAnalyzer.hpp`

The staged strict and relaxed routing and atom-event construction in
`modules/physical_compiler/src/nadit_physical_compiler/transport/` also adapt
the same revision's:

- `src/na/zoned/layout_synthesizer/router/IndependentSetRouter.cpp`
- `include/na/zoned/layout_synthesizer/router/IndependentSetRouter.hpp`
- `src/na/zoned/code_generator/CodeGenerator.cpp`
- `include/na/zoned/code_generator/CodeGenerator.hpp`

The standalone comparison in `modules/physical_compiler/reference/` extracts
and executes the original scheduling, reuse, layout, router, and complete
code-generation methods with data-only C++ adapters and observation wrappers.
The paper timing profile and benchmark settings follow
`eval/na/zoned/eval_ids_relaxed_routing.py` and
`eval/na/zoned/square_architecture.json`.

Source: <https://github.com/munich-quantum-toolkit/qmap>

```text
MIT License

Copyright (c) 2023 - 2025 Chair for Design Automation, TUM
Copyright (c) 2025 Munich Quantum Software Company GmbH

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
