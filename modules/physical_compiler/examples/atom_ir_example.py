"""Print a manually constructed atom program using the small device fixture."""

from math import hypot, pi
from pathlib import Path

from nadit_physical_compiler import (
    AODGrid,
    AODLine,
    AtomProgram,
    AtomStep,
    CZPulse,
    Load,
    Measure,
    Move,
    Position,
    Region,
    ResultRef,
    Rotation,
    Store,
    Wait,
    load_device,
)

device = load_device(
    Path(__file__).resolve().parents[1] / "tests/fixtures/devices/zoned_small.json"
)
storage_grid = AODGrid("transport", (AODLine(0, 0),), (AODLine(0, 0), AODLine(1, 5)))
gate_grid = AODGrid("transport", (AODLine(0, 120),), (AODLine(0, 0), AODLine(1, 2)))
readout_grid = AODGrid("transport", (AODLine(0, 240),), (AODLine(0, 0), AODLine(1, 5)))
bindings = ((0, 0, 0), (1, 0, 1))
move_duration = device.timing.move_duration(hypot(3, 120))
program = AtomProgram(
    (
        AtomStep(
            (
                Load(
                    storage_grid,
                    bindings,
                    duration_us=device.timing.load_us,
                    source_ids=(0,),
                ),
            )
        ),
        AtomStep(
            (
                Move(
                    gate_grid,
                    ((0, Position(0, 120)), (1, Position(2, 120))),
                    duration_us=move_duration,
                    source_ids=(0,),
                ),
            )
        ),
        AtomStep(
            (
                Store(
                    AODGrid("transport"),
                    ((0, "gate_0_left"), (1, "gate_0_right")),
                    duration_us=device.timing.store_us,
                    source_ids=(0,),
                ),
            )
        ),
        AtomStep(
            (
                CZPulse(
                    "rydberg",
                    Region(-1, 119, 3, 121),
                    duration_us=0.5,
                    source_ids=(0,),
                ),
            )
        ),
        AtomStep(
            (Rotation("raman", (0, 1), "Y", pi / 2, duration_us=1, source_ids=(1, 2)),)
        ),
        AtomStep((Load(gate_grid, bindings, duration_us=device.timing.load_us),)),
        AtomStep(
            (
                Move(
                    readout_grid,
                    ((0, Position(0, 240)), (1, Position(5, 240))),
                    duration_us=move_duration,
                ),
            )
        ),
        AtomStep(
            (
                Measure(
                    "camera",
                    ((0, ResultRef(3)), (1, ResultRef(4))),
                    retain_atoms=True,
                    duration_us=10,
                    source_ids=(3, 4),
                ),
            )
        ),
        AtomStep((Wait(duration_us=3),)),
    )
)

if __name__ == "__main__":
    print(program.to_text(), end="")
