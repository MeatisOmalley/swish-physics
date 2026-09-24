"""Run the headless test scripts in Blender and summarise them.

    python tools/run_tests.py            every tests/test_*.py
    python tools/run_tests.py register   only tests whose name contains "register"

Blender is found through the WAIFU_PHYSICS_BLENDER environment variable, or at the
default Steam install path.
"""
import glob
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLENDER = os.environ.get("WAIFU_PHYSICS_BLENDER",
                         r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")


def run(path):
    started = time.perf_counter()
    proc = subprocess.run([BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "--python", path],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    lines = (proc.stdout + proc.stderr).splitlines()
    passed = [line for line in lines if line.startswith("[PASS]")]
    failed = [line for line in lines if line.startswith("[FAIL]")]
    ok = proc.returncode == 0 and not failed and passed
    return ok, passed, failed, lines, proc.returncode, time.perf_counter() - started


def main(filters):
    tests = sorted(glob.glob(os.path.join(REPO, "tests", "test_*.py")))
    if filters:
        tests = [t for t in tests if any(f in os.path.basename(t) for f in filters)]
    if not tests:
        print("no tests matched")
        return 1
    bad = 0
    for path in tests:
        ok, passed, failed, lines, code, seconds = run(path)
        name = os.path.basename(path)
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {len(passed)} passed, {len(failed)} failed "
              f"({seconds:.1f} s)")
        if not ok:
            bad += 1
            for line in failed:
                print("       " + line)
            if code != 0 and not failed:
                # A crash or an uncaught error: show where it happened.
                tail = [line for line in lines if line.strip()][-15:]
                print("       exit code", code)
                for line in tail:
                    print("       | " + line)
    print(f"\n{len(tests) - bad} of {len(tests)} test files passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
