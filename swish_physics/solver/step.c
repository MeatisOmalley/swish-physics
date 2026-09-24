/* One Kawaii Physics substep, in C: the fast path of Swish Physics.
 *
 * A port of SimulateOnce (AnimNode_KawaiiPhysicsSimulation.cpp:799) from
 * Kawaii Physics by pafuhana1213, MIT licence, commit 64cbc77. It does the
 * same work as step_numpy.py, point by point in the same order and in the same
 * precisions -- positions in double, settings and times in float, float where
 * Kawaii narrows a result to float -- so the two agree to the bit, and both
 * reproduce Kawaii's golden positions. Points arrive sorted parents first.
 *
 * Build: cl /O2 /fp:precise /LD step.c   (no FMA contraction; see tools/release.py)
 */
#include <math.h>
#include <string.h>

#define EXPORT __declspec(dllexport)
#define SWISH_VERSION 2

#define KIND_BRIDGE 3
#define KIND_INTER 2
#define SPHERE_OUTER 0
#define SPHERE_INNER 1
#define CAPSULE 2
#define TAPERED 3
#define BOX 4
#define PLANE 5

static const float KINDA_SMALL = 1.0e-4f;
static const double SMALL_D = (double)1.0e-8f;
static const float UE_PI_F = 3.1415926535897932f;

typedef struct SwishSystem {
    int n, n_groups, n_links, n_shapes;
    /* points */
    const int *parent, *group, *real_parent, *real_child;
    const signed char *kind;
    const float *alpha, *damping, *world_loc, *world_rot, *radius, *limit_angle, *pull;
    double *loc, *prev;
    const double *pose, *pose_rot;
    double *bridge_base, *push_sum;
    float *push_weight;
    /* groups */
    const double *gravity, *move, *move_rot;
    const signed char *teleport, *legacy_gravity, *planar_axis, *collision_only;
    const int *iterations_before, *iterations_after, *shape_start, *shape_count;
    const float *feedback_scale;
    /* links, in solve order */
    const int *link_a, *link_b, *link_group;
    const float *link_length, *link_compliance;
    float *lambda;
    /* shapes, per group in Kawaii's order */
    const signed char *shape_type, *shape_fallback;
    const double *shape_loc, *shape_rot, *shape_start_point, *shape_end_point, *shape_segment, *shape_segment_sq,
        *shape_fallback_dir, *shape_normal, *shape_plane_w, *shape_extent, *shape_fallback_center;
    const float *shape_radius0, *shape_radius1, *shape_fallback_radius;
    /* this frame's forces: scene wind per point, simple force per group, and per force
       slot a vector and a mask per point (velocity slots: ApplyToVelocity; position: Apply) */
    const double *wind_vel, *simple_force, *vforce, *pforce;
    const signed char *simple_on, *vmask, *pmask;
    int n_vforce, n_pforce;
    /* this substep */
    float step_dt, dt_old;
} SwishSystem;

/* --- Unreal's vector helpers (Engine/Source/Runtime/Core, UE 5.8) --------------------- */

