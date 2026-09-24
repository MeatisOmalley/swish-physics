# Swish Physics

Bone-chain physics for Blender: hair, skirts, tails and accessories. The simulation is a faithful port of [Kawaii Physics](https://github.com/pafuhana1213/KawaiiPhysics), the Unreal Engine plugin, so physics tuned in Blender behaves the same in a game that runs Kawaii Physics. Around it sit the things Blender needs: keyframed settings, live playback with an optional cache, collider objects and selection-based editing.

**Status:** v1.1, every phase of the [plan](docs/superpowers/plans/2026-09-23-swish-physics.md) done: the simulation, colliders, links, the cache, saved setups, external forces, wind and sync bones.

## Using it

Everything is in the 3D Viewport sidebar, on the **Swish** tab.

1. Select an armature, enter Pose Mode, select the first bone of each chain and click **New Group**. A group is one Kawaii Physics node: one set of settings for all of its chains.
2. Pick a **Preset** (Hair or Skirt) as a starting point, then press **Simulate** and play the timeline.
3. Tune in the **Physics** panel. Every setting can be keyframed. The curve button next to a setting varies it from root to tip. With **Edit Selected Groups** on, a change reaches every group that holds a selected bone.
The group list is a tree of armatures and their groups; the cursor toggle beside **New Group** limits it to the selected armature, and off it lists every armature with a group. Clicking a group edits it. In **Chains**, tick chains (or select their bones) to **Split** them into a new group with the same settings, **Move to** another group (moving all of them merges the two), remove them, or select them in the viewport.
4. For skirts and capes, select the panels' chains and click **Link as Loop** or **Link as Strip** in **Links**. Linked chains keep their spacing.
5. Add colliders in **Colliders**. A collider is a real object parented to a bone. Move, rotate and scale it like any object, and set its shape on its modifier.
6. Push chains around in **Forces and Wind**. There are Kawaii's five external forces: Basic, Gravity, Curve, Wind and Procedural Wind. There is also a simple constant force, and scene wind that gusts. Blender's Wind force fields are the scene's wind.
7. Keep a skirt out of the legs with **Sync Bones**. Make a thigh the active bone, select skirt bones, and click **+**. The skirt's pose then follows the thigh's movement.

The input is the pose Blender evaluates each frame. It includes animation, constraints, drivers and IK, so hair under a head that copies another rig follows it. Before each frame, Swish clears its own output from the chain bones, so last frame's physics never feeds back. Keyed chain channels keep their animation, and unkeyed ones start from rest. Start a group below any constrained bones: Blender applies a constraint after the simulation's output, so a constrained bone in a chain cannot be moved. The panel warns when a group contains one.

**Speed.** Blender evaluates each frame once, whether Swish is off, live or cached. While simulating, Swish takes over the chain bones' keyframes: it mutes those F-curves and samples them itself, so Blender keeps what Swish writes before each frame. The keys still drive the simulation, and they are unmuted when Simulate is off, in every saved file, and on load after a crash. Live playback solves before Blender evaluates the frame, so the body's movement reaches the hair one frame late. Jumps and the first frame use the exact, slower path. Cache All evaluates only what the simulation reads (the armatures, their constraint targets, colliders and wind fields) and hides everything else while it bakes. A chain channel animated by an NLA strip or a driver can't be taken over, and that rig falls back to two evaluations per frame.

**Playback.** Live mode prioritizes responsiveness. It uses enough substeps for a normal frame even at 12 fps, and at scene rates above the chosen simulation rate it raises the preview rate to avoid repeated poses. **Cache** keeps frames produced during live playback so you can scrub them, but that opportunistic cache depends on playback order. **Cache All** is the deterministic bake: it samples the input animation on the chosen fixed simulation clock from the start of the range, then stores each output frame. At 120 fps, frames between solver ticks are interpolated. Equivalent animation at equivalent times gives the same baked motion at 12, 24, 30, 60 or 120 fps; changing the FPS without retiming frame-numbered keyframes changes the animation's timing. Frames outside the cache show the unsimulated pose. The cache is in memory and is cleared by changes that affect the result.

**Sharing.** **Copy Settings** and **Paste Settings** move a group's settings and curves between groups. **Export Setup** and **Import Setup** save an armature's groups, links, curves and colliders to JSON and load them back. A setup moved onto another armature with the same bone names behaves identically.

## Parity with Kawaii Physics

The rule: the simulation step does exactly what Kawaii Physics does, at the pinned commit, and anything Blender-specific lives outside it. The step reproduces Kawaii's own golden test bit for bit, in C (Windows) and in numpy (everywhere else), and the two backends agree bit for bit.

To see the same motion in a game:

- Run Kawaii Physics at commit `64cbc77`. A setup records the commit it follows.
- Convert lengths to centimetres: Blender units × 100 × the scene's unit scale. Gravity, radius, tip length and teleport distance are lengths. Damping, stiffness and world damping have no units.
- Curves are exported as the 65 linear keys the solver used, which Kawaii's `FRichCurve` evaluates to the same values.

Where Swish's defaults and conventions differ from Kawaii's:

- **Gravity.** It is on by default: (0, 0, −1) scaled by the scene's gravity. Kawaii's default is zero.
- **Steps.** Steps per second and steps per frame are scene settings, as they are project settings in Kawaii. Live preview may raise the rate or the per-frame step cap to keep up with Blender's timeline; Cache All uses the configured fixed rate without dropping elapsed time.
- **Collider sizes.** They scale with the collider object and its bone. Kawaii does not scale collider radii.
- **Capsule axis.** Capsules run along the collider's local Y. Kawaii's capsules run along Z.
- **Random draws.** Random force scales, the scene wind's gust and the Wind force's noise are seeded by timeline frame in live preview and by fixed solver tick in Cache All, so a baked motion remains reproducible across scene frame rates. Kawaii draws them from Unreal's unseeded random stream. Procedural wind is seeded in both and matches exactly.
- **Wind sources.** A Blender Wind force field stands in for Unreal's wind sources. It blows along its local Z, and its Strength is Unreal's wind Speed.

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
