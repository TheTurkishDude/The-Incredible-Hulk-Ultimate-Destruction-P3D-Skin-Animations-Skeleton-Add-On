# -*- coding: utf-8 -*-
"""blender_io.py - the only module (besides ui.py) that touches bpy.  Works on Blender 2.80 ... 5.x."""
import json
import re
import math

import bpy
from mathutils import Matrix, Vector, Quaternion

from . import p3d_core as C
from . import p3d_convert as V
from . import p3d_skin_export as X

SKEL_PROP = 'p3d_skeleton'
META_PROP = 'p3d_meta'
SCALE_PROP = 'p3d_scale'
_PB_RE = re.compile(r'^pose\.bones\["(.+)"\]\.(\w+)$')


# ------------------------------------------------------------------ compat helpers
def set_active(context, obj):
    for o in list(context.view_layer.objects):
        try:
            o.select_set(False)
        except Exception:
            pass
    obj.select_set(True)
    context.view_layer.objects.active = obj


def object_mode(context):
    try:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def link_object(context, obj):
    coll = context.collection if context.collection else context.scene.collection
    coll.objects.link(obj)


def action_has_slots():
    return hasattr(bpy.types.Action, 'slots')


def action_fcurve_container(action, obj):
    """returns (fcurves collection, slot or None, new_fcurve callable)"""
    if action_has_slots():                      # Blender 4.4+ layered / slotted actions
        slot = action.slots[0] if len(action.slots) else action.slots.new('OBJECT', obj.name)
        layer = action.layers[0] if len(action.layers) else action.layers.new('Layer')
        strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type='KEYFRAME')
        cb = strip.channelbag(slot, ensure=True)

        def new(path, index, group):
            try:
                return cb.fcurves.new(path, index=index, group_name=group)
            except TypeError:
                return cb.fcurves.new(path, index=index)
        return cb.fcurves, slot, new

    def new_legacy(path, index, group):
        return action.fcurves.new(path, index=index, action_group=group)
    return action.fcurves, None, new_legacy


def iter_fcurves(action):
    if action_has_slots():
        for layer in action.layers:
            for strip in layer.strips:
                for cb in getattr(strip, 'channelbags', []):
                    for fc in cb.fcurves:
                        yield fc
        return
    for fc in action.fcurves:
        yield fc


def assign_action(obj, action, slot=None):
    ad = obj.animation_data or obj.animation_data_create()
    ad.action = action
    if slot is not None:
        try:
            ad.action_slot = slot
        except Exception:
            pass
    return ad


def fill_fcurve(fc, frames, values, interp='LINEAR'):
    n = len(frames)
    if n == 0:
        return
    kp = fc.keyframe_points
    kp.add(n)
    co = [0.0] * (2 * n)
    co[0::2] = [float(f) for f in frames]
    co[1::2] = [float(v) for v in values]
    kp.foreach_set('co', co)
    for k in kp:
        k.interpolation = interp
    fc.update()


# ------------------------------------------------------------------ skeleton description stored on the armature
def skeleton_to_json(sk):
    return json.dumps({
        'name': sk.name,
        'joints': [{'n': j.name, 'p': j.parent, 'r': list(j.rest)} for j in sk.joints],
        'masks': [[m[0], m[1]] for m in sk.masks],
        'chains': [[c[0], c[1], c[2]] for c in sk.chains],
    })


class SkelInfo(object):
    """rest data (blender space) + bone frame constants for an armature"""
    def __init__(self):
        self.names = []
        self.index = {}       # bone name -> joint index (group id)
        self.rest = {}        # name -> (q, t)
        self.cq = {}          # name -> joint->bone rotation


def _mat_to_qt(m):
    q = m.to_quaternion()
    t = m.to_translation()
    return (q.w, q.x, q.y, q.z), (t.x, t.y, t.z)