static double size_sq(const double *v) { return v[0] * v[0] + v[1] * v[1] + v[2] * v[2]; }
static double dot3(const double *a, const double *b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

static void cross3(const double *a, const double *b, double *out)
{
    double x = a[1] * b[2] - a[2] * b[1];
    double y = a[2] * b[0] - a[0] * b[2];
    double z = a[0] * b[1] - a[1] * b[0];
    out[0] = x; out[1] = y; out[2] = z;
}

/* TVector::GetSafeNormal(UE_SMALL_NUMBER, ZeroVector) */
static void safe_normal(const double *v, double *out)
{
    double square_sum = size_sq(v);
    if (square_sum == 1.0) { out[0] = v[0]; out[1] = v[1]; out[2] = v[2]; return; }
    if (square_sum < SMALL_D) { out[0] = out[1] = out[2] = 0.0; return; }
    double scale = 1.0 / sqrt(square_sum);
    out[0] = v[0] * scale; out[1] = v[1] * scale; out[2] = v[2] * scale;
}

/* TVector::IsNearlyZero(UE_KINDA_SMALL_NUMBER) */
static int nearly_zero(const double *v)
{
    double t = (double)KINDA_SMALL;
    return fabs(v[0]) <= t && fabs(v[1]) <= t && fabs(v[2]) <= t;
}

/* TQuat::RotateVector: V + W * TT + Q x TT, TT = 2 (Q x V). sign -1 unrotates. */
static void rotate(const double *q, const double *v, double *out, double sign)
{
    double axis[3] = {sign * q[0], sign * q[1], sign * q[2]};
    double c[3], tt[3], c2[3];
    cross3(axis, v, c);
    tt[0] = 2.0 * c[0]; tt[1] = 2.0 * c[1]; tt[2] = 2.0 * c[2];
    cross3(axis, tt, c2);
    for (int j = 0; j < 3; ++j)
        out[j] = v[j] + q[3] * tt[j] + c2[j];
}

static void quat_axis(const double *q, int index, double *out)
{
    double unit[3] = {0.0, 0.0, 0.0};
    unit[index] = 1.0;
    rotate(q, unit, out, 1.0);
}

/* FMath::Atan2(double, double) */
static double ue_atan2(double y, double x) { return (x == 0.0 && y == 0.0) ? 0.0 : atan2(y, x); }

/* TVector::RotateAngleAxis(AngleDeg, Axis) for doubles */
static void rotate_angle_axis(const double *v, double angle_deg, const double *a, double *out)
{
    double rad = angle_deg * (3.14159265358979323846 / 180.0);
    double s = sin(rad), c = cos(rad);
    double xx = a[0] * a[0], yy = a[1] * a[1], zz = a[2] * a[2];
    double xy = a[0] * a[1], yz = a[1] * a[2], zx = a[2] * a[0];
    double xs = a[0] * s, ys = a[1] * s, zs = a[2] * s;
    double omc = 1.0 - c;
    double x = v[0], y = v[1], z = v[2];
    out[0] = (omc * xx + c) * x + (omc * xy - zs) * y + (omc * zx + ys) * z;
    out[1] = (omc * xy + zs) * x + (omc * yy + c) * y + (omc * yz - xs) * z;
    out[2] = (omc * zx - ys) * x + (omc * yz + xs) * y + (omc * zz + c) * z;
}

/* FMath::ClosestPointOnSegment */
static void closest_on_segment(const double *x, const double *start, const double *end, double *out)
{
    double segment[3] = {end[0] - start[0], end[1] - start[1], end[2] - start[2]};
    double to_point[3] = {x[0] - start[0], x[1] - start[1], x[2] - start[2]};
    double dot1 = dot3(to_point, segment);
    if (dot1 <= 0) { memcpy(out, start, 3 * sizeof(double)); return; }
    double dot2 = dot3(segment, segment);
    if (dot2 <= dot1) { memcpy(out, end, 3 * sizeof(double)); return; }
    double ratio = dot1 / dot2;
    for (int j = 0; j < 3; ++j)
        out[j] = start[j] + segment[j] * ratio;
}

static void push_out(double *x, const double *closest, float limit, const double *fallback)
{
    double d[3] = {x[0] - closest[0], x[1] - closest[1], x[2] - closest[2]};
    double dir[3];
    safe_normal(d, dir);
    if (nearly_zero(dir))
        memcpy(dir, fallback, 3 * sizeof(double));
    for (int j = 0; j < 3; ++j)
        x[j] = closest[j] + dir[j] * (double)limit;
}

/* --- the step's parts ------------------------------------------------------------------ */

static void links(SwishSystem *s, const int *iterations)
{
    if (s->n_links == 0)
        return;
    int most = 0;
    for (int k = 0; k < s->n_links; ++k) {
        int count = iterations[s->link_group[k]];
        if (count > 0)
            s->lambda[k] = 0.0f;
        if (count > most)
            most = count;
    }
    float step_dt = s->step_dt > KINDA_SMALL ? s->step_dt : KINDA_SMALL;
    for (int it = 0; it < most; ++it) {
        for (int k = 0; k < s->n_links; ++k) {
            if (it >= iterations[s->link_group[k]])
                continue;
            double *a = s->loc + 3 * s->link_a[k], *b = s->loc + 3 * s->link_b[k];
            double delta[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]};
            float length = (float)sqrt(size_sq(delta));
            if (length <= 0.0f)
                continue;
            float constraint = length - s->link_length[k];
            float compliance = s->link_compliance[k] / (step_dt * step_dt);
            float step = (constraint - compliance * s->lambda[k]) / (2.0f + compliance);
            double r = 1.0 / (double)length;
            for (int j = 0; j < 3; ++j) {
                double d = (delta[j] * r) * (double)step;
                a[j] = a[j] + d;
                b[j] = b[j] - d;
            }
            s->lambda[k] = s->lambda[k] + step;
        }
    }
}

