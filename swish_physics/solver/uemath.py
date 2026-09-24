"""Unreal Engine's math helpers, as Kawaii Physics calls them, over rows of vectors.

Kawaii mixes precisions: positions are doubles, settings and times are floats,
and several intermediate results are narrowed to float on assignment. These
helpers reproduce each helper's arithmetic in the same order and precision
(Engine/Source/Runtime/Core, UE 5.8), so results match Kawaii to the bit where
the platform's libm agrees. Vectors are float64 arrays of shape (n, 3),
quaternions (n, 4) in Unreal's (x, y, z, w) order.
"""
import math

import numpy as np

F32 = np.float32
F64 = np.float64
UE_PI = F32(3.1415926535897932)
KINDA_SMALL = F32(1.0e-4)
SMALL = F32(1.0e-8)
SMALL_D = F64(SMALL)                  # GetSafeNormal's default tolerance, as a double
RAD_TO_DEG = F32(180.0) / UE_PI       # RadiansToDegrees: RadVal * (180.f / UE_PI)
DEG_TO_RAD = F64(math.pi / 180.0)     # DegreesToRadians(double): DegVal * (UE_DOUBLE_PI / 180.0)
SLERP_LINEAR = F64(F32(0.9999))       # T(0.9999f) in Slerp_NotNormalized


def size_squared(v):
    return v[:, 0] * v[:, 0] + v[:, 1] * v[:, 1] + v[:, 2] * v[:, 2]


def size(v):
    return np.sqrt(size_squared(v))


def dot(a, b):
    return a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1] + a[:, 2] * b[:, 2]


def cross(a, b):
    return np.stack([a[:, 1] * b[:, 2] - a[:, 2] * b[:, 1],
                     a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2],
                     a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]], axis=1)


def safe_normal(v):
    """TVector::GetSafeNormal(UE_SMALL_NUMBER, ZeroVector)."""
    square_sum = size_squared(v)
    out = np.zeros_like(v)
    unit = square_sum == 1.0
    scale_rows = ~unit & ~(square_sum < SMALL_D)
    out[unit] = v[unit]
    scale = 1.0 / np.sqrt(square_sum[scale_rows])
    out[scale_rows] = v[scale_rows] * scale[:, None]
    return out


def nearly_zero(v):
    """TVector::IsNearlyZero(UE_KINDA_SMALL_NUMBER)."""
    tolerance = F64(KINDA_SMALL)
    return (np.abs(v[:, 0]) <= tolerance) & (np.abs(v[:, 1]) <= tolerance) & (np.abs(v[:, 2]) <= tolerance)


def rotate_vector(q, v):
    """TQuat::RotateVector: V + W * (2 (Q x V)) + Q x (2 (Q x V))."""
    axis = q[:, :3]
    tt = 2.0 * cross(axis, v)
    return v + q[:, 3:4] * tt + cross(axis, tt)


def unrotate_vector(q, v):
    """TQuat::UnrotateVector: the same with the axis negated."""
    axis = -q[:, :3]
    tt = 2.0 * cross(axis, v)
    return v + q[:, 3:4] * tt + cross(axis, tt)


def axis(q, index):
    """TQuat::GetAxisX / Y / Z: the rotated unit axis."""
    unit = np.zeros((len(q), 3))
    unit[:, index] = 1.0
    return rotate_vector(q, unit)


def quat_normalized(q):
    """TQuat::GetNormalized(UE_SMALL_NUMBER): identity below the tolerance."""
    square_sum = q[:, 0] * q[:, 0] + q[:, 1] * q[:, 1] + q[:, 2] * q[:, 2] + q[:, 3] * q[:, 3]
    out = np.zeros_like(q)
    out[:, 3] = 1.0
    ok = square_sum >= SMALL_D
    out[ok] = q[ok] * (1.0 / np.sqrt(square_sum[ok]))[:, None]
    return out


