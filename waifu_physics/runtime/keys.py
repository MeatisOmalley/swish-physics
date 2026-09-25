"""Waifu Physics takes over the chain bones' keyframes while it simulates.

Blender applies a bone's keyframes every time it evaluates a frame, after any
value Waifu Physics wrote before the evaluation. So while it simulates, Waifu Physics mutes
the F-curves of the chain bones' channels in the armature's action and samples
them itself (FCurve.evaluate, as Instant Physics does): Blender then keeps
what Waifu Physics writes before a frame -- a cached frame, or a live solve -- and
evaluates each frame once instead of twice.

Only curves in the armature's own action are taken over. A chain channel that
an NLA strip or a driver animates cannot be, and a rig with one keeps the
slower path (write after evaluation, evaluate again). An action blended at
less than full influence, or not replacing, is left alone the same way.

Muted curves are the user's data: they are unmuted whenever Waifu Physics stops
simulating, before every save (and muted again after), and on load if a
previous session did not get to (the armature remembers what Waifu Physics muted).
"""
import json

import numpy as np

MARK = "waifu_physics_muted"       # object ID property: the curves Waifu Physics muted, to recover after a crash
LEGACY_MARK = "swish_muted"         # the same, from before the add-on was renamed
CHANNELS = {"location": 3, "rotation_quaternion": 4, "rotation_euler": 3, "rotation_axis_angle": 4, "scale": 3}


def _bone_path(path):
    """('bone name', 'channel') for a pose bone transform path, else None."""
    if not path.startswith('pose.bones["'):
        return None
    end = path.find('"]', 12)
    if end < 0:
        return None
    channel = path[end + 3:]
    return (path[12:end], channel) if channel in CHANNELS else None


def _action_curves(data):
    """F-curves of the armature's own action for its slot, every layer and strip."""
    action, slot = data.action, data.action_slot
    if action is None or slot is None:
        return []
    found = []
    for layer in action.layers:
        for strip in layer.strips:
            bag = strip.channelbag(slot)
            if bag is not None:
                found += list(bag.fcurves)
    return found


class ChainKeys:
    """The chain bones' keyframe curves of one rig, which Waifu Physics samples while it owns them."""

    def __init__(self, rig):
        obj = rig.obj
        self.obj = obj
        self.curves = []                   # (fcurve, bone index, channel, array index)
        self.ownable = True
        self.muted = False
        self.total = 0                     # chain curves in the action, muted or not: notices new keys
        self.user_muted = set()            # chain curves the user muted: left alone, and their channels at rest
        data = obj.animation_data
        if data is None:
            return
        chain = {rig.names[i] for i in np.flatnonzero(rig.chain)}
        for curve in _action_curves(data):
            found = _bone_path(curve.data_path)
            if found and found[0] in chain:
                self.total += 1
                if curve.mute:
                    self.user_muted.add((curve.data_path, curve.array_index))
                else:
                    self.curves.append((curve, rig.index[found[0]], found[1], curve.array_index))
        # Animation Waifu Physics cannot take over: NLA strips and drivers on chain channels, or an
        # action that does not simply replace.
        foreign = []
        for track in data.nla_tracks:
            if track.mute:
                continue
            for strip in track.strips:
                if strip.mute or strip.action is None:
                    continue
                slot = getattr(strip, "action_slot", None)
                for layer in strip.action.layers:
                    for s in layer.strips:
                        bag = s.channelbag(slot) if slot is not None else None
                        foreign += [c.data_path for c in (bag.fcurves if bag else ())]
        foreign += [d.data_path for d in data.drivers]
        if any(_bone_path(p) and _bone_path(p)[0] in chain for p in foreign):
            self.ownable = False
        if self.curves and (data.action_influence < 1.0 or data.action_blend_type != "REPLACE"):
            self.ownable = False

    def count(self, rig):
        """Chain curves in the action now, muted or not."""
        data = self.obj.animation_data
        if data is None:
            return 0
        chain = {rig.names[i] for i in np.flatnonzero(rig.chain)}
        return sum(1 for c in _action_curves(data) if _bone_path(c.data_path)
                   and _bone_path(c.data_path)[0] in chain)

    def mutes_changed(self, rig):
        """Has a chain curve's mute been flipped since the curves were taken over? A curve the user unmuted
        must be taken over, one they muted let go; one of ours unmuted would be applied over the physics.
        Any of them: take the curves over again (Waifu Physics' own mute cannot show the user's)."""
        data = self.obj.animation_data
        if data is None:
            return bool(self.user_muted)
        chain = {rig.names[i] for i in np.flatnonzero(rig.chain)}
        owned = {(c.data_path, c.array_index) for c, *_ in self.curves} if self.muted else set()
        user_muted, owned_unmuted = set(), False
        for curve in _action_curves(data):
            found = _bone_path(curve.data_path)
            if not found or found[0] not in chain:
                continue
            key = (curve.data_path, curve.array_index)
            if key in owned:
                owned_unmuted = owned_unmuted or not curve.mute
            elif curve.mute:
                user_muted.add(key)
        return owned_unmuted or user_muted != self.user_muted

    def mute(self):
        """Take the chain curves over. Only curves not already muted are touched."""
        if not self.ownable or not self.curves or self.muted:
            return
        for curve, *_ in self.curves:
            curve.mute = True
        self.obj[MARK] = json.dumps([(c.data_path, c.array_index) for c, *_ in self.curves])
        self.muted = True

    def unmute(self):
        if not self.muted:
            return
        for curve, *_ in self.curves:
            try:
                curve.mute = False
            except ReferenceError:
                pass
        try:
            if MARK in self.obj:
                del self.obj[MARK]
        except ReferenceError:
            pass
        self.muted = False

    def sample(self, frame, buffers):
        """Write the sampled keyframe values at frame into per-bone channel buffers."""
        for curve, bone, channel, index in self.curves:
            buffers[channel][bone * CHANNELS[channel] + index] = curve.evaluate(frame)


def recover(objects):
    """Unmute curves a previous session left muted (it ended while simulating)."""
    for obj in objects:
        for mark in (MARK, LEGACY_MARK):
            if mark not in obj.keys():
                continue
            try:
                wanted = {tuple(entry) for entry in json.loads(obj[mark])}
            except (TypeError, ValueError):
                wanted = set()
            data = obj.animation_data
            if data is not None:
                for curve in _action_curves(data):
                    if (curve.data_path, curve.array_index) in wanted:
                        curve.mute = False
            del obj[mark]