static void collide_sphere(SwishSystem *s, double *x, float radius, int k, int outer)
{
    const double *c = s->shape_loc + 3 * k;
    double delta[3] = {x[0] - c[0], x[1] - c[1], x[2] - c[2]};
    float dist_sq = (float)size_sq(delta);
    if (outer) {
        float limit = s->shape_radius0[k] + radius;
        if (dist_sq > limit * limit)
            return;
        float dist = sqrtf(dist_sq);
        if (dist > KINDA_SMALL) {
            double r = 1.0 / (double)dist;
            double push = (double)(limit - dist);
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] + (delta[j] * r) * push;
        }
    } else {
        float limit = s->shape_radius0[k] - radius;
        if (limit < 0.0f)
            limit = 0.0f;
        if (dist_sq < limit * limit)
            return;
        float dist = sqrtf(dist_sq);
        if (dist > KINDA_SMALL) {
            double r = 1.0 / (double)dist;
            for (int j = 0; j < 3; ++j)
                x[j] = c[j] + (delta[j] * r) * (double)limit;
        } else {
            memcpy(x, c, 3 * sizeof(double));
        }
    }
}

static void collide_capsule(SwishSystem *s, double *x, float radius, int k)
{
    double closest[3];
    closest_on_segment(x, s->shape_start_point + 3 * k, s->shape_end_point + 3 * k, closest);
    double d[3] = {x[0] - closest[0], x[1] - closest[1], x[2] - closest[2]};
    float dist_sq = (float)size_sq(d);
    float limit = radius + s->shape_radius0[k];
    if (dist_sq < limit * limit)
        push_out(x, closest, limit, s->shape_fallback_dir + 3 * k);
}

static void collide_tapered(SwishSystem *s, double *x, float radius, int k)
{
    double closest[3];
    float tapered_radius = s->shape_fallback_radius[k];
    if (s->shape_fallback[k]) {
        memcpy(closest, s->shape_fallback_center + 3 * k, 3 * sizeof(double));
    } else {
        const double *start = s->shape_start_point + 3 * k, *segment = s->shape_segment + 3 * k;
        double rel[3] = {x[0] - start[0], x[1] - start[1], x[2] - start[2]};
        double t = dot3(rel, segment) / s->shape_segment_sq[k];
        t = t < 0.0 ? 0.0 : (t > 1.0 ? 1.0 : t);
        float tf = (float)t;
        for (int j = 0; j < 3; ++j)
            closest[j] = start[j] + segment[j] * (double)tf;
        float r0 = s->shape_radius0[k], r1 = s->shape_radius1[k];
        tapered_radius = r0 + tf * (r1 - r0);
        if (tapered_radius < 0.0f)
            tapered_radius = 0.0f;
    }
    float limit = radius + tapered_radius;
    double d[3] = {x[0] - closest[0], x[1] - closest[1], x[2] - closest[2]};
    float dist_sq = (float)size_sq(d);
    if (dist_sq < limit * limit)
        push_out(x, closest, limit, s->shape_fallback_dir + 3 * k);
}

