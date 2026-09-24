# Swish Physics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each phase is expanded into step-level tasks when it starts; this document fixes the design and the order.

**Goal:** A general-purpose Blender add-on that puts physics on bone chains quickly and plays it back fast, with behaviour identical to the Kawaii Physics Unreal plugin so that anything tuned in Blender means the same thing in a game.

**Architecture:** A port of Kawaii Physics' simulation step, run as a pure function over flat arrays that hold every chain point in the scene. The step exists twice, in C (a ctypes DLL, Windows) and in numpy (every platform, and the reference the C step is tested against); both run the same order and formulas and agree exactly. Blender I/O is batched: one bulk read of pose matrices and one bulk write of rotations per armature per frame. Physics runs live by default, with an optional in-memory cache for scrubbing and rendering. The editing model follows Kawaii's: settings belong to groups (one group is one Kawaii node), varied along each chain by curves. Colliders are real objects whose shapes are drawn by Geometry Nodes.

**Tech Stack:** Blender 5.2 (Python, numpy 2.3, bpy, gpu, Geometry Nodes), C via ctypes (MSVC 2022 Build Tools), Kawaii Physics source as the behavioural specification.

**Reference:** Kawaii Physics, MIT, <https://github.com/pafuhana1213/KawaiiPhysics>, pinned at `64cbc77ad4d75f6eb8c8f5673b4b4452f838ec21` (2026-09-20). Behaviour is ported from that commit; any later Kawaii change is a deliberate re-port. Credit it in the README and in the step sources.

---

## Established by measurement

Recorded 2026-09-23 so implementation does not re-derive them. Benchmark and probes: `research/2026-09-23-step-benchmark/`.

| Finding | Evidence |
| --- | --- |
| Geometry Nodes can read bones but not write them | Blender 5.2.2 has `GeometryNodeBoneInfo` (inputs Armature, Bone Name; outputs Pose, Local Pose, Transform Pose, Rest Pose, Rest Length, Exists) and no node that sets a bone (`probe_gn_bones.py`) |
| Kawaii has no per-bone settings | `FKawaiiPhysicsSettings` is one set per node (`AnimNode_KawaiiPhysics.h:263`); each value is multiplied by a curve at the bone's length rate, root to tip (`:357-413`, applied in `UpdatePhysicsSettingsOfModifyBones`, `AnimNode_KawaiiPhysicsSimulation.cpp:203`); extra root bones override only their excluded bones (`KawaiiPhysicsTypes.h:152`) |
| Kawaii's defaults that affect parity | Fixed substepping on, at most 4 substeps a frame (`KawaiiPhysicsDeveloperSettings.h:41,52`); target framerate 60; gravity (0,0,0); `DummyBoneLength` 0, so tip bones do not rotate unless it is set; component-space simulation; per-bone defaults Damping 0.1, Stiffness 0.05, World Damping 0.8/0.8 |
| Kawaii's step order | `SimulateOnce` (`AnimNode_KawaiiPhysicsSimulation.cpp:799`): roots follow the pose; per bone, parents first: velocity from displacement / previous dt, damping, wind, gravity, integrate, world-movement follow, stiffness pull toward the pose; XPBD links (iterations before collision); collisions; links (iterations after); angle limit, planar constraint, length restore, parents first. Rotations come from positions in `ApplySimulateResult` (`:1396`) |
| XPBD link compliance | Material table in `AnimNode_KawaiiPhysicsCollision.cpp:1413`: Concrete 4e-11, Wood 1.6e-10, Leather 1e-9, Tendon 2e-9, Rubber 1e-7, Muscle 2e-5, Fat 1e-4; divided by dt²; λ reset before each group of iterations |
| numpy and C can agree exactly | Same order and formulas, links in batches that share no point and collisions in a fixed per-point order: 0.0 m difference over 240 steps on Peach's dress and hair (`kstep_bench.py`) |
| C is ~15x faster to solve | Peach dress + hair (178 points, 32 chains, 28 colliders, 30 links), 60 Hz steps at 24 fps, headless, our code only: numpy 5.8 ms a frame (solve 4.9), C 1.2 ms (solve 0.35). Five characters: numpy 11.3 ms, C 2.7 ms. With C, Blender I/O dominates: writing rotations is 0.66 ms of 1.2 |
| numpy's cost is per call, not per point | Every phase cost about the same for 42 and 136 points; collisions ran one numpy pass per collider slot (22) and were the largest phase |
| The port reproduces Kawaii to the bit | Kawaii's own golden test (`KawaiiPhysicsGoldenTest.cpp`: chain, legacy chain, links, sphere/capsule/box/plane collisions with an angle limit; 200 frames each) matches 156 of 156 doubles bit for bit on both steps (`tests/test_solver_golden.py`). It took Kawaii's exact precisions: `DistSq` and `Dist` in sphere collision are floats, vector division multiplies by the reciprocal, `DegreesToRadians(double)` uses the double pi, and the test's `FVector(0.3f, ...)` inputs are floats |
| C and numpy agree to the bit on every feature | Six random scenes (tip, inter-bone and bridge dummies, links, all collider types, curves, planar constraints, legacy and substep modes, teleports), 170 frames each (`tests/test_solver_agreement.py`) |
| The whole frame loop is fast in C | Peach-sized scene (160 points, 32 links, 56 colliders), 24 fps: numpy 4.1 ms a frame, C 0.26 ms; five characters: numpy 7.7 ms, C 0.55 ms (`tests/bench_solver.py`, Blender I/O not included) |
| Curves agree with the game by construction | Each Blender curve is sampled into 65 linear keys; the solver evaluates them exactly as `FRichCurve::Eval` does linear keys (bit for bit against a transcription, `tests/test_curves.py`), so an export of those keys gives Unreal the same values. The host node group's leading dot keeps it out of the node Add menu (`node_add_menu.py` skips such groups unless Show Hidden IDs is on) |
| Posed chain lengths differ from rest | Peach MAXED's hair chains hang from `J_Scale_J_Bip_C_Head` at pose scale (2.44, 2.11, 2.23): up to 271 mm longer than rest. Kawaii restores posed lengths, so the solver handles it |