def skel_info(arm_obj):
    """Collect rest/frame data of an armature.  Uses the imported p3d skeleton when present (exact),
    otherwise derives it from the Blender bones (any rig whose bone names match the animation groups)."""
    info = SkelInfo()
    scale = float(arm_obj.get(SCALE_PROP, 1.0)) or 1.0
    bones = arm_obj.data.bones
    stored = arm_obj.get(SKEL_PROP)
    rest = None
    if stored:
        try:
            d = json.loads(stored)

            class J(object):
                pass
            joints = []
            for j in d['joints']:
                o = J()
                o.name, o.parent, o.rest = j['n'], j['p'], j['r']
                joints.append(o)
            rest = V.build_rest(joints, scale)
            for i, r in enumerate(rest):
                info.names.append(r.name)
                info.index[r.name] = i
                info.rest[r.name] = (r.q, r.t)
                b = bones.get(r.name)
                if b is not None:
                    bq = b.matrix_local.to_quaternion()
                    info.cq[r.name] = V.qnorm(V.qmul(V.qconj(r.wq), (bq.w, bq.x, bq.y, bq.z)))
                else:
                    info.cq[r.name] = V.IDENT_Q
        except Exception:
            rest = None
            info = SkelInfo()
    for i, b in enumerate(bones):
        if b.name in info.index:
            continue                      # joint of the stored skeleton
        m = b.matrix_local
        if b.parent is not None:
            m = b.parent.matrix_local.inverted() @ m
        info.names.append(b.name)
        info.index[b.name] = int(b.get('p3d_gid', i)) if hasattr(b, 'get') else i
        info.rest[b.name] = _mat_to_qt(m)
        info.cq[b.name] = V.IDENT_Q       # foreign / extra bone: joint frame == bone frame
    return info


def ensure_extra_bones(context, arm_obj, anims):
    """Create bones for animation groups that have no bone yet (Wrist_Grapple, Root_Grapple, ...).
    Their rest frame is the bone frame, so the pose is exactly the animated transform and exports back unchanged."""
    bones = arm_obj.data.bones
    missing = {}
    for a in anims:
        for g in a.groups:
            if g.name and bones.get(g.name) is None and g.name not in missing:
                missing[g.name] = g.group_id
    if not missing:
        return []
    scale = float(arm_obj.get(SCALE_PROP, 1.0)) or 1.0
    object_mode(context)
    set_active(context, arm_obj)
    bpy.ops.object.mode_set(mode='EDIT')
    for n in missing:
        eb = arm_obj.data.edit_bones.new(n)
        eb.head = Vector((0.0, 0.0, 0.0))
        eb.tail = Vector((0.0, 0.1 * scale, 0.0))
    bpy.ops.object.mode_set(mode='OBJECT')
    for n, gid in missing.items():
        b = arm_obj.data.bones.get(n)
        if b is not None:
            b['p3d_gid'] = int(gid)
            b['p3d_extra'] = 1
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    return list(missing)


