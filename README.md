# Swish Physics

Bone-chain physics for Blender: hair, skirts, tails and accessories. The simulation is a faithful port of [Kawaii Physics](https://github.com/pafuhana1213/KawaiiPhysics), the Unreal Engine plugin, so physics tuned in Blender behaves the same in a game that runs Kawaii Physics. Around it sit the things Blender needs: keyframed settings, live playback with an optional cache, collider objects and selection-based editing.

**Status:** v1 (phases 0–7 of the [plan](docs/superpowers/plans/2026-09-23-swish-physics.md)). Procedural wind, sync bones and external forces are planned for v1.1.

## Using it

Everything is in the 3D Viewport sidebar, on the **Swish** tab.

1. Select an armature, enter Pose Mode, select the first bone of each chain and click **New Group**. A group is one Kawaii Physics node: one set of settings for all of its chains.
2. Pick a **Preset** (Hair or Skirt) as a starting point, then press **Simulate** and play the timeline.
3. Tune in the **Physics** panel. Every setting can be keyframed. The curve button next to a setting varies it from root to tip. With **Edit Selected Groups** on, a change reaches every group that holds a selected bone.
4. For skirts and capes, select the panels' chains and click **Link as Loop** or **Link as Strip** in **Links**. Linked chains keep their spacing.
5. Add colliders in **Colliders**. A collider is a real object parented to a bone. Move, rotate and scale it like any object, and set its shape on its modifier.

Keyed bone channels are the input. The simulation starts from whatever animation the chain bones have, and unkeyed chain bones start from rest.

**Playback.** Live mode simulates while the timeline plays and stores nothing. **Cache** keeps each simulated frame so you can scrub and render. **Cache All** fills the frame range. Frames the cache has not reached show the unsimulated pose until they are played, as cloth does. Any change that affects the result clears the cache.

**Sharing.** **Copy Settings** and **Paste Settings** move a group's settings and curves between groups. **Export Setup** and **Import Setup** save an armature's groups, links, curves and colliders to JSON and load them back. A setup moved onto another armature with the same bone names behaves identically.

## Parity with Kawaii Physics

The rule: the simulation step does exactly what Kawaii Physics does, at the pinned commit, and anything Blender-specific lives outside it. The step reproduces Kawaii's own golden test bit for bit, in C (Windows) and in numpy (everywhere else), and the two backends agree bit for bit.

To see the same motion in a game:

- Run Kawaii Physics at commit `64cbc77`. A setup records the commit it follows.
- Convert lengths to centimetres: Blender units × 100 × the scene's unit scale. Gravity, radius, tip length and teleport distance are lengths. Damping, stiffness and world damping have no units.
- Curves are exported as the 65 linear keys the solver used, which Kawaii's `FRichCurve` evaluates to the same values.

Where Swish's defaults and conventions differ from Kawaii's:

- **Gravity.** It is on by default: (0, 0, −1) scaled by the scene's gravity. Kawaii's default is zero.
- **Steps.** Steps per second and steps per frame are scene settings, as they are project settings in Kawaii.
- **Collider sizes.** They scale with the collider object and its bone. Kawaii does not scale collider radii.
- **Capsule axis.** Capsules run along the collider's local Y. Kawaii's capsules run along Z.

## Development

```text
python tools/run_tests.py            run every headless test in Blender
python tools/run_tests.py register   run the tests whose file name contains "register"
python tools/release.py              compile the C step (Windows), validate, build dist/swish_physics-<version>.zip
python tools/release.py --dll-only   only compile the C step into swish_physics/bin/
```

`SWISH_BLENDER` points the tools at a Blender executable (default: the Steam install). `SWISH_VCVARS` points the release script at `vcvars64.bat` (default: Visual Studio 2022 Build Tools).

Tests are Blender scripts in `tests/`. Each prints one `[PASS]` or `[FAIL]` line per check and exits non-zero on failure; `tests/_harness.py` has the helpers.

## Credits and licence

The simulation follows Kawaii Physics by pafuhana1213, MIT licence, at commit `64cbc77ad4d75f6eb8c8f5673b4b4452f838ec21`. Its notice ships in [`swish_physics/THIRD_PARTY_NOTICES.txt`](swish_physics/THIRD_PARTY_NOTICES.txt). The interface takes ideas from Swingy Bone Physics but none of its code.

Swish Physics is GPL-3.0-or-later ([LICENSE](LICENSE)), like other Blender add-ons.
