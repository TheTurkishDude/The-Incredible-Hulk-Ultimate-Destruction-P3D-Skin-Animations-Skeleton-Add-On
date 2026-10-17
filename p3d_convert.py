# -*- coding: utf-8 -*-
"""
p3d_convert.py - game space <-> Blender space maths for Pure3D skeleton animation.
No bpy / mathutils dependency (plain tuples), so it can be unit tested outside Blender.

Conventions
    game space     : Y up, left handed look (character faces -Z, its left side is +X), row-vector matrices
    blender space  : Z up, right handed, character faces -Y
    vector         (x, y, z)_game -> (x, z, y)_blender          (axis swap = mirror)
    quaternion     (x, y, z, w)_game -> (w, -x, -z, -y)_blender (pseudo vector -> sign flip)
Quaternions in this module are (w, x, y, z) tuples unless a name ends in _xyzw.

A joint stores a *local* rest transform  L_rest = [R_r | t_r].  An animation key replaces it by L(t) = [R | t].
Blender's pose bone offset (matrix_basis) is expressed in the bone frame:
        B_joint = L_rest^-1 * L(t)                      (offset in the joint frame)
        B_bone  = C^-1 * B_joint * C                    (C = joint frame -> bone frame, pure rotation)
so a bone can point along the chain (pretty armature) and the animation stays exact.
"""
import math

# ------------------------------------------------------------------ quaternion / vector helpers
IDENT_Q = (1.0, 0.0, 0.0, 0.0)


def qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def qconj(q):
    return (q[0], -q[1], -q[2], -q[3])