# ------------------------------------------------------------------ armature / mesh import
def create_armature(context, sk, name, scale=1.0, mode='CHAIN', display='OCTAHEDRAL'):
    object_mode(context)
    rest = V.build_rest(sk.joints, scale)
    plans = V.plan_bones(rest, mode, default_len=0.1 * max(scale, 1e-6))
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    link_object(context, obj)
    set_active(context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    ebs = []
    for r, p in zip(rest, plans):
        eb = arm.edit_bones.new(r.name)
        h = Vector(p['head'])
        eb.head = h
        eb.tail = h + Vector((0.0, p['length'], 0.0))
        q = p['q']
        m = Quaternion(q).to_matrix().to_4x4()
        m.translation = h
        eb.matrix = m
        ebs.append(eb)
    for i, r in enumerate(rest):
        if r.parent >= 0:
            ebs[i].parent = ebs[r.parent]
            ebs[i].use_connect = False
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        arm.display_type = display
    except Exception:
        pass
    obj.show_in_front = True
    obj[SKEL_PROP] = skeleton_to_json(sk)
    obj[SCALE_PROP] = float(scale)
    obj['p3d_bone_mode'] = mode
    for pb in obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    return obj


def _normals_b(g):
    return [V.g2b_vec(n) for n in g.normals]


def create_skin_mesh(context, skin, arm_obj, sk, scale=1.0, flip_v=True, normals=True, name=None):
    """build one mesh (one material per shader) with vertex groups named like the skeleton joints"""
    object_mode(context)
    verts, faces, face_mat, loop_uv, mats = [], [], [], [], []
    infl = []
    for g in skin.groups:
        if g.shader not in mats:
            mats.append(g.shader)
        base = len(verts)
        pos = [V.vscale(V.g2b_vec(p), scale) for p in g.positions]
        verts.extend(pos)
        tris = g.triangles()
        # decide winding: the axis swap mirrors the model, compare with the file normals
        flip = False
        if g.normals and tris:
            nb = _normals_b(g)
            vote = 0
            for (a, b, c) in tris[:400]:
                pa, pb_, pc = pos[a], pos[b], pos[c]
                fn = V.vcross(V.vsub(pb_, pa), V.vsub(pc, pa))
                vn = V.vadd(V.vadd(nb[a], nb[b]), nb[c])
                vote += 1 if V.vdot(fn, vn) >= 0 else -1
            flip = vote < 0
        uv = g.uvs[0] if g.uvs else None
        for (a, b, c) in tris:
            if flip:
                a, c = c, a
            faces.append((a + base, b + base, c + base))
            face_mat.append(mats.index(g.shader))
            for v in (a, b, c):
                if uv:
                    u, w = uv[v]
                    loop_uv.extend((u, 1.0 - w if flip_v else w))
                else:
                    loop_uv.extend((0.0, 0.0))
        for v in range(len(g.positions)):
            infl.append(g.vertex_influences(v) if (g.weights or g.influences is not None) else [])
    mesh = bpy.data.meshes.new(name or skin.name or 'p3d_skin')
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for sh in mats:
        m = bpy.data.materials.get(sh) or bpy.data.materials.new(sh)
        mesh.materials.append(m)
    if len(mesh.polygons) == len(face_mat):
        mesh.polygons.foreach_set('material_index', face_mat)
        mesh.polygons.foreach_set('use_smooth', [True] * len(face_mat))
    if loop_uv and len(loop_uv) == 2 * len(mesh.loops):
        layer = mesh.uv_layers.new(name='UVMap')
        layer.data.foreach_set('uv', loop_uv)
    obj = bpy.data.objects.new(name or skin.name or 'p3d_skin', mesh)
    link_object(context, obj)
    names = [j.name for j in sk.joints] if hasattr(sk, 'joints') else list(sk)
    vgs = {}
    for v, lst in enumerate(infl):            # vertex groups always carry the joint names
        for j, w in lst:
            if j >= len(names):
                continue
            vg = vgs.get(j)
            if vg is None:
                vg = vgs[j] = obj.vertex_groups.new(name=names[j])
            vg.add([v], w, 'REPLACE')
    if arm_obj is not None:
        obj.parent = arm_obj
        mod = obj.modifiers.new('Armature', 'ARMATURE')
        mod.object = arm_obj
    if normals:
        _try_custom_normals(mesh, [V.g2b_vec(n) for g in skin.groups for n in g.normals])
    mesh.update()
    return obj


def _try_custom_normals(mesh, nrm):
    if len(nrm) != len(mesh.vertices):
        return
    try:
        if hasattr(mesh, 'use_auto_smooth'):      # < 4.1
            mesh.use_auto_smooth = True
        if hasattr(mesh, 'create_normals_split'):
            mesh.create_normals_split()
        mesh.normals_split_custom_set_from_vertices([tuple(n) for n in nrm])
    except Exception:
        pass


# ------------------------------------------------------------------ animation import
def _target_frames(frames, step, last):
    if step <= 0:
        return None
    t = list(range(0, int(last) + 1, step))
    if t[-1] != int(last):
        t.append(int(last))
    return t


def import_animation(context, arm_obj, anim, scale=None, bake_step=0, assign=True,
                     frame_offset=0, set_fake_user=True, set_scene=False):
    """creates an Action from a PTRN style Animation.  returns (action, matched, skipped_group_names)"""
    info = skel_info(arm_obj)
    if scale is None:
        scale = float(arm_obj.get(SCALE_PROP, 1.0)) or 1.0
    action = bpy.data.actions.new(anim.name)
    if set_fake_user:
        action.use_fake_user = True
    _, slot, new_fc = action_fcurve_container(action, arm_obj)
    meta = {'name': anim.name, 'type': anim.anim_type, 'fps': anim.fps, 'frames': anim.num_frames,
            'cyclic': anim.cyclic,
            'regions': [[r.name, [list(x) for x in r.ranges]] for r in anim.regions if r.raw is None],
            'rot': {}, 'tran': {}, 'order': [], 'gid': {}, 'scale': scale}
    last = max(0, int(anim.num_frames) - 1)
    targets = _target_frames(None, bake_step, last)
    matched, skipped = 0, []
    for g in anim.groups:
        pb = arm_obj.pose.bones.get(g.name)
        if pb is None or g.name not in info.rest:
            skipped.append(g.name)
            continue
        matched += 1
        meta['order'].append(g.name)
        meta['gid'][g.name] = g.group_id      # group ids differ per clip for extra groups
        rq, rt = info.rest[g.name]
        cq = info.cq[g.name]
        pb.rotation_mode = 'QUATERNION'
        base = 'pose.bones["%s"].' % g.name
        for ch in g.channels:
            if ch.raw is not None or not ch.frames:
                continue
            if ch.param == 'ROT' and ch.kind in (C.CH_CQ16, C.CH_CQ8, C.CH_QUAT):
                frames = list(ch.frames)
                vals = V.rot_track_game_to_pose(ch.quats(), rq, cq)
                if targets is not None:
                    vals = V.resample_quats(frames, vals, targets)
                    frames = targets
                for i in range(4):
                    fc = new_fc(base + 'rotation_quaternion', i, g.name)
                    fill_fcurve(fc, [f + frame_offset for f in frames], [v[i] for v in vals])
                meta['rot'][g.name] = ch.kind
            elif ch.param == 'TRAN' and ch.kind in (C.CH_V1, C.CH_V2, C.CH_V3):
                frames = list(ch.frames)
                vals = V.loc_track_game_to_pose(ch.vec3s(), rq, rt, cq, scale)
                if targets is not None:
                    vals = V.resample_vecs(frames, vals, targets)
                    frames = targets
                for i in range(3):
                    fc = new_fc(base + 'location', i, g.name)
                    fill_fcurve(fc, [f + frame_offset for f in frames], [v[i] for v in vals])
                meta['tran'][g.name] = {'kind': ch.kind, 'map': ch.mapping, 'const': list(ch.const)}
    action[META_PROP] = json.dumps(meta)
    if assign:
        assign_action(arm_obj, action, slot)
    if set_scene:
        sc = context.scene
        sc.frame_start = frame_offset
        sc.frame_end = frame_offset + last
        fps = int(round(anim.fps)) or 30
        sc.render.fps = fps
        sc.render.fps_base = 1.0
    return action, matched, skipped


# ------------------------------------------------------------------ animation export
def _bone_fcurves(action):
    res = {}
    for fc in iter_fcurves(action):
        m = _PB_RE.match(fc.data_path)
        if not m:
            continue
        res.setdefault(m.group(1), {}).setdefault(m.group(2), {})[fc.array_index] = fc
    return res


def _key_frames(fcs):
    s = set()
    for fc in fcs.values():
        for k in fc.keyframe_points:
            s.add(int(round(k.co[0])))
    return sorted(s)


def action_frame_range(action):
    lo, hi = None, None
    for fc in iter_fcurves(action):
        for k in fc.keyframe_points:
            f = k.co[0]
            lo = f if lo is None else min(lo, f)
            hi = f if hi is None else max(hi, f)
    if lo is None:
        return 0, 0
    return int(round(lo)), int(round(hi))


class ExportOptions(object):
    def __init__(self):
        self.rot_format = 'AUTO'        # AUTO | 16 | 8
        self.bake_all = False           # key every frame
        self.reduce = 0.0               # tolerance in radians / units, 0 = off
        self.start_at_zero = True
        self.all_bones = False          # also write unanimated skeleton bones as 1-key channels
        self.fps = 0.0                  # 0 = use stored / scene
        self.cyclic = None              # None = stored
        self.scale = None
        self.range_mode = 'ACTION'      # ACTION | SCENE
        self.name = None


def export_action(context, arm_obj, action, opts):
    """Action -> p3d_core.Animation"""
    info = skel_info(arm_obj)
    scale = opts.scale if opts.scale else (float(arm_obj.get(SCALE_PROP, 1.0)) or 1.0)
    meta = {}
    try:
        meta = json.loads(action.get(META_PROP, '{}'))
    except Exception:
        meta = {}
    fcs_by_bone = _bone_fcurves(action)
    lo, hi = action_frame_range(action)
    if opts.range_mode == 'SCENE':
        lo, hi = context.scene.frame_start, context.scene.frame_end
    shift = lo if opts.start_at_zero else 0
    nframes = hi - lo + 1 if opts.start_at_zero else hi + 1
    fps = opts.fps or meta.get('fps') or (context.scene.render.fps / (context.scene.render.fps_base or 1.0))
    cyc = opts.cyclic if opts.cyclic is not None else int(meta.get('cyclic', 0))
    name = opts.name or meta.get('name') or action.name
    anim = C.Animation(name, meta.get('type', 'PTRN'), float(max(1, nframes)), float(fps), cyc)
    for rn, ranges in meta.get('regions', []):
        anim.regions.append(C.Region(rn, [tuple(x) for x in ranges]))
    rot_meta = meta.get('rot', {})
    tran_meta = meta.get('tran', {})
    gids = meta.get('gid', {})
    order = [n for n in info.names if n in fcs_by_bone or (opts.all_bones and n in info.rest)]
    order.sort(key=lambda n: int(gids.get(n, info.index[n])))
    for bname in order:
        pb = arm_obj.pose.bones.get(bname)
        if pb is None:
            continue
        rq, rt = info.rest[bname]
        cq = info.cq[bname]
        g = C.Group(bname, int(gids.get(bname, info.index[bname])))
        d = fcs_by_bone.get(bname, {})
        # ---------------- rotation
        rot_fc = d.get('rotation_quaternion') or d.get('rotation_euler')
        if rot_fc or opts.all_bones:
            frames, quats = _sample_rotation(pb, d, lo, hi, opts)
            gq = V.rot_pose_to_game(quats, rq, cq)
            wxyz = [(q[3], q[0], q[1], q[2]) for q in gq]
            wxyz = V._hemisphere(wxyz)
            if len(frames) > 1 and V.is_constant(wxyz, 1e-5, True):
                frames, wxyz = frames[:1], wxyz[:1]
            elif opts.reduce > 0:
                frames, wxyz = V.reduce_keys(frames, wxyz, opts.reduce, True)
            kind = _pick_rot_kind(opts, rot_meta.get(bname))
            ch = C.Channel(kind, 'ROT', [f - shift for f in frames], [], 1)
            for (w, x, y, z) in wxyz:
                if kind == C.CH_QUAT:
                    ch.values.append((x, y, z, w))
                else:
                    ch.values.append(C.quat_quant(x, y, z, w, 16 if kind == C.CH_CQ16 else 8))
            ch.interp = (0, 0xFFFFFFFF)
            g.channels.append(ch)
        # ---------------- translation
        loc_fc = d.get('location')
        if loc_fc:
            frames, locs = _sample_location(pb, d, lo, hi, opts)
            gv = V.loc_pose_to_game(locs, rq, rt, cq, scale)
            if len(frames) > 1 and V.is_constant(gv, 1e-6, False):
                frames, gv = frames[:1], gv[:1]
            elif opts.reduce > 0:
                frames, gv = V.reduce_keys(frames, gv, opts.reduce * 0.1, False)
            ch = _make_tran_channel([f - shift for f in frames], gv, tran_meta.get(bname))
            g.channels.append(ch)
        if g.channels:
            anim.groups.append(g)
    return anim


def _pick_rot_kind(opts, stored):
    if opts.rot_format == '8':
        return C.CH_CQ8
    if opts.rot_format == '16':
        return C.CH_CQ16
    return stored if stored in (C.CH_CQ8, C.CH_CQ16, C.CH_QUAT) else C.CH_CQ16


def _make_tran_channel(frames, vecs, meta):
    kind, mapping, const = C.CH_V3, 0, (0.0, 0.0, 0.0)
    if meta:
        mk = meta.get('kind')
        cst = tuple(meta.get('const', (0, 0, 0)))
        mp = int(meta.get('map', 0))
        if mk == C.CH_V1:
            ax = mp % 3
            if all(abs(v[a] - cst[a]) < 1e-5 for v in vecs for a in range(3) if a != ax):
                kind, mapping, const = C.CH_V1, mp, cst
        elif mk == C.CH_V2:
            ax = C.V2_AXES.get(mp, (1, 2))
            free = [a for a in range(3) if a not in ax]
            if all(abs(v[a] - cst[a]) < 1e-5 for v in vecs for a in free):
                kind, mapping, const = C.CH_V2, mp, cst
    ch = C.Channel(kind, 'TRAN', frames, [], 0)
    ch.mapping, ch.const = mapping, const
    if kind == C.CH_V1:
        ax = mapping % 3
        ch.values = [v[ax] for v in vecs]
    elif kind == C.CH_V2:
        ax = C.V2_AXES.get(mapping, (1, 2))
        ch.values = [(v[ax[0]], v[ax[1]]) for v in vecs]
    else:
        ch.values = [tuple(v) for v in vecs]
    ch.interp = (0, 0xFFFFFFFF)
    return ch


def _frames_for(d, names, lo, hi, opts):
    fcs = {}
    for n in names:
        if n in d:
            for i, fc in d[n].items():
                fcs[(n, i)] = fc
    if opts.bake_all or not fcs:
        return list(range(lo, hi + 1))
    fr = [f for f in _key_frames(fcs) if lo <= f <= hi]
    if not fr:
        fr = [lo]
    return fr


def _eval(d, prop, idx, frame, default):
    fc = d.get(prop, {}).get(idx)
    return fc.evaluate(frame) if fc is not None else default


def _sample_rotation(pb, d, lo, hi, opts):
    frames = _frames_for(d, ('rotation_quaternion', 'rotation_euler'), lo, hi, opts)
    quats = []
    if 'rotation_quaternion' in d:
        for f in frames:
            q = (_eval(d, 'rotation_quaternion', 0, f, 1.0), _eval(d, 'rotation_quaternion', 1, f, 0.0),
                 _eval(d, 'rotation_quaternion', 2, f, 0.0), _eval(d, 'rotation_quaternion', 3, f, 0.0))
            quats.append(V.qnorm(q))
    elif 'rotation_euler' in d:
        mode = pb.rotation_mode if len(pb.rotation_mode) == 3 and pb.rotation_mode != 'QUATERNION' else 'XYZ'
        from mathutils import Euler
        for f in frames:
            e = Euler((_eval(d, 'rotation_euler', 0, f, 0.0), _eval(d, 'rotation_euler', 1, f, 0.0),
                       _eval(d, 'rotation_euler', 2, f, 0.0)), mode)
            q = e.to_quaternion()
            quats.append((q.w, q.x, q.y, q.z))
    else:
        frames = [lo]
        quats = [V.IDENT_Q]
    return frames, quats


def _sample_location(pb, d, lo, hi, opts):
    frames = _frames_for(d, ('location',), lo, hi, opts)
    locs = [(_eval(d, 'location', 0, f, 0.0), _eval(d, 'location', 1, f, 0.0), _eval(d, 'location', 2, f, 0.0))
            for f in frames]
    return frames, locs


def actions_for_armature(arm_obj, only_p3d=False):
    res = []
    for a in bpy.data.actions:
        if only_p3d and META_PROP not in a:
            continue
        names = set()
        for fc in iter_fcurves(a):
            m = _PB_RE.match(fc.data_path)
            if m:
                names.add(m.group(1))
        if names and any(arm_obj.data.bones.get(n) is not None for n in names):
            res.append(a)
    return res


# ------------------------------------------------------------------ skeleton export
class ExportJoint(object):
    __slots__ = ('name', 'bone', 'parent', 'q', 't')


def export_joint_list(arm_obj):
    """joints of the skeleton in file order.  Stored (imported) skeletons keep their original order so palette /
    group indices stay valid; user added bones are appended; 'extra' group bones are not part of the skeleton."""
    bones = arm_obj.data.bones
    stored = None
    try:
        if arm_obj.get(SKEL_PROP):
            stored = json.loads(arm_obj[SKEL_PROP])
    except Exception:
        stored = None
    order = []
    names = set()
    if stored:
        for j in stored['joints']:
            b = bones.get(j['n'])
            if b is not None:
                order.append(b)
                names.add(b.name)
    rest = [b for b in bones if b.name not in names and not b.get('p3d_extra', 0)]
    # parents first
    placed = set(names)
    pending = list(rest)
    guard = 0
    while pending and guard < len(rest) + 2:
        guard += 1
        nxt = []
        for b in pending:
            if b.parent is None or b.parent.name in placed or b.parent.get('p3d_extra', 0):
                order.append(b)
                placed.add(b.name)
            else:
                nxt.append(b)
        pending = nxt
    order.extend(pending)
    return order, stored


def export_skeleton(arm_obj, keep_joint_axes=True):
    """returns (Chunk, [joint names in order])"""
    scale = float(arm_obj.get(SCALE_PROP, 1.0)) or 1.0
    order, stored = export_joint_list(arm_obj)
    idx = dict((b.name, i) for i, b in enumerate(order))
    stored_rest = {}
    if stored and keep_joint_axes:
        class J(object):
            pass
        js = []
        for j in stored['joints']:
            o = J()
            o.name, o.parent, o.rest = j['n'], j['p'], j['r']
            js.append(o)
        for r in V.build_rest(js, scale):
            stored_rest[r.name] = r.wq
    wq, wt, joints = [], [], []
    for i, b in enumerate(order):
        m = b.matrix_local
        q = m.to_quaternion()
        bq = (q.w, q.x, q.y, q.z)
        head = b.head_local
        wt_i = (head.x, head.y, head.z)
        wq_i = stored_rest.get(b.name, bq)
        # parent: nearest ancestor that is part of the skeleton
        p = b.parent
        while p is not None and p.name not in idx:
            p = p.parent
        pi = idx[p.name] if p is not None else -1
        wq.append(wq_i)
        wt.append(wt_i)
        lq, lt = V.local_from_world(wq_i, wt_i, wq[pi] if pi >= 0 else None, wt[pi] if pi >= 0 else None)
        joints.append((b.name, pi if pi >= 0 else 0, V.rest16_from_blender(lq, lt, scale)))
    masks, chains = [], []
    if stored and len(order) == len(stored['joints']) and all(order[i].name == stored['joints'][i]['n'] for i in range(len(order))):
        masks = [(m[0], list(m[1])) for m in stored.get('masks', [])]
        chains = [(c[0], list(c[1]), list(c[2])) for c in stored.get('chains', [])]
    name = stored['name'] if stored and stored.get('name') else arm_obj.name
    return X.skeleton_chunk(name, joints, masks, chains), [b.name for b in order], name


# ------------------------------------------------------------------ skin export
def _cofactor3(m):
    return ((m[1][1] * m[2][2] - m[1][2] * m[2][1], -(m[1][0] * m[2][2] - m[1][2] * m[2][0]), m[1][0] * m[2][1] - m[1][1] * m[2][0]),
            (-(m[0][1] * m[2][2] - m[0][2] * m[2][1]), m[0][0] * m[2][2] - m[0][2] * m[2][0], -(m[0][0] * m[2][1] - m[0][1] * m[2][0])),
            (m[0][1] * m[1][2] - m[0][2] * m[1][1], -(m[0][0] * m[1][2] - m[0][2] * m[1][0]), m[0][0] * m[1][1] - m[0][1] * m[1][0]))


def _apply4(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])


