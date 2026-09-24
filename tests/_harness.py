"""Shared helpers for headless test scripts.

Each test is a Blender script run by tools/run_tests.py:

    blender -b --factory-startup --python-exit-code 1 --python tests/test_x.py

It prints one "[PASS] label" or "[FAIL] label -- detail" line per check and
exits non-zero if any check failed.
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "swish_physics"
failures = []


def check(label, ok, detail=None):
    ok = bool(ok)
    if not ok:
        failures.append(label)
    shown = "" if detail is None or (isinstance(detail, str) and not detail) else f" -- {detail}"
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{shown}")


def fresh_import():
    """Import the add-on from the repo, dropping any copy already loaded."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    for name in [m for m in sys.modules if m.split(".")[0] == PACKAGE]:
        del sys.modules[name]
    import swish_physics
    return swish_physics


def finish():
    print()
    print(f"FAILURES ({len(failures)}): " + "; ".join(failures) if failures else "ALL CHECKS PASSED")
    sys.stdout.flush()
    sys.exit(1 if failures else 0)
