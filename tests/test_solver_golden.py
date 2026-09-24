"""Both steps reproduce Kawaii Physics' golden positions bit for bit (KawaiiPhysicsGoldenTest.cpp)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish, REPO

sys.path.insert(0, REPO)
import kawaii_golden as golden
from swish_physics.solver import native, step_numpy

c_step = native.backend()
on_windows = sys.platform == "win32"
check("the C step is built and loads" if on_windows else "off Windows, numpy is the step",
      (c_step is not step_numpy) == on_windows, native.reason())

for backend in [step_numpy] + ([c_step] if c_step is not step_numpy else []):
    for scenario in ("Chain", "ChainLegacy", "Constraint", "Collision"):
        positions, expected = golden.run(scenario, backend)
        exact, worst = golden.compare(positions, expected)
        check(f"{backend.NAME}: {scenario} matches Kawaii bit for bit", exact == len(expected),
              f"{exact}/{len(expected)} exact, worst {worst:.3e} cm")

finish()
