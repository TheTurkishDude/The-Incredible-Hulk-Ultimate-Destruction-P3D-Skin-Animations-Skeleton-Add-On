# -*- coding: utf-8 -*-
"""
p3d_skin_export.py - build Pure3D skeleton / PolySkin (Xbox and PS2) chunks.  No bpy dependency.

Everything here works in *game space* (Y up, mirrored handedness, see p3d_convert).
Data model
    SkinVertex   pos (x,y,z)  nrm (x,y,z)  uv (u,v)  infl [(joint_index, weight)]  src (int, source vertex id)
    SkinGroup    shader, verts [SkinVertex], tris [(a,b,c)] oriented so cross(b-a,c-a).n > 0, palette [joint]
"""
import math
import struct

from . import p3d_core as C
from .p3d_core import Chunk, pack_str

XBOX_MAX_PALETTE = 18      # largest palette found in the Xbox data
GC_MAX_PALETTE = 29        # largest palette found in the GameCube data (records store slots as bytes)
GC_MAX_INFLUENCES = 3      # GameCube weight records hold 3 influences
WARNINGS = []              # messages collected during an export (cleared by build_groups)
PS2_MAX_PALETTE = 29       # largest palette found in the PS2 data
PS2_VU_MEM = 1024          # VU1 data memory in quad words


# ------------------------------------------------------------------ skeleton
def skeleton_chunk(name, joints, masks=(), chains=(), version=0):
    """joints: [(name, parent, rest16)]  masks: [(name,[words])]  chains: [(name,[j0,j1,j2],[words])]"""
    p = pack_str(name) + struct.pack('<4I', version, len(joints), len(masks), len(chains))
    ck = Chunk(C.ID_SKELETON, p)
    for nm, par, rest in joints:
        ck.children.append(Chunk(C.ID_SKEL_JOINT, pack_str(nm) + struct.pack('<I16f', par, *rest)))
    for nm, words in masks:
        ck.children.append(Chunk(C.ID_SKEL_MASK, pack_str(nm) + struct.pack('<I%dI' % len(words), len(words), *words)))
    for nm, j, words in chains:
        ck.children.append(Chunk(C.ID_SKEL_CHAIN, pack_str(nm) + struct.pack('<3II%dI' % len(words), j[0], j[1], j[2],
                                                                        len(words), *words)))
    return ck


# ------------------------------------------------------------------ vertex / triangle preparation
class SkinVertex(object):
    __slots__ = ('pos', 'nrm', 'uv', 'infl', 'src')

    def __init__(self, pos, nrm, uv, infl, src=0):
        self.pos, self.nrm, self.uv, self.infl, self.src = pos, nrm, uv, infl, src


class SkinGroup(object):
    def __init__(self, shader):
        self.shader = shader
        self.verts = []
        self.tris = []
        self.palette = []
        self.flags = 0x2191


def clean_influences(infl, max_inf=4, min_w=1e-4):
    lst = sorted(((j, w) for j, w in infl if w > min_w), key=lambda t: (-t[1], t[0]))[:max_inf]
    if not lst:
        return [(0, 1.0)]
    tot = sum(w for _, w in lst)
    return [(j, w / tot) for j, w in lst]


def orient_tris(verts, tris):
    """flip triangles whose winding disagrees with the vertex normals (game space convention)"""
    out = []
    for a, b, c in tris:
        pa, pb, pc = verts[a].pos, verts[b].pos, verts[c].pos
        e1 = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        e2 = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        fn = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
        s = 0.0
        for v in (a, b, c):
            n = verts[v].nrm
            s += fn[0] * n[0] + fn[1] * n[1] + fn[2] * n[2]
        out.append((a, b, c) if s >= 0 else (a, c, b))
    return out