static void collide_box(SwishSystem *s, double *x, float radius, int k)
{
    const double *rot = s->shape_rot + 4 * k, *centre = s->shape_loc + 3 * k, *extent = s->shape_extent + 3 * k;
    double rel[3] = {x[0] - centre[0], x[1] - centre[1], x[2] - centre[2]};
    double local[3];
    rotate(rot, rel, local, -1.0);
    for (int j = 0; j < 3; ++j)
        local[j] = local[j] * 1.0;
    double r = (double)radius;
    double dist_sq = 0.0;
    for (int j = 0; j < 3; ++j) {
        if (local[j] < -extent[j])
            dist_sq += (local[j] - -extent[j]) * (local[j] - -extent[j]);
        else if (local[j] > extent[j])
            dist_sq += (local[j] - extent[j]) * (local[j] - extent[j]);
    }
    if (!(dist_sq <= r * r))
        return;
    double closest[3], push[3];
    for (int j = 0; j < 3; ++j) {
        closest[j] = local[j] < -extent[j] ? -extent[j] : (local[j] < extent[j] ? local[j] : extent[j]);
        push[j] = local[j] - closest[j];
    }
    float distance = (float)sqrt(size_sq(push));
    int inside = (local[0] >= -extent[0] && local[0] <= extent[0] && local[1] >= -extent[1] &&
                  local[1] <= extent[1] && local[2] >= -extent[2] && local[2] <= extent[2]) ||
                 distance <= KINDA_SMALL;
    double new_local[3] = {local[0], local[1], local[2]};
    if (inside) {
        double pen[3] = {extent[0] - fabs(local[0]), extent[1] - fabs(local[1]), extent[2] - fabs(local[2])};
        int a = (pen[0] <= pen[1] && pen[0] <= pen[2]) ? 0 : (pen[1] <= pen[2] ? 1 : 2);
        double sign = local[a] >= 0.0 ? 1.0 : -1.0;
        new_local[a] = sign * (extent[a] + r);
    } else if (distance <= radius) {
        double dir[3];
        safe_normal(push, dir);
        for (int j = 0; j < 3; ++j)
            new_local[j] = closest[j] + dir[j] * r;
    } else {
        return;
    }
    double scaled[3] = {new_local[0] * 1.0, new_local[1] * 1.0, new_local[2] * 1.0}, turned[3];
    rotate(rot, scaled, turned, 1.0);
    for (int j = 0; j < 3; ++j)
        x[j] = turned[j] + centre[j];
}

static void collide_plane(SwishSystem *s, double *x, const double *previous, float radius, int k)
{
    const double *n = s->shape_normal + 3 * k;
    double w = s->shape_plane_w[k];
    double plane_dot = dot3(n, x) - w;
    double on_plane[3] = {x[0] - n[0] * plane_dot, x[1] - n[1] * plane_dot, x[2] - n[2] * plane_dot};
    double d[3] = {x[0] - on_plane[0], x[1] - on_plane[1], x[2] - on_plane[2]};
    float dist_sq = (float)size_sq(d);
    double seg[3] = {previous[0] - x[0], previous[1] - x[1], previous[2] - x[2]};
    float t = (float)((w - dot3(x, n)) / dot3(seg, n));
    int crosses = t > -KINDA_SMALL && t < 1.0f + KINDA_SMALL;
    if (dist_sq < radius * radius || crosses)
        for (int j = 0; j < 3; ++j)
            x[j] = on_plane[j] + n[j] * (double)radius;
}

static void collide(SwishSystem *s)
{
    for (int i = 0; i < s->n; ++i) {
        if (s->parent[i] < 0 && s->kind[i] != KIND_BRIDGE)
            continue;
        int g = s->group[i];
        double *x = s->loc + 3 * i;
        float radius = s->radius[i];
        for (int k = s->shape_start[g]; k < s->shape_start[g] + s->shape_count[g]; ++k) {
            switch (s->shape_type[k]) {
            case SPHERE_OUTER: collide_sphere(s, x, radius, k, 1); break;
            case SPHERE_INNER: collide_sphere(s, x, radius, k, 0); break;
            case CAPSULE: collide_capsule(s, x, radius, k); break;
            case TAPERED: collide_tapered(s, x, radius, k); break;
            case BOX: collide_box(s, x, radius, k); break;
            case PLANE: collide_plane(s, x, s->prev + 3 * i, radius, k); break;
            }
        }
    }
}

