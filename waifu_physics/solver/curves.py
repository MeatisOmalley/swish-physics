"""Setting curves as Kawaii evaluates them: FRichCurve with linear keys.

A curve multiplies a group setting along each chain, by the point's length
rate from root (0) to tip (1). Waifu Physics samples each Blender curve into evenly
spaced linear keys, and both the Blender preview and any export use those same
keys, so the game's FRichCurve::Eval (RichCurve.cpp:1163, linear keys via
UE::Curves::EvalForTwoKeys) gives the values Blender simulated with.
"""
import numpy as np

F32 = np.float32
SAMPLES = 65                 # keys at 0, 1/64, ..., 1


class LinearCurve:
    """Linear keys with constant extrapolation; float arithmetic as in FRichCurve."""

    def __init__(self, times, values):
        self.times = np.asarray(times, dtype=F32)
        self.values = np.asarray(values, dtype=F32)

    @classmethod
    def sampled(cls, function, samples=SAMPLES):
        times = np.arange(samples, dtype=F32) / F32(samples - 1)
        return cls(times, [function(float(t)) for t in times])

    def many(self, rates, default=F32(1.0)):
        """FRichCurve::Eval at each rate."""
        rates = np.asarray(rates, dtype=F32)
        n = len(self.times)
        if n == 0:
            return np.full(len(rates), default, dtype=F32)
        if n == 1:
            return np.full(len(rates), self.values[0], dtype=F32)
        # Lower bound of the second key: the first key whose time exceeds the rate, in [1, n - 1].
        second = np.clip(np.searchsorted(self.times, rates, side="right"), 1, n - 1)
        first = second - 1
        diff = self.times[second] - self.times[first]
        alpha = np.where(diff > 0, (rates - self.times[first]) / np.where(diff > 0, diff, F32(1.0)), F32(0.0))
        p0, p3 = self.values[first], self.values[second]
        inside = (p0 + alpha * (p3 - p0)).astype(F32)            # FMath::Lerp(P0, P3, Alpha)
        before = rates < self.times[0]
        after = rates > self.times[-1]
        return np.where(before, self.values[0], np.where(after, self.values[-1], inside)).astype(F32)

    def __call__(self, rate):
        return float(self.many([rate])[0])

    def keys(self):
        """[(time, value)] for an export."""
        return [[time, value] for time, value in zip(self.times.tolist(), self.values.tolist())]