def split_groups(shader, verts, tris, max_palette, flags=0x2191, max_inf=4):
    """split one material into groups that use at most max_palette joints; remaps vertices"""
    for v in verts:
        v.infl = clean_influences(v.infl, max_inf)
    tri_j = []
    for t in tris:
        s = set()
        for i in t:
            s.update(j for j, _ in verts[i].infl)
        tri_j.append(s)
    order = sorted(range(len(tris)), key=lambda k: (verts[tris[k][0]].infl[0][0], k))
    batches, cur, pal = [], [], set()
    for k in order:
        new = pal | tri_j[k]
        if cur and len(new) > max_palette:
            batches.append(cur)
            cur, pal = [], set()
            new = set(tri_j[k])
        cur.append(k)
        pal = new
    if cur:
        batches.append(cur)
    groups = []
    for b in batches:
        g = SkinGroup(shader)
        g.flags = flags
        remap = {}
        for k in b:
            nt = []
            for i in tris[k]:
                if i not in remap:
                    remap[i] = len(g.verts)
                    g.verts.append(verts[i])
                nt.append(remap[i])
            g.tris.append(tuple(nt))
        seen = []
        for v in g.verts:
            for j, _ in v.infl:
                if j not in seen:
                    seen.append(j)
        g.palette = seen
        groups.append(g)
    return groups


# ------------------------------------------------------------------ strips
def stripify(tris):
    """Greedy triangle strips.  Every strip is built so that its first triangle sits at an EVEN position:
    the triangle ending at strip position k is (v[k-2],v[k-1],v[k]) for even k and (v[k-1],v[k-2],v[k]) for odd k,
    which reproduces the given (oriented) triangles exactly."""
    edges = {}
    for t, (a, b, c) in enumerate(tris):
        for u, v, w in ((a, b, c), (b, c, a), (c, a, b)):
            edges.setdefault((u, v), []).append((t, w))
    used = [False] * len(tris)

    def grow(start_tri, rot, commit):
        a, b, c = tris[start_tri]
        if rot == 1:
            a, b, c = b, c, a
        elif rot == 2:
            a, b, c = c, a, b
        strip = [a, b, c]
        mine = set([start_tri])
        while True:
            m = len(strip) - 1
            k = m + 1
            u, v = (strip[m - 1], strip[m]) if (k & 1) == 0 else (strip[m], strip[m - 1])
            nxt = None
            for t, w in edges.get((u, v), ()):
                if not used[t] and t not in mine:
                    nxt = (t, w)
                    break
            if nxt is None:
                break
            mine.add(nxt[0])
            strip.append(nxt[1])
        if commit:
            for t in mine:
                used[t] = True
        return strip

    # start with triangles that have the fewest free neighbours (strip ends) for longer strips
    def free_neighbours(t):
        a, b, c = tris[t]
        n = 0
        for u, v in ((b, a), (c, b), (a, c)):
            n += len(edges.get((u, v), ()))
        return n
    order = sorted(range(len(tris)), key=lambda t: (free_neighbours(t), t))
    strips = []
    for t in order:
        if used[t]:
            continue
        best = None
        for rot in range(3):
            s = grow(t, rot, False)
            if best is None or len(s) > len(best[1]):
                best = (rot, s)
        strips.append(grow(t, best[0], True))
    return strips


def join_strips(strips):
    """one index list: strips joined with degenerate triangles, parity preserving (Xbox / index buffers)"""
    out = []
    for s in strips:
        if not out:
            out.extend(s)
            continue
        dups = [out[-1], s[0]] if (len(out) % 2) == 0 else [out[-1], s[0], s[0]]
        out.extend(dups)
        out.extend(s)
    return out