def qnorm(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n < 1e-12:
        return IDENT_Q
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def qdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def qrot(q, v):
    """rotate vector v by unit quaternion q"""
    w, x, y, z = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


def qslerp(a, b, t):
    d = qdot(a, b)
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        d = -d
    if d > 0.9995:
        r = tuple(a[i] + (b[i] - a[i]) * t for i in range(4))
        return qnorm(r)
    th = math.acos(min(1.0, d))
    s = math.sin(th)
    wa = math.sin((1.0 - t) * th) / s
    wb = math.sin(t * th) / s
    return tuple(a[i] * wa + b[i] * wb for i in range(4))


def qangle(a, b):
    d = min(1.0, abs(qdot(a, b)))
    return 2.0 * math.acos(d)


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vlen(a):
    return math.sqrt(vdot(a, a))


def vnorm(a):
    n = vlen(a)
    if n < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def vlerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def mat3_to_quat(m):
    """m = ((r00,r01,r02),(r10,r11,r12),(r20,r21,r22)) column-vector rotation matrix -> (w,x,y,z)"""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        q = (0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        q = ((m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        q = ((m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        q = ((m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s)
    return qnorm(q)


def quat_to_mat3(q):
    """(w,x,y,z) -> rows of the column-vector rotation matrix"""
    w, x, y, z = qnorm(q)
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def quat_from_axes(xa, ya, za):
    """quaternion whose rotation matrix has the given orthonormal columns"""
    m = ((xa[0], ya[0], za[0]), (xa[1], ya[1], za[1]), (xa[2], ya[2], za[2]))
    return mat3_to_quat(m)


# ------------------------------------------------------------------ space conversion
def g2b_vec(v):
    return (v[0], v[2], v[1])


def g2b_quat(q_xyzw):
    x, y, z, w = q_xyzw
    return (w, -x, -z, -y)


def b2g_quat(q):
    w, x, y, z = q
    return (-x, -z, -y, w)          # returned as (x, y, z, w)


# ------------------------------------------------------------------ skeleton rest data
class Rest(object):
    """joint rest data in *blender space* (translation already scaled)"""
    __slots__ = ('name', 'parent', 'q', 't', 'wq', 'wt')

    def __init__(self, name, parent, q, t):
        self.name, self.parent, self.q, self.t = name, parent, q, t
        self.wq, self.wt = q, t


def joint_local_rest(rest16, scale=1.0):
    """row-major (row vector) 4x4 from the file -> (q, t) in blender space"""
    r = rest16
    # column-vector matrix = transpose of the stored 3x3
    m = ((r[0], r[4], r[8]), (r[1], r[5], r[9]), (r[2], r[6], r[10]))
    q_g = mat3_to_quat(m)                      # (w,x,y,z) in game space
    q_b = g2b_quat((q_g[1], q_g[2], q_g[3], q_g[0]))
    t_b = vscale(g2b_vec((r[12], r[13], r[14])), scale)
    return q_b, t_b


def build_rest(joints, scale=1.0):
    """joints: iterable of objects with name/parent/rest (p3d_core.Joint)  ->  [Rest] with world transforms"""
    out = []
    for i, j in enumerate(joints):
        q, t = joint_local_rest(j.rest, scale)
        r = Rest(j.name, j.parent if i > 0 and j.parent != i else -1, q, t)
        if r.parent >= 0 and r.parent < i:
            p = out[r.parent]
            r.wq = qmul(p.wq, q)
            r.wt = vadd(p.wt, qrot(p.wq, t))
        out.append(r)
    return out


def plan_bones(rest, mode='CHAIN', default_len=0.1):
    """Decide head / bone frame / length for each joint.

    returns list of dicts: head (xyz), q (bone frame, wxyz), length, c (joint->bone rotation, wxyz)
    mode 'JOINT' : bone axes == joint axes (Y axis is the bone direction)
    mode 'CHAIN' : bone Y points to the (farthest) child, roll taken from the joint frame
    """
    n = len(rest)
    kids = [[] for _ in range(n)]
    for i, r in enumerate(rest):
        if r.parent >= 0:
            kids[r.parent].append(i)
    plans = []
    for i, r in enumerate(rest):
        head = r.wt
        length = 0.0
        ydir = None
        if kids[i]:
            best = max(kids[i], key=lambda k: vlen(vsub(rest[k].wt, head)))
            d = vsub(rest[best].wt, head)
            length = vlen(d)
            if length > 1e-5:
                ydir = vscale(d, 1.0 / length)
        if ydir is None:
            # leaf (or coincident child): continue the parent direction
            if r.parent >= 0:
                pd = vsub(head, rest[r.parent].wt)
                if vlen(pd) > 1e-5:
                    ydir = vnorm(pd)
            if ydir is None:
                ydir = qrot(r.wq, (0.0, 1.0, 0.0))
            if r.parent >= 0:
                plen = vlen(vsub(head, rest[r.parent].wt))
                length = max(0.02, min(default_len, plen * 0.4)) if plen > 1e-5 else default_len
            else:
                length = default_len
        if length < 0.02:
            length = max(length, 0.02)
        if mode == 'JOINT':
            qb = r.wq
            ydir_j = qrot(qb, (0.0, 1.0, 0.0))
            ydir = ydir_j
        else:
            # roll: pick the joint axis most perpendicular to the bone direction
            axes = [qrot(r.wq, (1.0, 0.0, 0.0)), qrot(r.wq, (0.0, 1.0, 0.0)), qrot(r.wq, (0.0, 0.0, 1.0))]
            best_a = min(axes, key=lambda a: abs(vdot(a, ydir)))
            za = vsub(best_a, vscale(ydir, vdot(best_a, ydir)))
            za = vnorm(za)
            if vlen(za) < 1e-6:
                za = (0.0, 0.0, 1.0) if abs(ydir[2]) < 0.9 else (1.0, 0.0, 0.0)
                za = vnorm(vsub(za, vscale(ydir, vdot(za, ydir))))
            xa = vcross(ydir, za)
            qb = quat_from_axes(xa, ydir, za)
        c = qmul(qconj(r.wq), qb)
        plans.append({'head': head, 'q': qb, 'length': length, 'c': qnorm(c)})
    return plans


# ------------------------------------------------------------------ animation  game -> blender pose
def _hemisphere(quats):
    out = []
    prev = None
    for q in quats:
        if prev is not None and qdot(prev, q) < 0.0:
            q = (-q[0], -q[1], -q[2], -q[3])
        out.append(q)
        prev = q
    return out


def rot_track_game_to_pose(quats_xyzw, rest_q, c_q):
    """list of (x,y,z,w) game quaternions -> list of pose bone rotation_quaternion (w,x,y,z)"""
    rinv = qconj(rest_q)
    cinv = qconj(c_q)
    out = []
    for q in quats_xyzw:
        qb = g2b_quat(q)
        bj = qmul(rinv, qb)
        bb = qmul(qmul(cinv, bj), c_q)
        out.append(qnorm(bb))
    return _hemisphere(out)


def loc_track_game_to_pose(vecs, rest_q, rest_t, c_q, scale=1.0):
    rinv = qconj(rest_q)
    cinv = qconj(c_q)
    out = []
    for v in vecs:
        tb = vscale(g2b_vec(v), scale)
        bj = qrot(rinv, vsub(tb, rest_t))
        out.append(qrot(cinv, bj))
    return out


# ------------------------------------------------------------------ animation  blender pose -> game
def rot_pose_to_game(quats_wxyz, rest_q, c_q):
    """pose bone quaternions -> list of (x,y,z,w) game quaternions (not yet quantised)"""
    cinv = qconj(c_q)
    out = []
    for q in quats_wxyz:
        bj = qmul(qmul(c_q, qnorm(q)), cinv)
        qb = qmul(rest_q, bj)
        out.append(b2g_quat(qnorm(qb)))
    return out


def loc_pose_to_game(vecs, rest_q, rest_t, c_q, scale=1.0):
    inv = 1.0 / scale if scale else 1.0
    out = []
    for v in vecs:
        bj = qrot(c_q, v)
        tb = vadd(rest_t, qrot(rest_q, bj))
        out.append(g2b_vec(vscale(tb, inv)))
    return out


# ------------------------------------------------------------------ resampling / reduction
def resample_quats(frames, quats, targets):
    """slerp resample (quats in any single convention); frames ascending"""
    out = []
    n = len(frames)
    j = 0
    for f in targets:
        if f <= frames[0]:
            out.append(quats[0]); continue
        if f >= frames[-1]:
            out.append(quats[-1]); continue
        while j + 1 < n - 1 and frames[j + 1] <= f:
            j += 1
        while j > 0 and frames[j] > f:
            j -= 1
        f0, f1 = frames[j], frames[j + 1]
        t = (f - f0) / float(f1 - f0) if f1 != f0 else 0.0
        out.append(qslerp(quats[j], quats[j + 1], t))
    return out


def resample_vecs(frames, vecs, targets):
    out = []
    n = len(frames)
    j = 0
    for f in targets:
        if f <= frames[0]:
            out.append(vecs[0]); continue
        if f >= frames[-1]:
            out.append(vecs[-1]); continue
        while j + 1 < n - 1 and frames[j + 1] <= f:
            j += 1
        while j > 0 and frames[j] > f:
            j -= 1
        f0, f1 = frames[j], frames[j + 1]
        t = (f - f0) / float(f1 - f0) if f1 != f0 else 0.0
        out.append(vlerp(vecs[j], vecs[j + 1], t))
    return out


def reduce_keys(frames, values, tol, is_quat):
    """Ramer-Douglas-Peucker style key reduction.  returns (frames, values) subset (first/last kept)."""
    n = len(frames)
    if n <= 2 or tol <= 0.0:
        return list(frames), list(values)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        worst, wi = -1.0, -1
        fa, fb = frames[a], frames[b]
        span = float(fb - fa) if fb != fa else 1.0
        for i in range(a + 1, b):
            t = (frames[i] - fa) / span
            if is_quat:
                e = qangle(qslerp(values[a], values[b], t), values[i])
            else:
                e = vlen(vsub(vlerp(values[a], values[b], t), values[i]))
            if e > worst:
                worst, wi = e, i
        if worst > tol:
            keep[wi] = True
            stack.append((a, wi))
            stack.append((wi, b))
    idx = [i for i in range(n) if keep[i]]
    return [frames[i] for i in idx], [values[i] for i in idx]


def is_constant(values, tol, is_quat):
    v0 = values[0]
    for v in values[1:]:
        if is_quat:
            if qangle(v0, v) > tol:
                return False
        elif vlen(vsub(v0, v)) > tol:
            return False
    return True


# ------------------------------------------------------------------ skeleton export (blender -> file)
def rest16_from_blender(q, t, scale=1.0):
    """joint local rest (blender space quaternion (w,x,y,z) + translation) -> 16 floats, row-major / row vector layout"""
    qg = b2g_quat(qnorm(q))                       # (x,y,z,w) game
    rc = quat_to_mat3((qg[3], qg[0], qg[1], qg[2]))   # column-vector rotation
    inv = 1.0 / scale if scale else 1.0
    tg = g2b_vec(vscale(t, inv))
    return (rc[0][0], rc[1][0], rc[2][0], 0.0,
            rc[0][1], rc[1][1], rc[2][1], 0.0,
            rc[0][2], rc[1][2], rc[2][2], 0.0,
            tg[0], tg[1], tg[2], 1.0)


def local_from_world(world_q, world_t, parent_q, parent_t):
    if parent_q is None:
        return world_q, world_t
    pinv = qconj(parent_q)
    return qmul(pinv, world_q), qrot(pinv, vsub(world_t, parent_t))