## Design decisions

1. **A faithful port of Kawaii, plus what Blender needs.** The simulation itself is Kawaii's: same settings, same order, same formulas, so a Blender preview means the same thing in a game running Kawaii. Around it sit Blender-specific features Kawaii has no need for: keyframed settings, live playback and the cache, collider objects, selection-based editing. New simulation features wait until the port is complete. Consumers may trim or extend for their own targets (VRoid Swap will, for the WaifuSim export) without changing the solver. Swingy Bone Physics (GPL-3) is a reference for workflow and UI conventions only; none of its code is used.
2. **v1 scope:** Verlet core, damping, gravity, stiffness pull, world damping (location and rotation), fixed 60 Hz substeps with a substep cap, teleport reset, warm-up, tip dummies, curves along the chain, angle limit, planar constraint, sphere / capsule / tapered capsule / box / plane colliders, XPBD links with iterations before and after collision, bone subdivision and bridge dummies. **v1.1:** procedural wind, sync bones, external forces. **Never:** Unreal world and simple-world collision, animation notifies, gust and multiplier triggers, LOD handling, mirror tables.
3. **The step is a pure array function, twice.** C for Windows through ctypes; numpy everywhere else and as the reference. Tests require them to agree. The DLL exports a version number; a missing DLL, another platform or a version mismatch falls back to numpy.
4. **One system for the whole scene.** Every chain point of every armature lives in one set of arrays, sorted by chain depth so parents come first. numpy works a depth at a time (identical to Kawaii's bone-by-bone order), links in batches that share no point (a reordering of Kawaii's link list, so the order can be kept in any Kawaii setup), collisions one collider slot at a time with each point's colliders in Kawaii's order.
5. **Live by default, cache optional.** Live simulates while playing and stores nothing. The cache, a checkbox, keeps each frame's solver state and output rotations in memory, replays them when scrubbing, and clears itself on any change that affects the result. **Cache All** fills the frame range. Uncached frames show the unsimulated pose until played, as cloth does. Memory only; keyframes are not used as a cache.
6. **Keyed chain channels are the input.** *(Revised 2026-09-24: the input is Blender's evaluated pose, read after frame_change_pre has cleared the chains' channels to their keyed values or rest, so constraints, drivers and IK reach the solver without feedback. Rebuilding chain bones from keyed channels dropped constraints on them, e.g. a group rooted at a Copy Transforms bone saw no movement. A panel warning flags constrained bones inside a group.)* Originally: In a post-frame-change handler, after Blender has evaluated animation, a keyed chain channel holds its animated value: that is the solver's pose input. Unkeyed chain bones use their rest pose. Which channels are keyed is read from the action when simulation starts and whenever animation changes. We never evaluate fcurves ourselves.
7. **Rotation modes are never changed.** Output is written in each bone's own rotation mode (quaternion, the six Euler orders, axis-angle), in bulk, as our VRM solver already does.
8. **Groups are the unit of tuning.** A group is one Kawaii node: settings, curves, root bones and excluded bones, links, and which collider sets affect it. Selecting bones acts on their whole chains (New Group, Add to Group); clicking a bone makes its group active; Alt-edit spans the selected bones' groups. Settings are animatable properties, read in bulk each step.
9. **Curves edited like Swingy's, hosted in node groups.** A hidden node group holds a Float Curve node per curve and the panel draws it with `template_curve_mapping`. Brush datablocks (Swingy's host) are assets in Blender 5 and would clutter the brush shelves.
10. **Colliders are real objects.** A mesh object parented to a bone, its shape generated by one shared Geometry Nodes group from modifier inputs (a shape menu, radius, second radius, length, extents). Wire display, drawn in front, never rendered, in its own collection, tagged so exporters skip it. Capsules run along the object's local Y, like a bone (Kawaii's capsules run along Z; the solver turns them by -90° about X). Object scale, and the bone's pose scale, multiply the shape: sphere radius by the largest axis, capsule radius by X and Z and length by Y, box extents by X, Y and Z. Kawaii does not scale collider radii; this is Blender-side and a consumer converts before export. The node group draws exactly what the solver uses. Only shape-value changes rebuild geometry, so colliders stay on the fast draw path.
11. **Collider sets belong to armatures; groups choose them.** A garment's groups can collide with another armature's set, which is how a character's body colliders are shared by every garment.
12. **Links.** Link Chains on two or more chains of a group joins neighbours depth by depth, tip dummies included (Kawaii's `bAutoAddChildDummyBoneConstraint`). Loop (skirts) or Strip (capes), ordered by angle around the chains' centre. Group-level compliance preset and iteration counts, per-link override, drawn as viewport lines.
13. **No Unreal export.** The add-on exposes a versioned serialise / deserialise API for its setup (groups, settings, curves, links, colliders): `data/serialize.py`, JSON-safe dicts, curves as control points plus the 65 linear keys the solver used, colliders in their bone's head frame, a group's own armature in its collider sets stored as null so a setup moves between armatures. Steps per second and steps per frame are scene settings (project settings in Kawaii) and ride along. Game packaging belongs to whoever consumes it; for WaifuSim that is the VRoid Swap add-on, which wraps all character metadata into a PNG.
14. **Performance is designed in.** No per-bone Python in the frame loop; per-frame Blender calls are counted and kept to one read and one write per armature; the C step's pointers are bound once, not per call.

## Module layout

```text
swish_physics/
  blender_manifest.toml        extension manifest; id "swish_physics"
  __init__.py                  register / unregister (reload-safe: handlers removed, simulation stopped)
  solver/
    layout.py                  builds the scene-wide arrays: points by depth, parents, roots, tip dummies,
                               per-point settings, collider slots, link batches
    step_numpy.py              the reference step
    step.c, build.bat          the C step -> bin/swish_step.dll
    native.py                  loads the DLL, checks its version, binds pointers once, falls back to numpy
  runtime/
    io.py                      bulk pose read, keyed-channel input, rotations from positions, mode-aware bulk write
    live.py                    handlers: post-frame-change step, fixed 60 Hz clock, teleport reset, warm-up
    cache.py                   per-frame state, replay, invalidation, Cache All
  data/
    props.py                   groups, chains, links, collider sets (PropertyGroups on the armature object)
    curves.py                  hidden node-group Float Curve hosts, sampling for the step
    colliders.py               the collider node group, collider objects, scale interpretation
    links.py                   Link Chains, ring / strip ordering
  ui/
    panels.py                  sidebar tab "Swish"
    draw.py                    viewport overlay: links, chains, group colours
    ops.py                     operators
  serialize.py                 versioned to_dict / from_dict
tests/                         headless Blender test scripts and a runner
research/                      spikes kept for reference
```

## Phases

Each phase ends with its tests passing headless and, where it changes what the modder sees, a real-window check with a screenshot.

### Phase 0: Scaffold

- [x] Extension manifest, `__init__.py` with reload-safe register and unregister, empty sidebar tab.
- [x] Test runner: runs every `tests/test_*.py` in headless Blender 5.2 and reports pass / fail per check.
- [x] Release script: builds the DLL, assembles the extension zip with `bin/swish_step.dll`.

### Phase 1: The solver step

- [x] `layout.py`: scene arrays from a description of chains (no bpy), points sorted by depth, tip dummies when a group's dummy length is above 0, link batches, collider slots.
- [x] `step_numpy.py`: the full v1 step in Kawaii's order (decision 2), in scene units, with the Kawaii source lines each part ports cited in comments.
- [x] Unit tests against hand-worked cases: a single pendulum under gravity, damping decay, stiffness pull convergence, a sphere and each collider shape pushing a point out, the angle limit, length restore, a two-point XPBD link at each compliance preset.
- [x] `step.c` in lockstep, `native.py` loader with version check and fallback.
- [x] Agreement test: the same randomised scenes through numpy and C for 1000 steps; exact agreement, or within 1e-12 where a formula's evaluation order cannot match.
- [x] Benchmark kept in `tests/`: per-phase timings for one and five characters, so later changes are measured against today's numbers.

### Phase 2: Blender runtime, live mode

- [x] Groups on the armature object; New Group and Add to Group from selected bones (whole chains); remove; exclude bones.
- [x] `io.py`: bulk pose read; keyed-channel detection from the object's action; unkeyed chain bones at rest; rotations from positions; bulk write in each bone's own rotation mode.
- [x] `live.py`: post-frame-change step with a fixed 60 Hz clock and a substep cap of 4; pose interpolation across substeps; teleport reset by distance and rotation thresholds; warm-up frames.
- [x] Settings read in bulk every step, so keyframed settings take effect as they play.
- [x] Tests: a keyed chain follows its animation under stiffness 1; an unkeyed chain hangs from its rest pose; a bone in each rotation mode keeps its mode; keyframed damping changes behaviour mid-playback; teleport resets.

### Phase 3: Curves and the group panel

- [x] `curves.py`: a hidden node group per armature with a Float Curve per curved setting; values sampled per point when the layout is built or a curve changes.
- [x] Panel: active group's settings and curves; Alt-edit across selected bones' groups; clicking a bone activates its group.
- [x] Verify the Float Curve host in a real window: widget draws, edits evaluate, nothing appears in brush shelves or asset browsers.

### Phase 4: Colliders

- [x] The collider node group: shape menu and dimensions; draws the solver's interpretation of object scale.
- [x] Add Collider on the active bone; collider sets per armature; groups choose sets, including other armatures'.
- [x] Collider transforms read in bulk each step.
- [x] Tests: each shape pushes points out where its drawing says; object scale and bone scale change the collision the way the drawing shows.

### Phase 5: Links

- [x] Link Chains, Loop and Strip, angular ordering, tip dummies included.
- [x] Group compliance preset and iteration counts, per-link override.
- [x] Bone subdivision and bridge dummies (collision points along bones and along links, with the bridge feedback that pushes real bones).
- [x] Viewport drawing of links.
- [x] Tests: a linked ring keeps its spacing under gravity and sway; a collider between two linked chains pushes both.

### Phase 6: Cache

- [x] Per-frame state and rotations in memory; replay on scrub; Cache All over the frame range.
- [x] Invalidation from a depsgraph handler that ignores updates our own writes cause: settings, curves, groups, links, colliders, the armature's and the collider armatures' animation, object transform animation, frame range, fps.
- [x] Render: verify that a render reads cached frames reliably, and that a render from the first frame simulates correctly live.
- [x] Tests: replay matches live bit for bit; each invalidation trigger clears; Cache All then scrub shows the same poses.

### Phase 7: Serialisation and presets

- [x] `serialize.py`: versioned `to_dict` / `from_dict` for groups, settings, curves (control points and dense samples), links, collider sets.
- [x] Hair and skirt group presets.
- [x] README with credits (Kawaii Physics, MIT) and the parity rule.

### Phase 8 (v1.1)

- [x] Procedural wind, sync bones, external forces, from the pinned Kawaii commit.
- [x] Tests: Kawaii's procedural-wind checks (hash snapshot values, noise, sway, ripple, strength cycle, seeds); each force's effect; sync bones; C and numpy agreeing to the bit with every force on; the cache resuming procedural wind exactly; saved setups carrying forces and sync bones.

Decisions made while porting phase 8:

- **Forces run once a frame, the step adds `vector * dt`.** Kawaii's forces prepare in PreApply once per SimulateModifyBones and add a frame-constant vector per bone in each substep, so `solver/forces.py` evaluates them in Python and the step only adds them at Kawaii's hook points (scene wind after damping, velocity forces after gravity, the simple force after integration, position forces after world-movement follow). The C and numpy steps still agree to the bit.
- **Randomness is seeded by frame.** Kawaii draws RandomForceScaleRange, the scene wind's gust and the Wind force's direction noise from Unreal's unseeded global stream. Swish uses Unreal's FRandomStream seeded by the frame number, so a frame simulates the same every time (the cache and renders need that). Procedural wind is seeded in Kawaii too and matches it exactly, sinf from the C runtime included.
- **Wind sources are Blender Wind force fields.** A field blows along its local Z; its Strength is Unreal's wind Speed, used as is. Only directional wind: field shape and falloff are ignored.
- **Gravity force strength is an acceleration.** Its direction is a unit vector, so the random scale range carries the magnitude, converted like any length. Character gravity direction and scale are not ported (there is no character).
- **Sync bones follow Kawaii's game build.** A target without children keeps a scale of 1 even with a length-rate curve (the editor build re-evaluates it at 0).
- **Every bone below a root is placed at its simulated head**, as ApplySimulateResult sets each such bone's location. Sync bones show only through that translation, because rotations are measured against the synced pose. Connected bones ignore location in Blender and will not show sync movement.
- **Not ported:** gust requests, shared wind publishers, transient and one-shot forces, custom (Blueprint) external forces, animation notifies.

## Performance (2026-09-24)

Measured on swaptest.blend with 202 hair and skirt bones and unthrottled playback. On the first runs, with the machine's baseline at 61-72 ms per frame, live mode and keyed cached playback each paid a full second scene evaluation (+45 to 51 ms), and Cache All took 16-18 s for 120 frames. In later runs the Swish-off baseline measured 41.6 ms. Unkeyed chains: live 42.5 ms, cached 41.7 ms. Keyed chains: live 46.1 ms, cached 42.3 ms. Cache All: 2.1-2.6 s. How:

- **One evaluation a frame.** Blender applies keyframes after anything written before evaluation, so while simulating Swish mutes the chain bones' F-curves in the armature's own action and samples them itself (`runtime/keys.py`). Curves are unmuted when simulation stops, around every save, and on load after a crash (an ID property records what was muted). Chain channels animated by NLA strips or drivers can't be taken over, and that rig keeps the two-evaluation path.
- **Live solves before the frame.** With keys taken over, live playback solves in frame_change_pre from the last evaluated pose: bones outside the chains are a frame late, and chain keys are sampled for this frame. Jumps and resets take the exact path. The user chose this trade (live favours running).
- **Lean bake.** Cache All hides every object the input doesn't depend on (armatures, their parents, constraint and driver targets, colliders and wind fields stay). Results are bit-identical.

## Open items

- **Unit dependence.** Radius, gravity and lengths carry units; damping, stiffness and world damping do not; XPBD compliance is compared with the unit inverse-mass sum, which looks unit-independent. Confirm each before any consumer converts to Unreal centimetres.
- ~~**Render and post-frame-change writes.**~~ Resolved in phase 6: an animation render simulates live and shows each frame's own pose (`tests/test_render_live.py`), and cached frames are replayed in frame_change_pre so a render sees them (`tests/test_cache.py`).
- **Kawaii rotations under scaled parents.** `ApplySimulateResult` builds rotations from pose rotations; with slider-scaled anchors the rotation part must be taken without scale (the benchmark normalised matrix columns). Specify this precisely in phase 2.

## WaifuSim follow-ups (VRoid Swap, not this repo)

- Seed groups, chains, links and colliders from a `.vrm`; split hair groups by gravity, since Kawaii has one gravity per node.
- Body colliders on the base, shared by every garment's groups.
- Ship test locomotion animations on the base so chains are tuned against real movement.
- Replace VRoid Swap's own VRM spring preview with Swish Physics.
- Garment chains are parented to `J_Scale` bones, which reintroduces inherited non-uniform scale that Unreal (no shear) will not reproduce. Decide before export: Blender's Aligned inherit-scale, or anchoring chains to core bones.
- Collider offsets need a Blender to Unreal bone-axis conversion that depends on the skeleton import; measure with a round trip.
- Package the physics setup into the character PNG with the rest of the metadata.
- Run Kawaii Physics in the game from the commit this port follows (`64cbc77`, master, 2026-09-20), not the latest tagged release (v1.21.0, 2026-06-23), which is 312 commits older; otherwise the game can behave differently from the Blender preview.