# ------------------------------------------------------------------ bounds
def bounds(verts):
    xs = [v.pos[0] for v in verts]
    ys = [v.pos[1] for v in verts]
    zs = [v.pos[2] for v in verts]
    mn = (min(xs), min(ys), min(zs))
    mx = (max(xs), max(ys), max(zs))
    c = ((mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0, (mn[2] + mx[2]) / 2.0)
    r = math.sqrt(sum(((mx[i] - mn[i]) / 2.0) ** 2 for i in range(3)))
    return mn, mx, c, r


def bbox_chunks(verts):
    mn, mx, c, r = bounds(verts)
    return [Chunk(0x10003, struct.pack('<6f', *(mn + mx))), Chunk(0x10004, struct.pack('<4f', c[0], c[1], c[2], r))]


# ------------------------------------------------------------------ Xbox group
def xbox_group_chunk(g, indices=None):
    n = len(g.verts)
    if indices is None:
        indices = join_strips(stripify(g.tris))
    pal = g.palette
    slot = dict((j, i) for i, j in enumerate(pal))
    ml = bytearray()
    wt = []
    for v in g.verts:
        infl = v.infl
        w = [0.0] * 4
        b = [0] * 4
        for k, (j, ww) in enumerate(infl[:4]):
            w[k] = ww
            b[3 - k] = slot[j]           # matrix bytes are stored in reverse order of the weights
        ml += bytes(b)
        wt.extend(w[:3])
    hdr = (struct.pack('<I', 0) + pack_str(g.shader) +
           struct.pack('<9I', 1, g.flags, n, len(indices), len(pal), 0, 1, 0, 0))
    ck = Chunk(C.ID_PRIMGROUP, hdr)
    ck.children = [
        Chunk(0x10011, b'\0'),
        Chunk(0x10005, struct.pack('<I%df' % (3 * n), n, *[c for v in g.verts for c in v.pos])),
        Chunk(0x10010, struct.pack('<I', n) + bytes([v.src & 0xff for v in g.verts])),
        Chunk(0x10006, struct.pack('<I%df' % (3 * n), n, *[c for v in g.verts for c in v.nrm])),
        Chunk(0x10007, struct.pack('<II%df' % (2 * n), n, 0, *[c for v in g.verts for c in v.uv])),
        Chunk(0x1000b, struct.pack('<I', n) + bytes(ml)),
        Chunk(0x1000c, struct.pack('<I%df' % (3 * n), n, *wt)),
        Chunk(0x1000d, struct.pack('<I%dI' % len(pal), len(pal), *pal)),
        Chunk(0x1000a, struct.pack('<I%dI' % len(indices), len(indices), *indices)),
    ]
    ck.children.extend(bbox_chunks(g.verts))
    return ck


# ------------------------------------------------------------------ PS2 group
PS2_PKT_INFO = bytes.fromhex('07001000010000001000000000000000000000000000000053000000')   # 0x10014 payload (28 bytes)


def ps2_capacity(nmat):
    """vertices per VIF packet: multiple of 6 that fits the VU1 double buffer (matches both groups of the original)"""
    n = (446 - 2 * nmat) // 8
    return max(6, (n // 6) * 6)


def ps2_pack_packets(g):
    """strips -> packets.  returns list of packets, each a list of (vertex_index, adc_flag)"""
    cap = ps2_capacity(len(g.palette))
    strips = stripify(g.tris)
    # split too long strips: keep parity (cut at even length) and restart with 2 flagged vertices
    parts = []
    for s in strips:
        if len(s) <= cap:
            parts.append(s)
            continue
        i = 0
        while i < len(s):
            chunk = s[i:i + cap]
            parts.append(chunk)
            i += cap - 2 - ((cap - 2) & 1)          # overlap by an even number (>=2) to keep triangles connected
            if len(s) - i < 3:
                break
    parts.sort(key=lambda p: -len(p))
    pk_list = []
    for p in parts:
        placed = False
        for pk in pk_list:
            pad = 1 if (len(pk) & 1) else 0            # a new strip must start on an even vertex
            if len(pk) + pad + len(p) <= cap:
                if pad:
                    pk.append((p[0], True))              # flagged filler, draws nothing
                pk.append((p[0], True))                  # the first two vertices of a restarted strip
                pk.append((p[1], True))                  # carry the ADC flag
                pk.extend((v, False) for v in p[2:])
                placed = True
                break
        if not placed:
            pk_list.append([(p[0], False), (p[1], False)] + [(v, False) for v in p[2:]])
    return pk_list, cap


def ps2_stream(packets, verts, palette, cap):
    """encode VIF packet stream (chunk 0x10012 payload).  packets: [[(vertex, adc)]]"""
    nmat = len(palette)
    slot = dict((j, i) for i, j in enumerate(palette))
    row = PS2_VU_MEM - 4 * nmat
    offset = 490 - 2 * nmat
    out = bytearray()

    def w(*words):
        for x in words:
            out.extend(struct.pack('<I', x & 0xffffffff))
    body = bytearray()

    def code(cmd, num=0, imm=0):
        return (cmd << 24) | (num << 16) | imm
    out_pkts = []
    for pi, pk in enumerate(packets):
        n = len(pk)
        b = bytearray()

        def bw(*words):
            for x in words:
                b.extend(struct.pack('<I', x & 0xffffffff))
        bw(code(0x6c, 3, 0x8000))
        bw(n, 3, 3 + 5 * cap, row - 27)
        bw(0, 0, 0, 0xbf800000)
        bw(n | 0x8000, 0x30024000, 0x412, 0)
        bw(code(0x01, 0, 0x0105))
        bw(0x20000000, 0xffffffa0)
        bw(code(0x74, n, 0x8003))
        for vi, adc in pk:
            u, v = verts[vi].uv
            ub, vb = struct.unpack('<2I', struct.pack('<2f', u, v))
            ub &= ~1
            vb &= ~1
            if adc:
                ub |= 1
                vb |= 1
            bw(ub, vb)
        bw(0x20000000, 0xffffff80)
        bw(code(0x79, n, 0x8004))
        nb = bytearray()
        for vi, _ in pk:
            nx, ny, nz = verts[vi].nrm
            nb += struct.pack('<3h', *[max(-32768, min(32767, int(round(c * 4096.0)))) for c in (nx, ny, nz)])
        nb += b'\0' * ((-len(nb)) & 3)
        b += nb
        bw(0x20000000, 0xffffff80)
        bw(code(0x78, n, 0x8005))
        for vi, _ in pk:
            b += struct.pack('<3f', *verts[vi].pos)
        bw(code(0x05, 0, 1))
        bw(0x30000000, row, row, row, row)
        bw(code(0x6e, n, 0xc006))
        for vi, _ in pk:
            infl = verts[vi].infl
            idx = [0, 0, 0, 0]
            for k, (j, _w) in enumerate(infl[:4]):
                idx[k] = slot[j] * 4
            used = max(1, min(4, len(infl)))
            idx[3] = idx[used - 1]
            b += bytes(idx)
        bw(code(0x05, 0, 0))
        bw(0x20000000, 0xffffffff)
        bw(code(0x6d, n, 0x8007))
        for vi, _ in pk:
            infl = verts[vi].infl
            a = [0, 0, 0, 0]
            prev = 0.0
            for k, (j, ww) in enumerate(infl[:4]):
                if k == 0:
                    a[0] = int(round(ww * 4096.0))
                else:
                    a[k] = int(round(min(1.0, ww / prev) * 4096.0)) if prev > 0 else 0
                prev = ww
            b += struct.pack('<4h', *a)
        bw(code(0x01, 0, 0x0101))
        bw(code(0x17) if pi > 0 else 0)
        out_pkts.append(bytes(b))
    prefix = bytearray()
    for x in (0x13000000, 0x03000011, 0x02000000 | offset, 0x31000000):
        prefix += struct.pack('<I', x)
    prefix += struct.pack('<4f', 1.0, 0.0, 0.0, 0.0)
    data = bytes(prefix) + b''.join(out_pkts)
    data += b'\0' * ((-len(data)) & 15)
    first = len(prefix) + (len(out_pkts[0]) if out_pkts else 0) - 4
    return struct.pack('<III', 0x00100007, first, len(data)) + data


def ps2_group_chunk(g):
    packets, cap = ps2_pack_packets(g)
    nv = sum(len(p) for p in packets) - 2 * len(packets) + 2
    hdr = (struct.pack('<I', 0) + pack_str(g.shader) +
           struct.pack('<9I', 1, g.flags, nv, 0, len(g.palette), 1, 1, 0, 0))
    ck = Chunk(C.ID_PRIMGROUP, hdr)
    ck.children = [
        Chunk(0x10011, b'\0'),
        Chunk(0x10014, PS2_PKT_INFO),
        Chunk(0x10012, ps2_stream(packets, g.verts, g.palette, cap)),
        Chunk(0x1000d, struct.pack('<I%dI' % len(g.palette), len(g.palette), *g.palette)),
    ]
    ck.children.extend(bbox_chunks(g.verts))
    return ck


# ------------------------------------------------------------------ GameCube group
GC_MAGIC = 0x00030003
_CHUNK_1001D = bytes.fromhex('01000000000100000000000000')


def gc_record(infl, slot):
    """8 byte weight record: count, slot0..2, weight0, weight1 (BE u16 / 32768); weight2 is the remainder"""
    infl = infl[:GC_MAX_INFLUENCES]
    tot = sum(w for _, w in infl) or 1.0
    infl = [(j, w / tot) for j, w in infl]
    n = len(infl)
    slots = [slot[j] for j, _ in infl] + [0] * (3 - n)
    if n == 1:
        w0, w1 = 0x8000, 0
    else:
        w0 = int(round(infl[0][1] * 32768.0))
        w1 = 0x8000 - w0 if n == 2 else int(round(infl[1][1] * 32768.0))
    return bytes([n] + slots) + struct.pack('>HH', min(w0, 0xffff), min(w1, 0xffff))


def gc_build_group(shader, flags, palette, recs, pos_ints, nrm_ints, tex_ints, cmds, width, bound_verts):
    """assemble a GameCube primitive group from already prepared arrays.

    recs      weight records (positions first, then normals), 8 bytes each
    pos_ints  [(x,y,z)] s16 / 4096     nrm_ints [(x,y,z)] s8 / 64      tex_ints [(u,v)] u16 / 32768
    cmds      [[(pos idx, nrm idx, uv idx)]] one GX_DRAW_TRIANGLE_STRIP command per list, vertex indices of `width` bytes
    """
    npos, nnrm, ntex = len(pos_ints), len(nrm_ints), len(tex_ints)
    arrays = bytearray()
    for r in recs:
        arrays += r
    posoff = len(arrays)
    arrays += struct.pack('>%dh' % (3 * npos), *[c for t in pos_ints for c in t])
    nrmoff = len(arrays)
    arrays += struct.pack('>%db' % (3 * nnrm), *[c for t in nrm_ints for c in t])
    texoff = len(arrays)
    arrays += struct.pack('>%dH' % (2 * ntex), *[c for t in tex_ints for c in t])
    vcd = 2 if width == 1 else 3
    fmt = (struct.pack('<II', 0, 0xff) + struct.pack('>II', len(recs), posoff) +
           struct.pack('>BBBBBBH', 9, vcd, 1, 3, 12, 6, npos) +
           struct.pack('>I', nrmoff) + struct.pack('>BBBBBBH', 10, vcd, 0, 1, 6, 3, nnrm) +
           struct.pack('>I', texoff) + struct.pack('>BBBBBBH', 13, vcd, 1, 2, 15, 4, ntex))
    dl = bytearray()
    total = 0
    for cmd in cmds:
        dl += bytes([0x98]) + struct.pack('>H', len(cmd))
        total += len(cmd)
        for p, n, t in cmd:
            if width == 1:
                dl += bytes([p, n, t])
            else:
                dl += struct.pack('>HHH', p, n, t)
    dl += b'\0' * ((-len(dl)) & 31)

    def blob(data):
        return struct.pack('<III', GC_MAGIC, len(data), len(data)) + bytes(data)
    hdr = (struct.pack('<I', 0) + pack_str(shader) + struct.pack('<9I', 1, flags, total, 0, len(palette), 1, 1, 0, 0))
    ck = Chunk(C.ID_PRIMGROUP, hdr)
    ck.children = [Chunk(0x10014, struct.pack('<III', GC_MAGIC, len(fmt), len(fmt)) + fmt),
                   Chunk(0x10012, blob(arrays)),
                   Chunk(0x10013, blob(dl)),
                   Chunk(0x1000d, struct.pack('<I%dI' % len(palette), len(palette), *palette))]
    ck.children.extend(bbox_chunks(bound_verts))
    return ck


def gc_group_chunk(g):
    slot = dict((j, i) for i, j in enumerate(g.palette))
    ipos, inrm, itex = {}, {}, {}
    pos_ints, nrm_ints, tex_ints, recs_p, recs_n = [], [], [], [], []
    corner = []
    lim = 32767.0 / 4096.0
    for v in g.verts:
        rec = gc_record(v.infl, slot)
        if max(abs(c) for c in v.pos) > lim:
            raise ValueError('vertex outside the +-8 unit range of the GameCube 16 bit position format')
        pq = tuple(int(round(c * 4096.0)) for c in v.pos)
        nq = tuple(max(-127, min(127, int(round(c * 64.0)))) for c in v.nrm)
        u, w = v.uv
        if not (0.0 <= u < 2.0 and 0.0 <= w < 2.0):
            WARNINGS.append('UV outside 0..2 clamped (GameCube stores unsigned 16 bit UVs)')
        tq = (max(0, min(65535, int(round(u * 32768.0)))), max(0, min(65535, int(round(w * 32768.0)))))
        kp, kn, kt = (pq, rec), (nq, rec), tq
        if kp not in ipos:
            ipos[kp] = len(pos_ints)
            pos_ints.append(pq)
            recs_p.append(rec)
        if kn not in inrm:
            inrm[kn] = len(nrm_ints)
            nrm_ints.append(nq)
            recs_n.append(rec)
        if kt not in itex:
            itex[kt] = len(tex_ints)
            tex_ints.append(tq)
        corner.append((ipos[kp], inrm[kn], itex[kt]))
    width = 2 if max(len(pos_ints), len(nrm_ints), len(tex_ints)) > 256 else 1
    cmds, cur = [], []
    for s in stripify(g.tris):
        if cur and len(cur) + len(s) + 3 > 65535:
            cmds.append(cur)
            cur = []
        if cur:
            cur_v = [corner[i] for i in s]
            parity_pad = (len(cur) % 2 == 0)
            cur.extend([cur[-1], cur_v[0]] if parity_pad else [cur[-1], cur_v[0], cur_v[0]])
            cur.extend(cur_v)
        else:
            cur = [corner[i] for i in s]
    if cur:
        cmds.append(cur)
    return gc_build_group(g.shader, g.flags, g.palette, recs_p + recs_n, pos_ints, nrm_ints, tex_ints, cmds, width, g.verts)


# ------------------------------------------------------------------ PolySkin
_CHUNK_10021_XBOX = struct.pack('<8I', 0, 32, 32, 32, 32, 32, 32, 32)
_CHUNK_10021_PS2 = struct.pack('<8I', 0, 32, 32, 32, 32, 16, 16, 32)


def polyskin_chunk(platform, name, skeleton_name, groups):
    """groups: [SkinGroup] -> 0x10001 chunk"""
    ps2 = (platform == 'PS2')
    gc = (platform == 'GC')
    ck = Chunk(C.ID_POLYSKIN, pack_str(name) + struct.pack('<I', 3) + pack_str(skeleton_name) +
               struct.pack('<I', len(groups)))
    ck.children.append(Chunk(0x10021, _CHUNK_10021_PS2 if ps2 else _CHUNK_10021_XBOX))
    for g in groups:
        ck.children.append(ps2_group_chunk(g) if ps2 else (gc_group_chunk(g) if gc else xbox_group_chunk(g)))
    ck.children.append(Chunk(0x122000, struct.pack('<If', 0, 0.5)))
    ck.children.append(Chunk(0x10017, struct.pack('<I', 1)))
    if gc:
        ck.children.append(Chunk(0x1001d, _CHUNK_1001D))
    allv = [v for g in groups for v in g.verts]
    if allv:
        ck.children.extend(bbox_chunks(allv))
    return ck


def build_groups(materials, max_palette, flags=0x2191, max_inf=4):
    """materials: [(shader, verts, tris)] -> [SkinGroup]"""
    del WARNINGS[:]
    out = []
    for shader, verts, tris in materials:
        tris = orient_tris(verts, tris)
        out.extend(split_groups(shader, verts, tris, max_palette, flags, max_inf))
    return out


# ------------------------------------------------------------------ helpers used by tests / conversion
def groups_from_decoded(skin):
    """decoded PolySkin (either platform) -> [SkinGroup] (game space)"""
    res = []
    for pg in skin.groups:
        g = SkinGroup(pg.shader)
        g.flags = pg.flags
        g.palette = list(pg.palette)
        tris = pg.triangles() if pg.platform == 'XBOX' else [tuple(pg.indices[i:i + 3]) for i in range(0, len(pg.indices), 3)]
        for i, p in enumerate(pg.positions):
            uv = pg.uvs[0][i] if pg.uvs else (0.0, 0.0)
            g.verts.append(SkinVertex(p, pg.normals[i] if pg.normals else (0, 1, 0), uv, pg.vertex_influences(i), i))
        g.tris = list(tris)
        res.append(g)
    return res
