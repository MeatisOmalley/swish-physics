"""Build the extension zip: compile the C step (Windows), fetch it for Linux, validate, package.

    python tools/release.py

Writes dist/waifu_physics-<version>.zip. The C step is compiled with the
Visual C++ build tools into waifu_physics/bin/waifu_physics_step.dll when its source
exists; on other platforms, or without the build tools, the package ships
without it and the numpy step is used. The Linux build (waifu_physics_step.so) comes from
the linux-step GitHub workflow, fetched with the GitHub CLI when its run includes the current
step.c; without it, Linux uses the numpy step. WAIFU_PHYSICS_BLENDER and WAIFU_PHYSICS_VCVARS
override the default Blender and vcvars64.bat locations.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO, "waifu_physics")
BLENDER = os.environ.get("WAIFU_PHYSICS_BLENDER",
                         r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")
VCVARS = os.environ.get("WAIFU_PHYSICS_VCVARS", r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"
                                        r"\VC\Auxiliary\Build\vcvars64.bat")
SOURCE = os.path.join(PACKAGE, "solver", "step.c")
DLL = os.path.join(PACKAGE, "bin", "waifu_physics_step.dll")


def build_dll():
    if not os.path.exists(SOURCE):
        print("no C step yet (solver/step.c); packaging without a DLL")
        return True
    if sys.platform != "win32" or not os.path.exists(VCVARS):
        print("no Visual C++ build tools here; packaging without a DLL")
        return True
    os.makedirs(os.path.dirname(DLL), exist_ok=True)
    build_dir = os.path.join(REPO, "build")
    os.makedirs(build_dir, exist_ok=True)
    # /fp:precise and no /arch: SSE2 arithmetic without FMA contraction, so floats round
    # as Kawaii's C++ does and the C step agrees with step_numpy to the bit.
    command = (f'"{VCVARS}" >nul && cl /nologo /O2 /fp:precise /LD "{SOURCE}" '
               f'/Fo"{build_dir}\\\\" /Fe"{DLL}" /link /IMPLIB:"{build_dir}\\waifu_physics_step.lib"')
    proc = subprocess.run(f'cmd /s /c "{command}"', shell=True, capture_output=True, text=True)
    print(proc.stdout.strip())
    if proc.returncode != 0 or not os.path.exists(DLL):
        print(proc.stderr.strip())
        print("compiling the C step failed")
        return False
    print("built", os.path.relpath(DLL, REPO))
    return True


def fetch_linux():
    """The Linux C step from the latest successful linux-step workflow run, if that run includes the
    committed step.c (and step.c has no uncommitted changes). Packaging goes on without it otherwise."""
    so = os.path.join(PACKAGE, "bin", "waifu_physics_step.so")

    def run(*args):
        return subprocess.run(args, cwd=REPO, capture_output=True, text=True)
    if os.path.exists(so):
        os.remove(so)                                    # never package a stale one
    if run("git", "diff", "--quiet", "HEAD", "--", SOURCE).returncode != 0:
        print("step.c has uncommitted changes: no Linux C step (push them for the workflow to build)")
        return
    found = run("gh", "run", "list", "--workflow", "linux-step.yml", "--status", "success", "--limit", "1",
                "--json", "databaseId,headSha", "-q", r'.[0] | "\(.databaseId) \(.headSha)"')
    if found.returncode != 0 or not found.stdout.strip():
        print("no Linux C step: the GitHub CLI or a successful linux-step run is missing")
        return
    run_id, head = found.stdout.split()
    changed = run("git", "log", "-1", "--format=%H", "--", SOURCE).stdout.strip()
    if run("git", "merge-base", "--is-ancestor", changed, head).returncode != 0:
        print("no Linux C step: the last successful linux-step run predates step.c (push, then wait for it)")
        return
    got = run("gh", "run", "download", run_id, "--name", "waifu_physics_step-linux-x64",
              "--dir", os.path.join(PACKAGE, "bin"))
    print("fetched the Linux C step" if got.returncode == 0 and os.path.exists(so)
          else "fetching the Linux C step failed: " + got.stderr.strip())


def blender(*args):
    proc = subprocess.run([BLENDER, "--command", "extension", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def main(argv):
    if not build_dll():
        return 1
    if "--dll-only" in argv:
        return 0
    fetch_linux()
    if not blender("validate", PACKAGE):
        print("the manifest or package did not validate")
        return 1
    os.makedirs(os.path.join(REPO, "dist"), exist_ok=True)
    if not blender("build", "--source-dir", PACKAGE, "--output-dir", os.path.join(REPO, "dist")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
