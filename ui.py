# -*- coding: utf-8 -*-
"""ui.py - properties, operators, panels."""
import os
import bpy
from bpy.props import (StringProperty, BoolProperty, IntProperty, FloatProperty, EnumProperty,
                       CollectionProperty, PointerProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ImportHelper, ExportHelper

from . import p3d_core as C
from . import blender_io as B

# the parsed file lives in python memory (bytes cannot be stored in ID properties)
_LIB = {'file': None, 'path': ''}

TYPE_ITEMS = [('ALL', 'All types', ''), ('PTRN', 'PTRN (skeletal)', ''), ('CAM', 'CAM (camera)', ''),
              ('BQG', 'BQG', ''), ('VRTX', 'VRTX', ''), ('PVIS', 'PVIS', ''), ('SHAD', 'SHAD', ''),
              ('TEX', 'TEX', '')]


def regions_to_str(regions):
    parts = []
    for r in regions:
        if r.raw is None:
            parts.append('%s:%s' % (r.name, ','.join('%g-%g' % (a, b) for a, b in r.ranges)))
    return ';'.join(parts)


def regions_from_str(s, old):
    keep = {r.name: r for r in old}
    out = []
    for part in [p for p in s.split(';') if p.strip()]:
        if ':' not in part:
            continue
        name, rng = part.split(':', 1)
        name = name.strip()
        ranges = []
        for item in rng.split(','):
            item = item.strip()
            if '-' in item:
                try:
                    a, b = item.split('-', 1)
                    ranges.append((float(a), float(b)))
                except ValueError:
                    pass
        r = C.Region(name, ranges, keep[name].version if name in keep else 0)
        out.append(r)
    return out


class P3DAnimItem(PropertyGroup):
    name: StringProperty(name='Name')
    anim_type: StringProperty(name='Type')
    frames: FloatProperty(name='Frames', min=1.0)
    fps: FloatProperty(name='FPS', min=1.0, default=30.0)
    cyclic: BoolProperty(name='Looping')
    regions: StringProperty(name='Regions', description='name:start-end,start-end;name2:...')
    channels: IntProperty()
    groups: IntProperty()
    matches: IntProperty()
    selected: BoolProperty(name='', default=False)


class P3DLibrary(PropertyGroup):
    path: StringProperty(subtype='FILE_PATH')
    items: CollectionProperty(type=P3DAnimItem)
    active: IntProperty()
    armature: PointerProperty(type=bpy.types.Object, name='Armature',
                              poll=lambda self, o: o.type == 'ARMATURE')
    type_filter: EnumProperty(name='Type', items=TYPE_ITEMS, default='ALL')
    only_matching: BoolProperty(name='Only animations matching the armature', default=False)


def target_armature(context):
    lib = context.scene.p3d_lib
    if lib.armature is not None:
        return lib.armature
    o = context.object
    if o is not None and o.type == 'ARMATURE':
        return o
    for o in context.scene.objects:
        if o.type == 'ARMATURE' and B.SKEL_PROP in o:
            return o
    return None


def refresh_items(context, keep_selection=True):
    lib = context.scene.p3d_lib
    f = _LIB['file']
    old = {}
    if keep_selection:
        old = {i.name: i.selected for i in lib.items}
    lib.items.clear()
    if f is None:
        return
    arm = target_armature(context)
    bones = set(b.name for b in arm.data.bones) if arm is not None else set()
    for a in f.animations:
        it = lib.items.add()
        it.name = a.name
        it.anim_type = a.anim_type
        it.frames = a.num_frames
        it.fps = a.fps
        it.cyclic = bool(a.cyclic)
        it.regions = regions_to_str(a.regions)
        it.channels = a.num_channels()
        it.groups = len(a.groups)
        it.matches = sum(1 for g in a.groups if g.name in bones)
        it.selected = old.get(a.name, False)
    if lib.active >= len(lib.items):
        lib.active = max(0, len(lib.items) - 1)


def lib_loaded(op, context):
    if _LIB['file'] is None:
        op.report({'ERROR'}, 'No .p3d library loaded (use "Load .p3d")')
        return False
    return True


def selected_indices(context, fallback_active=True):
    lib = context.scene.p3d_lib
    idx = [i for i, it in enumerate(lib.items) if it.selected]
    if not idx and fallback_active and 0 <= lib.active < len(lib.items):
        idx = [lib.active]
    return idx


# ------------------------------------------------------------------ list
class P3D_UL_anims(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, 'selected', text='')
        row.label(text=item.name)
        sub = row.row(align=True)
        sub.alignment = 'RIGHT'
        sub.label(text='%s  %df' % (item.anim_type, int(item.frames)))

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt = [self.bitflag_filter_item] * len(items)
        pat = (self.filter_name or '').lower()
        for i, it in enumerate(items):
            ok = True
            if pat and pat not in it.name.lower():
                ok = False
            if data.type_filter != 'ALL' and it.anim_type != data.type_filter:
                ok = False
            if data.only_matching and it.matches == 0:
                ok = False
            if not ok:
                flt[i] = 0
        return flt, []


# ------------------------------------------------------------------ library operators
class P3D_OT_load(Operator, ImportHelper):
    bl_idname = 'p3d.load_library'
    bl_label = 'Load .p3d'
    bl_description = 'Parse a Pure3D file and list the animations it contains'
    filename_ext = '.p3d'
    filter_glob: StringProperty(default='*.p3d', options={'HIDDEN'})

    def execute(self, context):
        try:
            _LIB['file'] = C.P3DFile.load(self.filepath)
        except (C.P3DError, OSError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        _LIB['path'] = self.filepath
        context.scene.p3d_lib.path = self.filepath
        refresh_items(context, keep_selection=False)
        f = _LIB['file']
        self.report({'INFO'}, '%d animations, skeleton: %s, skin: %s' % (
            len(f.animations), 'yes' if f.skeleton() else 'no', 'yes' if f.polyskin() else 'no'))
        return {'FINISHED'}


def joint_names_for(f, arm):
    """skeleton object of the file, or the joint names stored on the armature"""
    sk = f.skeleton()
    if sk is not None:
        return sk
    if arm is not None and B.SKEL_PROP in arm:
        try:
            import json
            return [j['n'] for j in json.loads(arm[B.SKEL_PROP])['joints']]
        except Exception:
            return None
    return None


class P3D_OT_import_skeleton(Operator):
    bl_idname = 'p3d.import_skeleton'
    bl_label = 'Import Skeleton'
    bl_description = 'Create only the armature (joint hierarchy and rest pose) stored in the loaded file'
    bl_options = {'REGISTER', 'UNDO'}

    scale: FloatProperty(name='Scale', default=1.0, min=0.0001, soft_max=100.0)
    bone_mode: EnumProperty(name='Bone orientation', items=[
        ('CHAIN', 'Along the chain', 'Bones point to their child (nicer to look at); animation stays exact'),
        ('JOINT', 'Joint axes', 'Bone axes equal the game joint axes')], default='CHAIN')

    def execute(self, context):
        if not lib_loaded(self, context):
            return {'CANCELLED'}
        sk = _LIB['file'].skeleton()
        if sk is None:
            self.report({'ERROR'}, 'The file has no skeleton chunk')
            return {'CANCELLED'}
        arm = B.create_armature(context, sk, sk.name or 'p3d_skeleton', self.scale, self.bone_mode)
        B.set_active(context, arm)
        context.scene.p3d_lib.armature = arm
        refresh_items(context)
        self.report({'INFO'}, 'Imported %d bones' % len(sk.joints))
        return {'FINISHED'}


PLATFORM_LABEL = {'XBOX': 'Xbox', 'PS2': 'PS2', 'GC': 'GameCube'}


def _skin_import(op, context, platform):
    """shared body of the Skin (Xbox) / Skin (PS2) operators"""
    if not lib_loaded(op, context):
        return {'CANCELLED'}
    f = _LIB['file']
    skin = f.polyskin()
    if skin is None:
        op.report({'ERROR'}, 'The file has no skin (PolySkin) chunk')
        return {'CANCELLED'}
    if skin.platform != platform:
        label = PLATFORM_LABEL.get(skin.platform, skin.platform)
        op.report({'ERROR'}, 'This file contains a %s skin - use the Skin (%s) importer' % (label, label))
        return {'CANCELLED'}
    arm = target_armature(context) if op.use_armature else None
    names = joint_names_for(f, arm)
    if names is None:
        op.report({'ERROR'}, 'This file has no skeleton: import the skeleton first (from an Xbox/animation .p3d) '
                             'so the vertex groups can be named after its joints')
        return {'CANCELLED'}
    scale = float(arm.get(B.SCALE_PROP, op.scale)) if arm is not None else op.scale
    B.create_skin_mesh(context, skin, arm, names, scale, op.flip_v, op.normals)
    op.report({'INFO'}, 'Imported %s skin%s' % (PLATFORM_LABEL.get(platform, platform), ' bound to %s' % arm.name if arm is not None else ' (no armature)'))
    return {'FINISHED'}


class P3D_OT_import_skin_xbox(Operator):
    bl_idname = 'p3d.import_skin_xbox'
    bl_label = 'Skin (Xbox)'
    bl_description = ('Create the skinned mesh from an Xbox-format file (vertex groups named like the joints). If an '
                      'armature is selected in the panel it gets parented and an Armature modifier')
    bl_options = {'REGISTER', 'UNDO'}

    scale: FloatProperty(name='Scale', default=1.0, min=0.0001, soft_max=100.0,
                         description='Ignored when an armature is used: its own import scale is applied')
    flip_v: BoolProperty(name='Flip V', default=True)
    normals: BoolProperty(name='Custom normals', default=True)
    use_armature: BoolProperty(name='Bind to armature', default=True)

    def execute(self, context):
        return _skin_import(self, context, 'XBOX')


class P3D_OT_import_skin_ps2(Operator):
    bl_idname = 'p3d.import_skin_ps2'
    bl_label = 'Skin (PS2)'
    bl_description = ('Create the skinned mesh from a PlayStation 2 file (VIF packet data, e.g. global_hulk.p3d). '
                      'PS2 files carry no skeleton, so an imported armature is needed: its joint names become the '
                      'vertex group names')
    bl_options = {'REGISTER', 'UNDO'}

    scale: FloatProperty(name='Scale', default=1.0, min=0.0001, soft_max=100.0,
                         description='Ignored when an armature is used: its own import scale is applied')
    flip_v: BoolProperty(name='Flip V', default=True)
    normals: BoolProperty(name='Custom normals', default=True)
    use_armature: BoolProperty(name='Bind to armature', default=True)

    def execute(self, context):
        return _skin_import(self, context, 'PS2')


class P3D_OT_import_skin_gc(Operator):
    bl_idname = 'p3d.import_skin_gc'
    bl_label = 'Skin (GameCube)'
    bl_description = ('Create the skinned mesh from a GameCube file (GX display list + indexed 16 bit vertex arrays, '
                      'e.g. global_hulk_1.p3d). Joint names for the vertex groups come from the file skeleton or from '
                      'the imported armature')
    bl_options = {'REGISTER', 'UNDO'}

    scale: FloatProperty(name='Scale', default=1.0, min=0.0001, soft_max=100.0,
                         description='Ignored when an armature is used: its own import scale is applied')
    flip_v: BoolProperty(name='Flip V', default=True)
    normals: BoolProperty(name='Custom normals', default=True)
    use_armature: BoolProperty(name='Bind to armature', default=True)

    def execute(self, context):
        return _skin_import(self, context, 'GC')


class P3D_OT_import_anims(Operator):
    bl_idname = 'p3d.import_animations'
    bl_label = 'Import Animations'
    bl_description = 'Convert the selected animations into Blender actions on the target armature'
    bl_options = {'REGISTER', 'UNDO'}

    which: EnumProperty(name='Animations', items=[('SELECTED', 'Selected (or active)', ''),
                                                  ('ALL', 'All matching', '')], default='SELECTED')
    bake_step: IntProperty(name='Resample step', default=0, min=0, max=30,
                           description='0 keeps the original keys, N>0 resamples every N frames with slerp')
    set_scene: BoolProperty(name='Set scene range / FPS', default=True)
    assign_last: BoolProperty(name='Assign last action', default=True)
    add_missing_bones: BoolProperty(name='Add bones for extra groups', default=True,
                                    description='Create bones for animation groups the armature does not have '
                                                '(Wrist_Grapple, Root_Grapple, extra weapon joints...) so their '
                                                'position/rotation keys are not lost')

    def execute(self, context):
        if not lib_loaded(self, context):
            return {'CANCELLED'}
        arm = target_armature(context)
        if arm is None:
            self.report({'ERROR'}, 'Select or import an armature first')
            return {'CANCELLED'}
        B.object_mode(context)
        f = _LIB['file']
        anims = f.animations
        lib = context.scene.p3d_lib
        idx = range(len(anims)) if self.which == 'ALL' else selected_indices(context)
        done, skipped, last = 0, 0, None
        bones = set(b.name for b in arm.data.bones)
        todo = [anims[i] for i in idx if any(g.name in bones for g in anims[i].groups)]
        skipped = len(list(idx)) - len(todo)
        if self.add_missing_bones and todo:
            added = B.ensure_extra_bones(context, arm, todo)
            if added:
                self.report({'INFO'}, 'Added %d extra bones: %s' % (len(added), ', '.join(added[:6])))
        for a in todo:
            act, m, _ = B.import_animation(context, arm, a, bake_step=self.bake_step, assign=False)
            last = (act, a)
            done += 1
        if last is not None and self.assign_last:
            B.assign_action(arm, last[0], last[0].slots[0] if B.action_has_slots() and len(last[0].slots) else None)
            if self.set_scene:
                last_n = max(0, int(last[1].num_frames) - 1)
                context.scene.frame_start = 0
                context.scene.frame_end = last_n
                context.scene.render.fps = int(round(last[1].fps)) or 30
                context.scene.render.fps_base = 1.0
        self.report({'INFO'}, '%d imported, %d skipped (no matching bones)' % (done, skipped))
        return {'FINISHED'}


class P3D_OT_select(Operator):
    bl_idname = 'p3d.select'
    bl_label = 'Select'
    bl_options = {'INTERNAL'}
    mode: EnumProperty(items=[('ALL', 'All', ''), ('NONE', 'None', ''), ('INVERT', 'Invert', ''),
                              ('MATCH', 'Matching armature', '')])

    def execute(self, context):
        lib = context.scene.p3d_lib
        for it in lib.items:
            if self.mode == 'ALL':
                it.selected = True
            elif self.mode == 'NONE':
                it.selected = False
            elif self.mode == 'INVERT':
                it.selected = not it.selected
            else:
                it.selected = it.matches > 0
        return {'FINISHED'}


class P3D_OT_edit(Operator):
    bl_idname = 'p3d.edit'
    bl_label = 'Edit'
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = 'Edit the loaded library'
    action: EnumProperty(items=[('APPLY', 'Apply edits', 'Write name / frames / fps / loop / regions of the active entry into the library'),
                                ('DUP', 'Duplicate', ''), ('DELETE', 'Delete selected', ''),
                                ('UP', 'Move up', ''), ('DOWN', 'Move down', '')])

    def execute(self, context):
        if not lib_loaded(self, context):
            return {'CANCELLED'}
        lib = context.scene.p3d_lib
        f = _LIB['file']
        anims = f.animations
        act = self.action
        if act == 'APPLY':
            if not (0 <= lib.active < len(anims)):
                return {'CANCELLED'}
            a, it = anims[lib.active], lib.items[lib.active]
            a.name = f.unique_name(it.name, ignore=a)
            a.num_frames = float(it.frames)
            a.fps = float(it.fps)
            a.cyclic = 1 if it.cyclic else 0
            a.regions = regions_from_str(it.regions, a.regions)
        elif act == 'DUP':
            for i in reversed(selected_indices(context)):
                n = anims[i].copy()
                n.name = f.unique_name(n.name)
                f.items.insert(f.items.index(anims[i]) + 1, n)
        elif act == 'DELETE':
            for i in sorted(selected_indices(context), reverse=True):
                f.items.remove(anims[i])
        else:
            i = lib.active
            if act == 'UP' and i > 0:
                x, y = f.items.index(anims[i]), f.items.index(anims[i - 1])
                f.items[x], f.items[y] = f.items[y], f.items[x]
                lib.active = i - 1
            elif act == 'DOWN' and i < len(anims) - 1:
                x, y = f.items.index(anims[i]), f.items.index(anims[i + 1])
                f.items[x], f.items[y] = f.items[y], f.items[x]
                lib.active = i + 1
        sel = {i.name: i.selected for i in lib.items}
        active = lib.active
        refresh_items(context, keep_selection=False)
        for it in lib.items:
            it.selected = sel.get(it.name, False)
        lib.active = min(active, max(0, len(lib.items) - 1))
        return {'FINISHED'}


class P3D_OT_save(Operator, ExportHelper):
    bl_idname = 'p3d.save_library'
    bl_label = 'Save .p3d'
    bl_description = 'Write the (edited) library, including skeleton and skin chunks, to a file'
    filename_ext = '.p3d'
    filter_glob: StringProperty(default='*.p3d', options={'HIDDEN'})

    def invoke(self, context, event):
        if _LIB['path']:
            base, ext = os.path.splitext(_LIB['path'])
            self.filepath = base + '_edited.p3d'
        return super().invoke(context, event)

    def execute(self, context):
        if not lib_loaded(self, context):
            return {'CANCELLED'}
        try:
            _LIB['file'].save(self.filepath)
        except OSError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, 'Saved %s' % self.filepath)
        return {'FINISHED'}


class P3D_OT_extract(Operator, ExportHelper):
    bl_idname = 'p3d.extract'
    bl_label = 'Extract Selected'
    bl_description = 'Write the selected animations to new .p3d files'
    filename_ext = '.p3d'
    filter_glob: StringProperty(default='*.p3d', options={'HIDDEN'})
    split: BoolProperty(name='One file per animation', default=True,
                        description='Files are written next to the chosen file name')
    include_model: BoolProperty(name='Include skeleton and skin', default=False)

    def execute(self, context):
        if not lib_loaded(self, context):
            return {'CANCELLED'}
        f = _LIB['file']
        anims = f.animations
        sel = [anims[i] for i in selected_indices(context)]
        if not sel:
            self.report({'ERROR'}, 'Nothing selected')
            return {'CANCELLED'}
        extra = []
        if self.include_model:
            extra = [c for c in f.other_chunks()
                     if c.id in (C.ID_SKELETON, C.ID_POLYSKIN)]
        folder = os.path.dirname(self.filepath)
        if self.split:
            for a in sel:
                nf = C.new_animation_file([a.copy()], extra)
                safe = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in a.name)
                nf.save(os.path.join(folder, safe + '.p3d'))
            self.report({'INFO'}, 'Extracted %d files to %s' % (len(sel), folder))
        else:
            C.new_animation_file([a.copy() for a in sel], extra).save(self.filepath)
            self.report({'INFO'}, 'Extracted %d animations' % len(sel))
        return {'FINISHED'}


