# Third-Party Notices

## MQT QMAP

The candidate-generation, scoring, and IDS algorithms in
`modules/physical_compiler/src/nadit_physical_compiler/placement/` are adapted
from MQT QMAP commit `cd8ec9131f99d93ce1601c9a1dcad5c9e09ce31d`:

- `src/na/zoned/layout_synthesizer/placer/HeuristicPlacer.cpp`
- `include/na/zoned/layout_synthesizer/placer/HeuristicPlacer.hpp`
- `src/na/zoned/Architecture.cpp`
- `include/na/zoned/Architecture.hpp`

The staged strict and relaxed routing and atom-event construction in
`modules/physical_compiler/src/nadit_physical_compiler/transport/` also adapt
the same revision's:

- `src/na/zoned/layout_synthesizer/router/IndependentSetRouter.cpp`
- `include/na/zoned/layout_synthesizer/router/IndependentSetRouter.hpp`
- `src/na/zoned/code_generator/CodeGenerator.cpp`
- `include/na/zoned/code_generator/CodeGenerator.hpp`

The standalone comparison in `modules/physical_compiler/reference/` extracts
and executes the original layout, router, and rearrangement methods with
data-only C++ adapters and observation wrappers.

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
