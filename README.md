# Pure3D Animation Tools for Blender

**View, extract, edit, batch import and export Radical Entertainment *Pure3D* (`.p3d`) animations, skeletons and skins — with Xbox, PlayStation 2 and GameCube skin formats.**
Built around *The Incredible Hulk: Ultimate Destruction* (PTRN skeletal animations), working for any Pure3D file that uses the same chunk layout.

![Blender](https://img.shields.io/badge/Blender-2.80%20→%205.x-orange)
![Python](https://img.shields.io/badge/Python-3.7%2B-blue)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-green)

Author: **TheTurkishDude**

---

## Features

| | Import | Export |
|---|:---:|:---:|
| **Animations** (PTRN: 8/16-bit quaternions, translation channels, regions, looping flag) | ✅ | ✅ |
| **Skeleton** (joint hierarchy, rest pose, body-part masks, leg chains) | ✅ | ✅ |
| **Skin – Xbox** (triangle-strip index buffer, float weights) | ✅ | ✅ |
| **Skin – PlayStation 2** (VIF packet streams, ADC strip restarts) | ✅ | ✅ |
| **Skin – GameCube** (GX display list, indexed 16/8-bit vertex arrays) | ✅ | ✅ |

* 📂 **Library browser** – load a `.p3d`, browse every animation (type, length, filter by name/type/armature match), multi-select.
* 🎬 **Batch** – import all matching animations at once, import many files at once (File ▸ Import), export one file per action.
* ✂️ **Extract** – write selected animations to separate `.p3d` files.
* ✏️ **Edit** – rename, change frame count / FPS / looping / leg-lock regions, duplicate, delete, reorder, save – or edit the keys as normal Blender actions and export them back.
* 🦴 **Faithful rigs** – bones are created along the chain (pretty) while the animation stays exact; extra animation groups (e.g. `Wrist_Grapple`, `Root_Grapple`) get their own bones so no channel is lost.
* 🧩 **Skins with vertex groups named like the skeleton joints**, bound to the armature with an Armature modifier.
* 🔁 **Merge export** – write skeleton / skin / animations *into* the loaded library: textures, shaders and every other chunk are kept untouched.
* 🧪 **All Blender versions** – legacy action API (2.80 – 4.3) and the slotted/layered actions of 4.4 / 5.x are both handled.

---

## Installation

Download one of the two zips from the *Releases* page (or build them, see below) and install **without unzipping**:

| Blender | Package | How |
|---|---|---|
| 4.2 and newer (Extensions) | `p3d_ud_animation_extension_4.2plus.zip` | *Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…* |
| 2.80 – 4.1 (also 4.2+ via *Install Legacy Add-on*) | `p3d_ud_animation_legacy.zip` | *Preferences ▸ Add-ons ▸ Install…* then enable **Pure3D (.p3d) Animation Tools** |

The panel appears in the 3D Viewport sidebar (**N**) on the **P3D** tab; the operators are also in *File ▸ Import* and *File ▸ Export*.

### Build the zips yourself
```bash
# legacy (Blender 2.80 – 4.1): the folder must be inside the zip
zip -r p3d_ud_animation_legacy.zip p3d_ud_animation -x "*/blender_manifest.toml" -x "*/__pycache__/*"
# extension (Blender 4.2+): files at the root of the zip
(cd p3d_ud_animation && zip -r ../p3d_ud_animation_extension_4.2plus.zip . -x "__pycache__/*")
```

---

## Quick start

1. **Load .p3d** – pick e.g. `hulkudanims.p3d`. All animations are listed.
2. **Import Skeleton** – builds the armature (scale and bone orientation are options).
3. **Skin (Xbox) / Skin (PS2) / Skin (GameCube)** – imports the mesh with vertex groups named after the joints. Pick the button that matches the file's platform; the add-on tells you if it does not.
   *PS2 files may not contain a skeleton: import the skeleton first (from an Xbox/animation/GameCube file) and select the armature in the panel.*
4. **Import Selected / All** – every animation becomes an Action on the armature (sparse keys are kept, linear interpolation; optional slerp resampling).
5. Edit in Blender (graph editor, dope sheet, pose mode) or use **Edit Entry** for animation properties.
6. **Export** – tick what to write and choose the output mode.

### Export options

| Option | Meaning |
|---|---|
| **Animations / Skeleton / Skin** | Skin is *None*, *Xbox*, *PS2* or *GameCube*. Meshes come from the selection or from the meshes parented to the armature. |
| **Output** | *Merge into loaded library* (replaces same-named animations / skeleton / skin, keeps everything else, clones a shader chunk for new materials) · *New file* · *One file per action* (skeleton/skin go to the chosen file name) |
| **Rotation precision** | keep the original encoding of each channel, or force 16-bit / 8-bit |
| **Key every frame / Key reduction / Start at frame 0 / Write all bones** | sampling controls for animations |
| **Keep original joint axes** | imported skeletons keep their game joint orientation; only positions come from the bones |

Skin export triangulates, splits by material and by the palette limit (Xbox 18, PS2 29, GameCube 29 joints per group), builds triangle strips, quantizes for the target platform and writes bounding boxes/spheres. GameCube keeps 3 influences per vertex, Xbox/PS2 4.

---

## Supported data

* **Animations:** chunk `0x121000` (`PTRN`, also `CAM`, `BQG`, `VRTX`, `PVIS`, `SHAD`, `TEX` are listed, edited, extracted and preserved; only skeletal PTRN clips are converted to Blender actions).
* **Channels:** 16-bit quaternions (`0x121112`), 8-bit quaternions (`0x121114`), float quaternions, 1/2/3-DOF translation, float, int, bool, colour, string.
* **Skeleton:** `0x23000` with joints, masks and leg chains.
* **Skins:** `0x10001` PolySkin in the three platform layouts.
* Coordinate conversion: game Y-up (mirrored handedness) → Blender Z-up `(x, y, z) → (x, z, y)`, winding fixed automatically.

The reverse-engineered layouts are written down in **[docs/FORMAT_NOTES.md](docs/FORMAT_NOTES.md)**.

---

## Verification status

* The file codecs are verified by **re-encoding the original game files**: a full re-serialisation of `hulkudanims.p3d` is byte-identical; skeleton chunks, Xbox skin groups, PS2 VIF streams and GameCube groups re-encode identically (PS2: apart from trailing padding). Decode → export → decode round trips between all three skin platforms preserve triangles and dominant bone weights.
* Animation import → export reproduces the original quantized keys exactly for 178 skeletal clips (rotations bit-identical, translations to 1e-14).
* The Blender-facing code is exercised by the test-suite with a small `bpy`/`mathutils` stand-in (`tests/fakebpy.py`). Please report problems you hit on real Blender versions via *Issues*.
* The PS2 and GameCube exporters follow the observed layouts and stay within the combinations found in the original files (palette ≤ 29, packet capacity fitted to the two PS2 groups, UV 0..2 / positions ±8 on GameCube). **They have not been verified in-game or on hardware.**

## Known limitations

* Non-skeletal animation types are not converted to Blender actions (they are preserved in the library).
* The Xbox per-vertex byte in chunk `0x10010` is not decoded; it is written as the low byte of the source vertex index.
* PS2 streams are padded to 16 bytes only (the original pads further by a rule that is not understood).
* GameCube UVs are unsigned 16-bit (0..2) and positions signed 16-bit with 12 fraction bits (±8 units) – larger values raise an error / are clamped with a warning.

---

## Repository layout

```
p3d_ud_animation/        the add-on package
  __init__.py            bl_info, registration
  blender_manifest.toml  manifest for Blender 4.2+ extensions
  p3d_core.py            chunk reader/writer, animation / skeleton / skin decoders (no bpy)
  p3d_convert.py         game ↔ Blender space maths (no bpy)
  p3d_skin_export.py     skeleton + Xbox / PS2 / GameCube skin encoders, strips, palettes (no bpy)
  blender_io.py          Blender side: armature, mesh, actions, export
  ui.py                  properties, operators, panels
tests/                   test-suite + bpy stand-in (needs your own game files, see below)
docs/FORMAT_NOTES.md     reverse-engineered format documentation
```

### Running the tests
Game files are **not** included. Point `P3D_SAMPLES` at a folder containing `hulkudanims.p3d` (Xbox), `global_hulk.p3d` (PS2) and `global_hulk_1.p3d` (GameCube):
```bash
P3D_SAMPLES=/path/to/files python tests/run_all.py
```
The core modules only use the Python standard library (3.7+); the tests additionally use `numpy`.

---

## Disclaimer

This is an unofficial fan/modding tool. It is not affiliated with or endorsed by Radical Entertainment, Activision, Universal or Marvel. No game assets are distributed with this repository – you need your own legally obtained game files.

## License

GPL-3.0-or-later (as declared in `blender_manifest.toml`).

## Credits

Created by **TheTurkishDude**.
