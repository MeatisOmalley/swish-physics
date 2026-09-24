"""Build the extension zip: compile the C step (Windows), validate, package.

    python tools/release.py

Writes dist/swish_physics-<version>.zip. The C step is compiled with the
Visual C++ build tools into swish_physics/bin/swish_step.dll when its source
exists; on other platforms, or without the build tools, the package ships
without it and the numpy step is used. SWISH_BLENDER and SWISH_VCVARS
override the default Blender and vcvars64.bat locations.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO, "swish_physics")
BLENDER = os.environ.get("SWISH_BLENDER",
                         r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")
VCVARS = os.environ.get("SWISH_VCVARS", r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"
                                        r"\VC\Auxiliary\Build\vcvars64.bat")
SOURCE = os.path.join(PACKAGE, "solver", "step.c")
DLL = os.path.join(PACKAGE, "bin", "swish_step.dll")


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
    command = (f'"{VCVARS}" >nul && cl /nologo /O2 /fp:precise /LD "{SOURCE}" '
               f'/Fo"{build_dir}\\\\" /Fe"{DLL}" /link /IMPLIB:"{build_dir}\\swish_step.lib"')
    proc = subprocess.run(f'cmd /s /c "{command}"', shell=True, capture_output=True, text=True)
    print(proc.stdout.strip())
    if proc.returncode != 0 or not os.path.exists(DLL):
        print(proc.stderr.strip())
        print("compiling the C step failed")
        return False
    print("built", os.path.relpath(DLL, REPO))
    return True


def blender(*args):
    proc = subprocess.run([BLENDER, "--command", "extension", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(proc.stdout.strip() or proc.stderr.strip())
    return proc.returncode == 0


def main():
    if not build_dll():
        return 1
    if not blender("validate", PACKAGE):
        print("the manifest or package did not validate")
        return 1
    os.makedirs(os.path.join(REPO, "dist"), exist_ok=True)
    if not blender("build", "--source-dir", PACKAGE, "--output-dir", os.path.join(REPO, "dist")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