# ------------------------------------------------------------------ export (Blender -> p3d)
class P3D_OT_export(Operator, ExportHelper):
    bl_idname = 'export_scene.p3d_ud_anim'
    bl_label = 'Export Pure3D (.p3d)'
    bl_description = 'Export animations, skeleton and/or skin (Xbox, PS2 or GameCube format) of the armature to a Pure3D file'
    filename_ext = '.p3d'
    filter_glob: StringProperty(default='*.p3d', options={'HIDDEN'})

    export_animations: BoolProperty(name='Animations', default=True)
    export_skeleton: BoolProperty(name='Skeleton', default=False,
                                  description='Write the armature as a Pure3D skeleton chunk')
    skin_format: EnumProperty(name='Skin', items=[
        ('NONE', 'None', 'Do not export a skin'),
        ('XBOX', 'Skin (Xbox)', 'Triangle strip index buffer, float weights'),
        ('PS2', 'Skin (PS2)', 'PlayStation 2 VIF packets (48-54 vertex strips, ADC restarts)'),
        ('GC', 'Skin (GameCube)', 'GX display list, indexed s16/s8/u16 arrays, 3 influences per vertex')], default='NONE')
    skin_source: EnumProperty(name='Meshes', items=[
        ('SELECTED', 'Selected meshes', 'Falls back to the meshes parented to the armature'),
        ('CHILDREN', 'Meshes parented to the armature', '')], default='CHILDREN')
    flip_v: BoolProperty(name='Flip V', default=True, description='Inverse of the importer option')
    keep_joint_axes: BoolProperty(name='Keep original joint axes', default=True,
                                  description='Imported skeletons keep their game joint orientation; only positions '
                                              'are taken from the bones (turn off to use the bone axes)')
    scope: EnumProperty(name='Actions', items=[
        ('ACTIVE', 'Active action', ''), ('P3D', 'All imported (p3d) actions', ''),
        ('ALL', 'All actions using the armature', '')], default='P3D')
    target: EnumProperty(name='Output', items=[
        ('LIB', 'Merge into loaded library', 'Replace same-named animations / skeleton / skin, keep every other chunk '
                                              '(textures, shaders...)'),
        ('NEW', 'New file', 'One file containing everything exported'),
        ('SPLIT', 'One file per action', 'Batch export; skeleton/skin go to the chosen file name')], default='NEW')
    rot_format: EnumProperty(name='Rotation precision', items=[
        ('AUTO', 'Keep original', 'Use the encoding the animation had when imported (16 bit if unknown)'),
        ('16', '16 bit', ''), ('8', '8 bit (smallest)', '')], default='AUTO')
    bake_all: BoolProperty(name='Key every frame', default=False)
    reduce: FloatProperty(name='Key reduction', default=0.0, min=0.0, max=0.1, precision=5,
                          description='Tolerance (radians); 0 keeps all keys')
    start_at_zero: BoolProperty(name='Start at frame 0', default=True)
    all_bones: BoolProperty(name='Write all skeleton bones', default=False,
                            description='Also write 1-key rotation channels for bones without animation')

    def invoke(self, context, event):
        if _LIB['path'] and self.target == 'LIB':
            base, ext = os.path.splitext(_LIB['path'])
            self.filepath = base + '_edited.p3d'
        return super().invoke(context, event)

    def draw(self, context):
        lay = self.layout
        box = lay.box()
        box.label(text='Content')
        box.prop(self, 'export_animations')
        box.prop(self, 'export_skeleton')
        box.prop(self, 'skin_format')
        if self.skin_format != 'NONE':
            box.prop(self, 'skin_source')
            box.prop(self, 'flip_v')
        if self.export_skeleton:
            box.prop(self, 'keep_joint_axes')
        lay.prop(self, 'target')
        if self.export_animations:
            box = lay.box()
            box.label(text='Animations')
            for p in ('scope', 'rot_format', 'bake_all', 'reduce', 'start_at_zero', 'all_bones'):
                box.prop(self, p)

    def _model_chunks(self, context, arm, lib):
        """skeleton / skin chunks requested by the options -> (list of chunks, shader names)"""
        chunks, shaders = [], []
        want_skin = self.skin_format != 'NONE'
        if not (self.export_skeleton or want_skin):
            return chunks, shaders
        sk_chunk, names, sk_name = B.export_skeleton(arm, self.keep_joint_axes)
        if self.export_skeleton:
            chunks.append(sk_chunk)
        if want_skin:
            flags, name = 0x2191, None
            if lib is not None:
                old = lib.polyskin()
                if old is not None and old.groups:
                    flags, name = old.groups[0].flags, old.name
            skin, shaders = B.build_skin_chunk(context, arm, names, sk_name, self.skin_format,
                                               self.skin_source, flip_v=self.flip_v, flags=flags, name=name)
            for w in sorted(set(B.X.WARNINGS)):
                self.report({'WARNING'}, w)
            if skin is None:
                self.report({'WARNING'}, 'No mesh found to export as skin')
            else:
                chunks.append(skin)
        return chunks, shaders

    def execute(self, context):
        arm = target_armature(context)
        if arm is None:
            self.report({'ERROR'}, 'No armature found')
            return {'CANCELLED'}
        B.object_mode(context)
        anims = []
        if self.export_animations:
            if self.scope == 'ACTIVE':
                ad = arm.animation_data
                acts = [ad.action] if ad is not None and ad.action else []
            else:
                acts = B.actions_for_armature(arm, only_p3d=(self.scope == 'P3D'))
            opts = B.ExportOptions()
            opts.rot_format, opts.bake_all, opts.reduce = self.rot_format, self.bake_all, self.reduce
            opts.start_at_zero, opts.all_bones = self.start_at_zero, self.all_bones
            for a in acts:
                try:
                    anims.append(B.export_action(context, arm, a, opts))
                except Exception as e:      # keep batch export going
                    self.report({'WARNING'}, 'Action %s failed: %s' % (a.name, e))
        lib = _LIB['file'] if (self.target == 'LIB' and _LIB['file'] is not None) else None
        try:
            model, shaders = self._model_chunks(context, arm, _LIB['file'])
        except Exception as e:
            self.report({'ERROR'}, 'Skeleton/skin export failed: %s' % e)
            return {'CANCELLED'}
        if not anims and not model:
            self.report({'ERROR'}, 'Nothing to export (no actions, skeleton or skin selected)')
            return {'CANCELLED'}
        folder = os.path.dirname(self.filepath)
        if self.target == 'SPLIT':
            for a in anims:
                safe = ''.join(ch if ch.isalnum() or ch in '-_.' else '_' for ch in a.name)
                C.new_animation_file([a]).save(os.path.join(folder, safe + '.p3d'))
            if model:
                C.new_animation_file([], model).save(self.filepath)
        elif lib is not None:
            for ck in model:
                _replace_chunk(lib, ck)
            if model and shaders:
                _ensure_shaders(lib, shaders)
            for a in anims:
                lib.add_animation(a, replace=True)
            lib.save(self.filepath)
            refresh_items(context)
        else:
            C.new_animation_file(anims, model).save(self.filepath)
        what = []
        if anims:
            what.append('%d animations' % len(anims))
        for ck in model:
            what.append({C.ID_SKELETON: 'skeleton', C.ID_POLYSKIN: 'skin (%s)' % PLATFORM_LABEL.get(self.skin_format, self.skin_format)}.get(ck.id, 'chunk'))
        self.report({'INFO'}, 'Exported ' + ', '.join(what))
        return {'FINISHED'}