static void bridge_feedback(SwishSystem *s)
{
    int any = 0;
    for (int i = 0; i < s->n && !any; ++i)
        any = s->kind[i] == KIND_BRIDGE && s->feedback_scale[s->group[i]] > 0.0f;
    if (!any)
        return;
    memset(s->push_sum, 0, 3 * s->n * sizeof(double));
    memset(s->push_weight, 0, s->n * sizeof(float));
    for (int i = 0; i < s->n; ++i) {
        if (s->kind[i] != KIND_BRIDGE || !(s->feedback_scale[s->group[i]] > 0.0f))
            continue;
        const double *x = s->loc + 3 * i, *base = s->bridge_base + 3 * i;
        double push[3] = {x[0] - base[0], x[1] - base[1], x[2] - base[2]};
        if (nearly_zero(push))
            continue;
        float w1 = 1.0f - s->alpha[i], w2 = s->alpha[i];
        int e1 = s->real_parent[i], e2 = s->real_child[i];
        for (int j = 0; j < 3; ++j)
            s->push_sum[3 * e1 + j] += push[j] * (double)w1;
        s->push_weight[e1] += w1;
        for (int j = 0; j < 3; ++j)
            s->push_sum[3 * e2 + j] += push[j] * (double)w2;
        s->push_weight[e2] += w2;
    }
    for (int e = 0; e < s->n; ++e) {
        float w = s->push_weight[e];
        if (w > 0.0f) {
            float divisor = w > 1.0f ? w : 1.0f;
            double r = 1.0 / (double)divisor;
            double scale = (double)s->feedback_scale[s->group[e]];
            for (int j = 0; j < 3; ++j)
                s->loc[3 * e + j] = s->loc[3 * e + j] + (s->push_sum[3 * e + j] * r) * scale;
        }
    }
}

static void limits(SwishSystem *s)
{
    for (int i = 0; i < s->n; ++i) {
        int p = s->parent[i];
        if (p < 0 || s->kind[i] == KIND_BRIDGE)
            continue;
        double *x = s->loc + 3 * i;
        const double *px = s->loc + 3 * p;
        const double *pose = s->pose + 3 * i, *ppose = s->pose + 3 * p;
        float limit = s->limit_angle[i];
        if (limit != 0.0f) {
            double bone[3] = {x[0] - px[0], x[1] - px[1], x[2] - px[2]};
            double posed[3] = {pose[0] - ppose[0], pose[1] - ppose[1], pose[2] - ppose[2]};
            double bone_dir[3], pose_dir[3], axis[3];
            safe_normal(bone, bone_dir);
            safe_normal(posed, pose_dir);
            cross3(pose_dir, bone_dir, axis);
            float angle = (float)ue_atan2(sqrt(size_sq(axis)), dot3(pose_dir, bone_dir));
            float over = angle * (180.0f / UE_PI_F) - limit;
            if (over > 0.0f) {
                double rotation_axis[3], turned[3];
                safe_normal(axis, rotation_axis);
                if (nearly_zero(rotation_axis))
                    quat_axis(s->pose_rot + 4 * p, 0, rotation_axis);
                rotate_angle_axis(bone_dir, -(double)over, rotation_axis, turned);
                double length = sqrt(size_sq(bone));
                for (int j = 0; j < 3; ++j)
                    x[j] = turned[j] * length + px[j];
            }
        }
        int planar = s->planar_axis[s->group[i]];
        if (planar != 0) {
            double n[3];
            quat_axis(s->pose_rot + 4 * p, planar - 1, n);
            double w = dot3(px, n);
            double plane_dot = dot3(n, x) - w;
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] - n[j] * plane_dot;
        }
        double posed[3] = {pose[0] - ppose[0], pose[1] - ppose[1], pose[2] - ppose[2]};
        float bone_length = (float)sqrt(size_sq(posed));
        double rel[3] = {x[0] - px[0], x[1] - px[1], x[2] - px[2]}, dir[3];
        safe_normal(rel, dir);
        for (int j = 0; j < 3; ++j)
            x[j] = dir[j] * (double)bone_length + px[j];
    }
}

/* --- entry points ------------------------------------------------------------------------ */

EXPORT int swish_version(void) { return SWISH_VERSION; }

/* 1 - (1 - Stiffness) ^ Exponent per point, as ApplyStiffnessPull computes it. */
EXPORT void swish_pull(int n, const float *stiffness, float exponent, float *out)
{
    for (int i = 0; i < n; ++i)
        out[i] = 1.0f - powf(1.0f - stiffness[i], exponent);
}