def slerp(q1, q2, alpha):
    """TQuat::Slerp: Slerp_NotNormalized, then GetNormalized. Alpha is a float."""
    alpha = F64(alpha)
    raw = q1[:, 0] * q2[:, 0] + q1[:, 1] * q2[:, 1] + q1[:, 2] * q2[:, 2] + q1[:, 3] * q2[:, 3]
    cosom = np.where(raw >= 0.0, raw, -raw)
    scale0 = np.full(len(q1), 1.0 - alpha)
    scale1 = np.full(len(q1), alpha)
    arc = cosom < SLERP_LINEAR
    for i in np.flatnonzero(arc):
        omega = math.acos(cosom[i])
        inv_sin = 1.0 / math.sin(omega)
        scale0[i] = math.sin((1.0 - alpha) * omega) * inv_sin
        scale1[i] = math.sin(alpha * omega) * inv_sin
    scale1 = np.where(raw >= 0.0, scale1, -scale1)
    return quat_normalized(scale0[:, None] * q1 + scale1[:, None] * q2)


def lerp(a, b, alpha):
    """FMath::Lerp(A, B, Alpha): A + Alpha * (B - A)."""
    return a + (b - a) * np.asarray(alpha, dtype=F64).reshape(-1, 1)


def quat_multiply(a, b):
    """TQuat operator*: A * B, B applied first."""
    ax, ay, az, aw = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    bx, by, bz, bw = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    return np.stack([aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw,
                     aw * bw - ax * bx - ay * by - az * bz], axis=1)


def quat_from_axis_angle(axis_vector, angle_rad):
    """TQuat(Axis, AngleRad): half = 0.5f * AngleRad, exact sin and cos for doubles."""
    half = F64(F32(0.5)) * F64(angle_rad)
    s, c = math.sin(half), math.cos(half)
    return np.array([s * axis_vector[0], s * axis_vector[1], s * axis_vector[2], c])


def find_between(a, b):
    """TQuat::FindBetweenVectors: the rotation taking A's direction to B's."""
    norm_ab = np.sqrt(size_squared(a) * size_squared(b))
    w = norm_ab + dot(a, b)
    out = np.empty((len(a), 4))
    ok = w >= F64(F32(1.0e-6)) * norm_ab
    c = cross(a, b)
    out[ok, :3] = c[ok]
    out[ok, 3] = w[ok]
    opposite = ~ok
    x_bigger = np.abs(a[:, 0]) > np.abs(a[:, 1])
    first = opposite & x_bigger
    second = opposite & ~x_bigger
    out[first] = np.stack([-a[first, 2], np.zeros(first.sum()), a[first, 0], np.zeros(first.sum())], axis=1)
    out[second] = np.stack([np.zeros(second.sum()), -a[second, 2], a[second, 1], np.zeros(second.sum())], axis=1)
    return quat_normalized(out)


def atan2(y, x):
    """FGenericPlatformMath::Atan2(double, double): libm atan2, 0 for (0, 0)."""
    return np.array([0.0 if (xi == 0.0 and yi == 0.0) else math.atan2(yi, xi) for yi, xi in zip(y, x)])


def rotate_angle_axis(v, angle_deg, axis_vector):
    """TVector::RotateAngleAxis(AngleDeg, Axis) for doubles: exact sin and cos."""
    rad = np.asarray(angle_deg, dtype=F64) * DEG_TO_RAD
    s = np.array([math.sin(r) for r in rad])
    c = np.array([math.cos(r) for r in rad])
    ax, ay, az = axis_vector[:, 0], axis_vector[:, 1], axis_vector[:, 2]
    xx, yy, zz = ax * ax, ay * ay, az * az
    xy, yz, zx = ax * ay, ay * az, az * ax
    xs, ys, zs = ax * s, ay * s, az * s
    omc = 1.0 - c
    x, y, z = v[:, 0], v[:, 1], v[:, 2]
    return np.stack([(omc * xx + c) * x + (omc * xy - zs) * y + (omc * zx + ys) * z,
                     (omc * xy + zs) * x + (omc * yy + c) * y + (omc * yz - xs) * z,
                     (omc * zx - ys) * x + (omc * yz + xs) * y + (omc * zz + c) * z], axis=1)
