/* One Kawaii Physics substep over every chain point in a scene, in C.
 *
 * Same order and formulas as kstep_numpy.substep, which follows
 * AnimNode_KawaiiPhysicsSimulation.cpp SimulateOnce: roots follow the pose;
 * every other point (parents before children): Verlet velocity, damping,
 * gravity, integrate, follow the component's movement, pull toward the pose;
 * XPBD links; collisions; XPBD links again; angle limit and length restore.
 * Points are sorted so that every parent comes before its children.
 */
#include <math.h>
#include <string.h>

#define EXPORT __declspec(dllexport)

static double dot3(const double *a, const double *b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

static void links(int count, const int *first, const int *second, const double *rest, double *lambda,
                  double compliance, double *loc)
{
    for (int k = 0; k < count; ++k) {
        double *a = loc + 3 * first[k], *b = loc + 3 * second[k];
        double d[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]};
        double length = sqrt(dot3(d, d));
        if (length <= 0.0)
            continue;
        double step = (length - rest[k] - compliance * lambda[k]) / (2.0 + compliance);
        double scale = step / length;
        for (int j = 0; j < 3; ++j) {
            a[j] += d[j] * scale;
            b[j] -= d[j] * scale;
        }
        lambda[k] += step;
    }
}

EXPORT void kstep(int n, const int *parent, double *loc, double *prev, const double *pose,
                  const double *damping, const double *pull, const double *radius, const double *limit,
                  const double *gravity, double dt, double dt_old,
                  const double *move, const double *move_rot, const double *world_loc, const double *world_rot,
                  int link_count, const int *link_first, const int *link_second, const double *link_rest,
                  double *lambda, double compliance, int iterations_before, int iterations_after,
                  const int *group, const int *shape_start, const int *shape_count,
                  const double *shape_a, const double *shape_b, const double *shape_radius)
{
    for (int i = 0; i < n; ++i) {
        if (parent[i] < 0) {
            memcpy(prev + 3 * i, loc + 3 * i, 3 * sizeof(double));
            memcpy(loc + 3 * i, pose + 3 * i, 3 * sizeof(double));
        }
    }
    for (int i = 0; i < n; ++i) {
        int p = parent[i];
        if (p < 0)
            continue;
        double *x = loc + 3 * i, *old = prev + 3 * i;
        for (int j = 0; j < 3; ++j) {
            double v = (x[j] - old[j]) / dt_old;
            old[j] = x[j];
            v *= 1.0 - damping[i];
            v += gravity[j] * dt;
            x[j] += v * dt;
        }
        for (int j = 0; j < 3; ++j) {
            double turned = move_rot[3 * j] * old[0] + move_rot[3 * j + 1] * old[1] + move_rot[3 * j + 2] * old[2];
            x[j] += move[j] * (1.0 - world_loc[i]);
            x[j] += (turned - old[j]) * (1.0 - world_rot[i]);
        }
        const double *px = loc + 3 * p;
        for (int j = 0; j < 3; ++j) {
            double base = px[j] + (pose[3 * i + j] - pose[3 * p + j]);
            x[j] += (base - x[j]) * pull[i];
        }
    }
    memset(lambda, 0, link_count * sizeof(double));
    for (int it = 0; it < iterations_before; ++it)
        links(link_count, link_first, link_second, link_rest, lambda, compliance, loc);
    for (int i = 0; i < n; ++i) {
        if (parent[i] < 0)
            continue;
        double *x = loc + 3 * i;
        int g = group[i];
        for (int k = shape_start[g]; k < shape_start[g] + shape_count[g]; ++k) {
            const double *a = shape_a + 3 * k, *b = shape_b + 3 * k;
            double ab[3] = {b[0] - a[0], b[1] - a[1], b[2] - a[2]};
            double ap[3] = {x[0] - a[0], x[1] - a[1], x[2] - a[2]};
            double abab = dot3(ab, ab);
            double t = abab > 1e-12 ? dot3(ap, ab) / abab : 0.0;
            t = t < 0.0 ? 0.0 : (t > 1.0 ? 1.0 : t);
            double c[3], d[3];
            for (int j = 0; j < 3; ++j) {
                c[j] = a[j] + t * ab[j];
                d[j] = x[j] - c[j];
            }
            double dist = sqrt(dot3(d, d));
            double reach = shape_radius[k] + radius[i];
            if (dist < reach && dist > 1e-9) {
                for (int j = 0; j < 3; ++j)
                    x[j] = c[j] + d[j] / dist * reach;
            }
        }
    }
    memset(lambda, 0, link_count * sizeof(double));
    for (int it = 0; it < iterations_after; ++it)
        links(link_count, link_first, link_second, link_rest, lambda, compliance, loc);
    for (int i = 0; i < n; ++i) {
        int p = parent[i];
        if (p < 0)
            continue;
        double *x = loc + 3 * i;
        const double *px = loc + 3 * p;
        double bone[3] = {x[0] - px[0], x[1] - px[1], x[2] - px[2]};
        double length = sqrt(dot3(bone, bone));
        double inv = 1.0 / (length > 1e-12 ? length : 1e-12);
        double dir[3] = {bone[0] * inv, bone[1] * inv, bone[2] * inv};
        double posed[3] = {pose[3 * i] - pose[3 * p], pose[3 * i + 1] - pose[3 * p + 1],
                           pose[3 * i + 2] - pose[3 * p + 2]};
        double posed_length = sqrt(dot3(posed, posed));
        double pinv = 1.0 / (posed_length > 1e-12 ? posed_length : 1e-12);
        double pdir[3] = {posed[0] * pinv, posed[1] * pinv, posed[2] * pinv};
        if (limit[i] > 0.0) {
            double c = dot3(dir, pdir);
            c = c > 1.0 ? 1.0 : (c < -1.0 ? -1.0 : c);
            if (acos(c) > limit[i]) {
                double o[3] = {dir[0] - pdir[0] * c, dir[1] - pdir[1] * c, dir[2] - pdir[2] * c};
                double ol = sqrt(dot3(o, o));
                ol = ol > 1e-12 ? ol : 1e-12;
                double cl = cos(limit[i]), sl = sin(limit[i]);
                for (int j = 0; j < 3; ++j)
                    dir[j] = pdir[j] * cl + o[j] / ol * sl;
            }
        }
        for (int j = 0; j < 3; ++j)
            x[j] = px[j] + dir[j] * posed_length;
    }
}
