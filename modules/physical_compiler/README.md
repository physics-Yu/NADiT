# Physical Compiler Workspace

Status: Workspace layout accepted on 2026-10-20; implementation has not started.

This directory holds module-specific design notes, source, tests, and example
configurations. The top-level layout is:

```text
physical_compiler/
├── README.md
├── src/          # Implementation code
├── tests/        # Module tests
├── docs/         # Module design notes
└── configs/      # Example device configurations
```

Empty directories contain `.gitkeep` files so Git preserves the layout.
It belongs to the shared NADiT repository, with no separate Git repository.

Read the root [engineering standards](../../agent.md) and
[architecture](../../architecture.md) before working here.

The current discussion covers configurable AOD motion constraints through a
constraint map, including fixed-grid translation and endpoint row/column
order preservation; configurable zoning; static SLM lattice sites and timed
SLM/AOD transfers; and simple move-from/to actions timed by a distance function
without velocity dynamics. These describe the user's direction, not selected
APIs or algorithms.

Project-wide decisions belong in the root `docs/decisions/`; module-specific
design notes belong in this module's `docs/`.

The user leads the module design step by step in the NADiT physical compiler
chat. Language, packaging, interfaces, and organization within these folders
remain open for that discussion.