def _replace_chunk(lib, ck):
    """replace the first top-level chunk with the same id (skeleton 0x23000 / skin 0x10001) or append"""
    for i, it in enumerate(lib.items):
        if isinstance(it, C.Chunk) and it.id == ck.id:
            lib.items[i] = ck
            return
    lib.items.append(ck)


def _ensure_shaders(lib, names):
    """clone an existing shader chunk for materials the library does not know yet (PS2 files carry shaders)"""
    have = {}
    for it in lib.items:
        if isinstance(it, C.Chunk) and it.id == 0x11000:
            have[C.read_str(it.payload, 0)[0]] = it
    if not have:
        return
    proto = list(have.values())[0]
    pos = max(i for i, it in enumerate(lib.items) if isinstance(it, C.Chunk) and it.id == 0x11000) + 1
    for n in names:
        if n not in have:
            lib.items.insert(pos, C.clone_shader_chunk(proto, n))
            pos += 1


# ------------------------------------------------------------------ File > Import (batch capable)
class P3D_OT_import(Operator, ImportHelper):
    bl_idname = 'import_scene.p3d_ud'
    bl_label = 'Import Pure3D (.p3d)'
    bl_description = 'Import skeleton, skin and/or animations from one or many .p3d files'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.p3d'
    filter_glob: StringProperty(default='*.p3d', options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH')

    import_skeleton: BoolProperty(name='Skeleton', default=True)
    import_skin_xbox: BoolProperty(name='Skin (Xbox)', default=True)
    import_skin_ps2: BoolProperty(name='Skin (PS2)', default=True)
    import_skin_gc: BoolProperty(name='Skin (GameCube)', default=True)
    import_anims: BoolProperty(name='Animations', default=True)
    scale: FloatProperty(name='Scale', default=1.0, min=0.0001, soft_max=100.0)
    bone_mode: EnumProperty(name='Bone orientation', items=[('CHAIN', 'Along the chain', ''),
                                                            ('JOINT', 'Joint axes', '')], default='CHAIN')
    bake_step: IntProperty(name='Resample step', default=0, min=0, max=30)
    add_missing_bones: BoolProperty(name='Add bones for extra groups', default=True)

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files] or [self.filepath]
        arm = target_armature(context)
        total = 0
        for p in paths:
            try:
                pf = C.P3DFile.load(p)
            except (C.P3DError, OSError) as e:
                self.report({'WARNING'}, '%s: %s' % (os.path.basename(p), e))
                continue
            _LIB['file'], _LIB['path'] = pf, p
            sk = pf.skeleton()
            if self.import_skeleton and sk is not None and (arm is None or B.SKEL_PROP not in arm):
                arm = B.create_armature(context, sk, sk.name or 'p3d_skeleton', self.scale, self.bone_mode)
            skin = pf.polyskin()
            if skin is not None and ((skin.platform == 'XBOX' and self.import_skin_xbox) or
                                     (skin.platform == 'PS2' and self.import_skin_ps2) or
                                     (skin.platform == 'GC' and self.import_skin_gc)):
                names = joint_names_for(pf, arm)
                if names is not None:
                    sc = float(arm.get(B.SCALE_PROP, self.scale)) if arm is not None else self.scale
                    B.create_skin_mesh(context, skin, arm, names, sc)
            if self.import_anims and arm is not None:
                bones = set(b.name for b in arm.data.bones)
                todo = [a for a in pf.animations if any(g.name in bones for g in a.groups)]
                if self.add_missing_bones and todo:
                    B.ensure_extra_bones(context, arm, todo)
                for a in todo:
                    B.import_animation(context, arm, a, bake_step=self.bake_step, assign=False)
                    total += 1
        if arm is not None:
            context.scene.p3d_lib.armature = arm
            B.set_active(context, arm)
        context.scene.p3d_lib.path = _LIB['path']
        refresh_items(context, keep_selection=False)
        self.report({'INFO'}, '%d animations imported' % total)
        return {'FINISHED'}


