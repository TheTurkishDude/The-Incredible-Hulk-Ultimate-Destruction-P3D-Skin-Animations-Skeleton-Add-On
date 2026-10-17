# -*- coding: utf-8 -*-
bl_info = {
    'name': 'Pure3D (.p3d) Animation Tools - Hulk: Ultimate Destruction',
    'author': 'TheTurkishDude',
    'version': (1, 0, 0),
    'blender': (2, 80, 0),
    'location': 'View3D > Sidebar > P3D  |  File > Import / Export',
    'description': 'View, extract, edit, batch import and export Radical Pure3D (PTRN) animations, skeleton and Xbox/PS2/GameCube skins',
    'category': 'Import-Export',
}

if 'bpy' in locals():
    import importlib
    for _m in ('p3d_core', 'p3d_convert', 'p3d_skin_export', 'blender_io', 'ui'):
        if _m in globals():
            importlib.reload(globals()[_m])
else:
    from . import p3d_core, p3d_convert, p3d_skin_export, blender_io, ui


def register():
    ui.register()


def unregister():
    ui.unregister()