EXPORT void swish_simulate_once(SwishSystem *s)
{
    for (int i = 0; i < s->n; ++i) {
        if (s->parent[i] < 0 && s->kind[i] != KIND_BRIDGE) {
            memcpy(s->prev + 3 * i, s->loc + 3 * i, 3 * sizeof(double));
            memcpy(s->loc + 3 * i, s->pose + 3 * i, 3 * sizeof(double));
        }
    }
    float dt_old = s->dt_old > KINDA_SMALL ? s->dt_old : KINDA_SMALL;
    double over_dt_old = 1.0 / (double)dt_old;
    double step_dt = (double)s->step_dt;
    for (int i = 0; i < s->n; ++i) {
        int p = s->parent[i];
        if (p < 0 || s->kind[i] == KIND_BRIDGE)
            continue;
        int g = s->group[i];
        if (s->kind[i] == KIND_INTER && s->collision_only[g])
            continue;
        double *x = s->loc + 3 * i, *old = s->prev + 3 * i;
        double v[3];
        for (int j = 0; j < 3; ++j) {
            v[j] = (x[j] - old[j]) * over_dt_old;
            old[j] = x[j];
        }
        double damp = (double)(1.0f - s->damping[i]);
        const double *gravity = s->gravity + 3 * g;
        for (int j = 0; j < 3; ++j) {
            v[j] = v[j] * damp;
            v[j] = v[j] + s->wind_vel[3 * i + j];
        }
        if (!s->legacy_gravity[g]) {
            for (int j = 0; j < 3; ++j)
                v[j] = v[j] + gravity[j] * step_dt;
        } else {
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] + ((0.5 * gravity[j]) * step_dt) * step_dt;
        }
        for (int k = 0; k < s->n_vforce; ++k) {
            if (!s->vmask[(size_t)k * s->n + i])
                continue;
            const double *f = s->vforce + ((size_t)k * s->n + i) * 3;
            for (int j = 0; j < 3; ++j)
                v[j] = v[j] + f[j] * step_dt;
        }
        for (int j = 0; j < 3; ++j)
            x[j] = x[j] + v[j] * step_dt;
        if (s->simple_on[g]) {
            const double *f = s->simple_force + 3 * g;
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] + f[j] * step_dt;
        }
        if (s->teleport[g] == 0) {
            const double *move = s->move + 3 * g;
            double follow_loc = (double)(1.0f - s->world_loc[i]);
            double follow_rot = (double)(1.0f - s->world_rot[i]);
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] + move[j] * follow_loc;
            double turned[3];
            rotate(s->move_rot + 4 * g, old, turned, 1.0);
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] + (turned[j] - old[j]) * follow_rot;
        }
        for (int k = 0; k < s->n_pforce; ++k) {
            if (!s->pmask[(size_t)k * s->n + i])
                continue;
            const double *f = s->pforce + ((size_t)k * s->n + i) * 3;
            for (int j = 0; j < 3; ++j)
                x[j] = x[j] + f[j] * step_dt;
        }
        const double *px = s->loc + 3 * p, *pose = s->pose + 3 * i, *ppose = s->pose + 3 * p;
        double pull = (double)s->pull[i];
        for (int j = 0; j < 3; ++j) {
            double base = px[j] + (pose[j] - ppose[j]);
            x[j] = x[j] + (base - x[j]) * pull;
        }
    }
    /* Inter-bone dummies first, then bridge dummies, which may sit between them. */
    for (int pass = 0; pass < 2; ++pass) {
        for (int i = 0; i < s->n; ++i) {
            int k = s->kind[i];
            if (pass == 0 ? !(k == KIND_INTER && s->collision_only[s->group[i]]) : k != KIND_BRIDGE)
                continue;
            double *x = s->loc + 3 * i;
            memcpy(s->prev + 3 * i, x, 3 * sizeof(double));
            const double *a = s->loc + 3 * s->real_parent[i], *b = s->loc + 3 * s->real_child[i];
            double alpha = (double)s->alpha[i];
            for (int j = 0; j < 3; ++j)
                x[j] = a[j] + (b[j] - a[j]) * alpha;
            if (k == KIND_BRIDGE)
                memcpy(s->bridge_base + 3 * i, x, 3 * sizeof(double));
        }
    }
    links(s, s->iterations_before);
    collide(s);
    bridge_feedback(s);
    links(s, s->iterations_after);
    limits(s);
}