def _apply3(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2])


def _prepare_normals(mesh):
    """make per-corner normals available on every Blender version"""
    try:
        if hasattr(mesh, 'calc_normals_split'):
            mesh.calc_normals_split()
    except Exception:
        pass


def extract_skin_materials(context, mesh_objs, arm_obj, joint_names, scale=1.0, flip_v=True):
    """Blender meshes -> [(shader, [SkinVertex], [tris])] in game space (one entry per material name)"""
    joint_index = dict((n, i) for i, n in enumerate(joint_names))
    inv_arm = arm_obj.matrix_world.inverted()
    mats = {}
    order = []
    inv_scale = 1.0 / scale if scale else 1.0
    for obj in mesh_objs:
        mesh = obj.data
        mesh.calc_loop_triangles()
        _prepare_normals(mesh)
        m4 = [tuple(r) for r in (inv_arm @ obj.matrix_world)]
        nm = _cofactor3([r[:3] for r in m4[:3]])
        uvl = mesh.uv_layers.active.data if mesh.uv_layers.active is not None else None
        vgn = [vg.name for vg in obj.vertex_groups]
        infl_cache = {}

        def influences(vi):
            r = infl_cache.get(vi)
            if r is None:
                acc = {}
                for g in mesh.vertices[vi].groups:
                    if g.group < len(vgn):
                        j = joint_index.get(vgn[g.group])
                        if j is not None and g.weight > 0.0:
                            acc[j] = acc.get(j, 0.0) + g.weight
                r = infl_cache[vi] = sorted(acc.items())
            return r
        slots = [s.material.name if s.material is not None else obj.name for s in obj.material_slots]
        corner = {}
        for tri in mesh.loop_triangles:
            shader = slots[tri.material_index] if tri.material_index < len(slots) else (slots[0] if slots else obj.name)
            if shader not in mats:
                mats[shader] = ([], [])
                order.append(shader)
            verts, tris = mats[shader]
            poly = mesh.polygons[tri.polygon_index]
            ids = []
            for k in range(3):
                vi = tri.vertices[k]
                try:
                    n = tuple(tri.split_normals[k])
                except Exception:
                    n = tuple(mesh.vertices[vi].normal) if poly.use_smooth else tuple(poly.normal)
                uv = tuple(uvl[tri.loops[k]].uv) if uvl is not None else (0.0, 0.0)
                key = (shader, id(verts), vi, round(n[0], 4), round(n[1], 4), round(n[2], 4), round(uv[0], 5), round(uv[1], 5))
                i = corner.get(key)
                if i is None:
                    i = corner[key] = len(verts)
                    p = _apply4(m4, tuple(mesh.vertices[vi].co))
                    pg = V.g2b_vec(V.vscale(p, inv_scale))
                    ng = V.vnorm(V.g2b_vec(_apply3(nm, n)))
                    verts.append(X.SkinVertex(pg, ng, (uv[0], (1.0 - uv[1]) if flip_v else uv[1]), influences(vi), vi))
                ids.append(i)
            if ids[0] != ids[1] and ids[1] != ids[2] and ids[0] != ids[2]:
                tris.append(tuple(ids))
    return [(s, mats[s][0], mats[s][1]) for s in order if mats[s][1]]


