"""Swish takes over the chain bones' keyframes while it simulates.

Blender applies a bone's keyframes every time it evaluates a frame, after any
value Swish wrote before the evaluation. So while it simulates, Swish mutes
the F-curves of the chain bones' channels in the armature's action and samples
them itself (FCurve.evaluate, as Instant Physics does): Blender then keeps
what Swish writes before a frame -- a cached frame, or a live solve -- and
evaluates each frame once instead of twice.

Only curves in the armature's own action are taken over. A chain channel that
an NLA strip or a driver animates cannot be, and a rig with one keeps the
slower path (write after evaluation, evaluate again). An action blended at
less than full influence, or not replacing, is left alone the same way.

Muted curves are the user's data: they are unmuted whenever Swish stops
simulating, before every save (and muted again after), and on load if a
previous session did not get to (the armature remembers what Swish muted).
"""
import json

import numpy as np

MARK = "swish_muted"                   # object ID property: the curves Swish muted, to recover after a crash
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
    """The chain bones' keyframe curves of one rig, which Swish samples while it owns them."""

    def __init__(self, rig):
        obj = rig.obj
        self.obj = obj
        self.curves = []                   # (fcurve, bone index, channel, array index)
        self.ownable = True
        self.muted = False
        self.total = 0                     # chain curves in the action, muted or not: notices new keys
        data = obj.animation_data
        if data is None:
            return
        chain = {rig.names[i] for i in np.flatnonzero(rig.chain)}
        for curve in _action_curves(data):
            found = _bone_path(curve.data_path)
            if found and found[0] in chain:
                self.total += 1
                if not curve.mute:
                    self.curves.append((curve, rig.index[found[0]], found[1], curve.array_index))
        # Animation Swish cannot take over: NLA strips and drivers on chain channels, or an
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
        if MARK not in obj.keys():
            continue
        try:
            wanted = {tuple(entry) for entry in json.loads(obj[MARK])}
        except (TypeError, ValueError):
            wanted = set()
        data = obj.animation_data
        if data is not None:
            for curve in _action_curves(data):
                if (curve.data_path, curve.array_index) in wanted:
                    curve.mute = False
        del obj[MARK]