# ------------------------------------------------------------------ panels
class P3D_PT_main(Panel):
    bl_label = 'Pure3D Animations'
    bl_idname = 'P3D_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'P3D'

    def draw(self, context):
        layout = self.layout
        lib = context.scene.p3d_lib
        col = layout.column(align=True)
        col.operator('p3d.load_library', icon='FILE_FOLDER')
        if _LIB['file'] is None:
            if len(lib.items):
                col.label(text='Library lost (restart) - reload the file', icon='ERROR')
            return
        col.label(text=os.path.basename(_LIB['path']) or '(unsaved)')
        col.prop(lib, 'armature')
        row = layout.row(align=True)
        row.operator('p3d.import_skeleton', icon='ARMATURE_DATA')
        row = layout.row(align=True)
        row.operator('p3d.import_skin_xbox', icon='MESH_DATA')
        row.operator('p3d.import_skin_ps2', icon='MESH_DATA')
        row.operator('p3d.import_skin_gc', icon='MESH_DATA')
        layout.separator()
        row = layout.row(align=True)
        row.prop(lib, 'type_filter', text='')
        row.prop(lib, 'only_matching', text='', icon='BONE_DATA')
        layout.template_list('P3D_UL_anims', '', lib, 'items', lib, 'active', rows=8)
        row = layout.row(align=True)
        for m, t in (('ALL', 'All'), ('NONE', 'None'), ('INVERT', 'Invert'), ('MATCH', 'Match')):
            row.operator('p3d.select', text=t).mode = m
        row = layout.row(align=True)
        op = row.operator('p3d.import_animations', text='Import Selected', icon='IMPORT')
        op.which = 'SELECTED'
        op = row.operator('p3d.import_animations', text='All', icon='ACTION')
        op.which = 'ALL'
        layout.operator('p3d.extract', icon='DUPLICATE')