def mesh_objects_for(context, arm_obj, which):
    if which == 'SELECTED':
        res = [o for o in context.selected_objects if o.type == 'MESH']
        if res:
            return res
    return [o for o in context.scene.objects if o.type == 'MESH' and o.parent is arm_obj]


def build_skin_chunk(context, arm_obj, joint_names, skeleton_name, platform, which='CHILDREN',
                     scale=None, flip_v=True, flags=0x2191, name=None):
    """returns (skin Chunk, [shader names]) or (None, [])"""
    if scale is None:
        scale = float(arm_obj.get(SCALE_PROP, 1.0)) or 1.0
    objs = mesh_objects_for(context, arm_obj, which)
    if not objs:
        return None, []
    mats = extract_skin_materials(context, objs, arm_obj, joint_names, scale, flip_v)
    if not mats:
        return None, []
    maxp = {'PS2': X.PS2_MAX_PALETTE, 'GC': X.GC_MAX_PALETTE}.get(platform, X.XBOX_MAX_PALETTE)
    groups = X.build_groups(mats, maxp, flags, X.GC_MAX_INFLUENCES if platform == 'GC' else 4)
    ck = X.polyskin_chunk(platform, name or objs[0].name, skeleton_name, groups)
    return ck, sorted(set(g.shader for g in groups))
