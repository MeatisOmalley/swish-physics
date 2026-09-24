# Swish Physics

Bone-chain physics for Blender: hair, skirts, tails and accessories. The simulation is a faithful port of [Kawaii Physics](https://github.com/pafuhana1213/KawaiiPhysics), the Unreal Engine plugin, so physics tuned in Blender behaves the same in a game that runs Kawaii Physics. Around it sit the things Blender needs: keyframed settings, live playback with an optional cache, collider objects and selection-based editing.

**Status:** Phase 0 of the [plan](docs/superpowers/plans/2026-09-23-swish-physics.md): the extension registers and packages; there is no solver yet.

## Development

```text
python tools/run_tests.py            run every headless test in Blender
python tools/run_tests.py register   run the tests whose file name contains "register"
python tools/release.py              compile the C step (Windows), validate, build dist/swish_physics-<version>.zip
```

`SWISH_BLENDER` points the tools at a Blender executable (default: the Steam install). `SWISH_VCVARS` points the release script at `vcvars64.bat` (default: Visual Studio 2022 Build Tools).

Tests are Blender scripts in `tests/`. Each prints one `[PASS]` or `[FAIL]` line per check and exits non-zero on failure; `tests/_harness.py` has the helpers.

## Credits and licence

The simulation follows Kawaii Physics by pafuhana1213, MIT licence, at commit `64cbc77ad4d75f6eb8c8f5673b4b4452f838ec21`. Swish Physics is GPL-3.0-or-later, like other Blender add-ons.