class P3D_PT_edit(Panel):
    bl_label = 'Edit Entry'
    bl_idname = 'P3D_PT_edit'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'P3D'
    bl_parent_id = 'P3D_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _LIB['file'] is not None

    def draw(self, context):
        layout = self.layout
        lib = context.scene.p3d_lib
        if not (0 <= lib.active < len(lib.items)):
            return
        it = lib.items[lib.active]
        col = layout.column(align=True)
        col.prop(it, 'name')
        col.prop(it, 'frames')
        col.prop(it, 'fps')
        col.prop(it, 'cyclic')
        col.prop(it, 'regions')
        col.label(text='%d groups / %d channels / %d match armature' % (it.groups, it.channels, it.matches))
        col.operator('p3d.edit', text='Apply to Library', icon='CHECKMARK').action = 'APPLY'
        row = layout.row(align=True)
        row.operator('p3d.edit', text='Duplicate', icon='DUPLICATE').action = 'DUP'
        row.operator('p3d.edit', text='Delete', icon='TRASH').action = 'DELETE'
        row = layout.row(align=True)
        row.operator('p3d.edit', text='Up', icon='TRIA_UP').action = 'UP'
        row.operator('p3d.edit', text='Down', icon='TRIA_DOWN').action = 'DOWN'
        layout.operator('p3d.save_library', icon='FILE_TICK')


class P3D_PT_export(Panel):
    bl_label = 'Export'
    bl_idname = 'P3D_PT_export'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'P3D'
    bl_parent_id = 'P3D_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text='Animations, skeleton, skin (Xbox / PS2 / GC)')
        col.operator('export_scene.p3d_ud_anim', icon='EXPORT')


def menu_import(self, context):
    self.layout.operator(P3D_OT_import.bl_idname, text='Pure3D Skeleton / Animations (.p3d)')


def menu_export(self, context):
    self.layout.operator(P3D_OT_export.bl_idname, text='Pure3D Animations / Skeleton / Skin (.p3d)')


CLASSES = (P3DAnimItem, P3DLibrary, P3D_UL_anims, P3D_OT_load, P3D_OT_import_skeleton, P3D_OT_import_skin_xbox, P3D_OT_import_skin_ps2, P3D_OT_import_skin_gc, P3D_OT_import_anims,
           P3D_OT_select, P3D_OT_edit, P3D_OT_save, P3D_OT_extract, P3D_OT_export, P3D_OT_import,
           P3D_PT_main, P3D_PT_edit, P3D_PT_export)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.p3d_lib = PointerProperty(type=P3DLibrary)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    del bpy.types.Scene.p3d_lib
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
