# -*- coding: utf-8 -*-
"""
p3d_core.py  -  Pure3D (.p3d) reader / writer, Blender independent.

Layout of a file
    "P3D\xff" | u32 header_size (12) | u32 file_size | chunk*
Chunk
    u32 id | u32 data_size (header+payload) | u32 total_size (data + children) | payload | children

Animation related chunk ids (Radical Entertainment, Hulk: Ultimate Destruction flavour)
    0x121000 Animation        ver, name, fourcc type ('PTRN'...), numFrames f32, fps f32, cyclic u32
    0x121006 AnimationSize    ver, numGroups  (+ 0x121400 region count, 0x121007 per-channel-type key counts)
    0x121002 AnimationGroupList  ver, numGroups
    0x121001 AnimationGroup   ver, name, groupId(= skeleton joint index), numChannels
    0x1211xx channels (see CH_* below)
    0x121110 channel interpolation mode
    0x121401 region ("left_leg"/"right_leg" frame ranges), 0x121402 tail
Written only with the python standard library so it runs on every Blender version (Python 3.7+).
"""
import struct
import math

SIGNATURE = b'P3D\xff'


class P3DError(Exception):
    pass


# ---------------------------------------------------------------- chunk ids
ID_ANIMATION = 0x121000
ID_ANIM_GROUP = 0x121001
ID_ANIM_GROUP_LIST = 0x121002
ID_ANIM_SIZE = 0x121006
ID_ANIM_SIZE_ENTRY = 0x121007
ID_ANIM_REGION_HDR = 0x121400
ID_ANIM_REGION = 0x121401
ID_ANIM_TAIL = 0x121402
ID_CH_INTERP = 0x121110

CH_F1 = 0x121100      # float
CH_F2 = 0x121101      # float2
CH_V1 = 0x121102      # 1 DOF vector (mapping + constants)
CH_V2 = 0x121103      # 2 DOF vector
CH_V3 = 0x121104      # float3 (translation)
CH_QUAT = 0x121105    # float quaternion (x,y,z,w)
CH_STR = 0x121107     # string/entity (texture names...)
CH_BOOL = 0x121108    # visibility toggles
CH_COLOR = 0x121109   # u32 colours
CH_INT = 0x12110e     # u32 values
CH_CQ16 = 0x121112    # quaternion, 3 x int16 (w rebuilt)
CH_CQ8 = 0x121114     # quaternion, 3 x int8  (w rebuilt)

CHANNEL_IDS = (CH_F1, CH_F2, CH_V1, CH_V2, CH_V3, CH_QUAT, CH_STR, CH_BOOL,
               CH_COLOR, CH_INT, CH_CQ16, CH_CQ8)

CHANNEL_NAMES = {
    CH_F1: 'Float1', CH_F2: 'Float2', CH_V1: 'Vector1DOF', CH_V2: 'Vector2DOF',
    CH_V3: 'Vector3', CH_QUAT: 'Quaternion', CH_STR: 'String', CH_BOOL: 'Bool',
    CH_COLOR: 'Colour', CH_INT: 'Int', CH_CQ16: 'Quat16', CH_CQ8: 'Quat8',
}

ID_SKELETON = 0x23000
ID_SKEL_JOINT = 0x23001
ID_SKEL_MASK = 0x23002
ID_SKEL_CHAIN = 0x23003
ID_POLYSKIN = 0x10001
ID_PRIMGROUP = 0x10020


# ---------------------------------------------------------------- strings
def read_str(buf, o):
    n = buf[o]
    raw = bytes(buf[o + 1:o + 1 + n])
    i = raw.find(b'\0')
    if i >= 0:
        raw = raw[:i]
    return raw.decode('latin-1'), o + 1 + n


def pack_str(s):
    b = s.encode('latin-1', 'replace')
    n = (len(b) + 3) & ~3
    if n > 255:
        raise P3DError('string too long: %r' % s)
    return bytes([n]) + b + b'\0' * (n - len(b))


def fourcc_str(b):
    return b.split(b'\0')[0].decode('latin-1')


def fourcc_pack(s):
    return s.encode('latin-1')[:4].ljust(4, b'\0')


# ---------------------------------------------------------------- chunks
class Chunk(object):
    __slots__ = ('id', 'payload', 'children')

    def __init__(self, cid, payload=b'', children=None):
        self.id = cid
        self.payload = payload
        self.children = children if children is not None else []

    def total_size(self):
        return 12 + len(self.payload) + sum(c.total_size() for c in self.children)

    def write_into(self, out):
        dsz = 12 + len(self.payload)
        out += struct.pack('<III', self.id, dsz, self.total_size())
        out += self.payload
        for c in self.children:
            c.write_into(out)

    def find(self, cid):
        return [c for c in self.children if c.id == cid]

    def __repr__(self):
        return '<Chunk 0x%x payload=%d children=%d>' % (self.id, len(self.payload), len(self.children))


def parse_chunks(data, off, end):
    res = []
    while off + 12 <= end:
        cid, dsz, tsz = struct.unpack_from('<III', data, off)
        if dsz < 12 or tsz < dsz or off + tsz > end:
            raise P3DError('corrupt chunk 0x%x at offset %d (data=%d total=%d)' % (cid, off, dsz, tsz))
        payload = bytes(data[off + 12:off + dsz])
        kids = parse_chunks(data, off + dsz, off + tsz) if tsz > dsz else []
        res.append(Chunk(cid, payload, kids))
        off += tsz
    if off != end:
        raise P3DError('trailing bytes in chunk list')
    return res


# ---------------------------------------------------------------- quaternion quantisation
def quat_dequant(xi, yi, zi, bits):
    s = 32767.0 if bits == 16 else 127.0
    x, y, z = xi / s, yi / s, zi / s
    w = math.sqrt(max(0.0, 1.0 - x * x - y * y - z * z))
    return (x, y, z, w)


def quat_quant(x, y, z, w, bits):
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        x, y, z, w, n = 0.0, 0.0, 0.0, 1.0, 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    if w < 0.0:
        x, y, z = -x, -y, -z
    s = 32767 if bits == 16 else 127
    def q(v):
        i = int(round(v * s))
        return -s if i < -s else (s if i > s else i)
    return (q(x), q(y), q(z))


# ---------------------------------------------------------------- channels
class Channel(object):
    """One animation channel.

    kind    chunk id (CH_*)
    param   target parameter ('ROT', 'TRAN', 'VIS', ...) without NUL padding
    frames  list of int frame numbers (u16)
    values  per kind: float | (a,b) | (x,y,z) | (x,y,z,w) | int tuple for CQ | None (BOOL) | str | u32
    mapping/const  for V1/V2 (axis mapping and constant components)
    start   BOOL start state
    interp  (version, mode) of the 0x121110 child or None
    """
    __slots__ = ('kind', 'version', 'param', 'frames', 'values', 'mapping',
                 'const', 'start', 'interp', 'extra', 'raw')

    def __init__(self, kind, param='', frames=None, values=None, version=0):
        self.kind = kind
        self.version = version
        self.param = param
        self.frames = frames if frames is not None else []
        self.values = values if values is not None else []
        self.mapping = 0
        self.const = (0.0, 0.0, 0.0)
        self.start = 0
        self.interp = (0, 0xFFFFFFFF)
        self.extra = []      # unknown child chunks
        self.raw = None      # original Chunk when payload could not be decoded

    def num_keys(self):
        return len(self.frames)

    def size_entry(self):
        """number stored in AnimationSize (0x121007) for this channel"""
        if self.kind == CH_BOOL:
            # number of states: an implicit state at frame 0 counts when the first toggle is later
            return len(self.frames) + (1 if (not self.frames or self.frames[0] != 0) else 0)
        return len(self.frames)

    # -- quaternion helpers -------------------------------------------------
    def quats(self):
        """list of (x,y,z,w) floats for the three quaternion kinds"""
        if self.kind == CH_CQ16:
            return [quat_dequant(a, b, c, 16) for (a, b, c) in self.values]
        if self.kind == CH_CQ8:
            return [quat_dequant(a, b, c, 8) for (a, b, c) in self.values]
        if self.kind == CH_QUAT:
            return [tuple(v) for v in self.values]
        raise P3DError('not a quaternion channel')

    def vec3s(self):
        """translation channels expanded to (x,y,z) tuples (1/2/3 DOF)"""
        if self.kind == CH_V3:
            return [tuple(v) for v in self.values]
        if self.kind == CH_V1:
            out = []
            for v in self.values:
                t = list(self.const)
                t[self.mapping % 3] = v
                out.append(tuple(t))
            return out
        if self.kind == CH_V2:
            ax = V2_AXES.get(self.mapping, (1, 2))
            out = []
            for v in self.values:
                t = list(self.const)
                t[ax[0]] = v[0]
                t[ax[1]] = v[1]
                out.append(tuple(t))
            return out
        raise P3DError('not a vector channel')

    def copy(self):
        c = Channel(self.kind, self.param, list(self.frames), list(self.values), self.version)
        c.mapping, c.const, c.start, c.interp = self.mapping, self.const, self.start, self.interp
        c.extra = list(self.extra)
        c.raw = self.raw
        return c


# axes (indices into xyz) carried by a 2 DOF channel for each mapping value
V2_AXES = {0: (0, 1), 1: (0, 2), 2: (1, 2)}


def channel_from_chunk(ck):
    kind = ck.id
    p = ck.payload
    ch = None
    try:
        ver = struct.unpack_from('<I', p, 0)[0]
        param = fourcc_str(p[4:8])
        ch = Channel(kind, param, version=ver)
        o = 8
        if kind in (CH_V1, CH_V2):
            ch.mapping = struct.unpack_from('<H', p, o)[0]
            ch.const = struct.unpack_from('<3f', p, o + 2)
            o += 14
        elif kind == CH_BOOL:
            ch.start = struct.unpack_from('<H', p, o)[0]
            o += 2
        nk = struct.unpack_from('<I', p, o)[0]
        o += 4
        ch.frames = list(struct.unpack_from('<%dH' % nk, p, o))
        o += 2 * nk
        if kind == CH_F1 or kind == CH_V1:
            ch.values = list(struct.unpack_from('<%df' % nk, p, o)); o += 4 * nk
        elif kind in (CH_F2, CH_V2):
            fl = struct.unpack_from('<%df' % (2 * nk), p, o); o += 8 * nk
            ch.values = [(fl[2 * i], fl[2 * i + 1]) for i in range(nk)]
        elif kind == CH_V3:
            fl = struct.unpack_from('<%df' % (3 * nk), p, o); o += 12 * nk
            ch.values = [(fl[3 * i], fl[3 * i + 1], fl[3 * i + 2]) for i in range(nk)]
        elif kind == CH_QUAT:
            fl = struct.unpack_from('<%df' % (4 * nk), p, o); o += 16 * nk
            ch.values = [tuple(fl[4 * i:4 * i + 4]) for i in range(nk)]
        elif kind == CH_CQ16:
            it = struct.unpack_from('<%dh' % (3 * nk), p, o); o += 6 * nk
            ch.values = [tuple(it[3 * i:3 * i + 3]) for i in range(nk)]
        elif kind == CH_CQ8:
            it = struct.unpack_from('<%db' % (3 * nk), p, o); o += 3 * nk
            ch.values = [tuple(it[3 * i:3 * i + 3]) for i in range(nk)]
        elif kind in (CH_COLOR, CH_INT):
            ch.values = list(struct.unpack_from('<%dI' % nk, p, o)); o += 4 * nk
        elif kind == CH_BOOL:
            ch.values = [None] * nk
        elif kind == CH_STR:
            vals = []
            for _ in range(nk):
                s, o = read_str(p, o)
                vals.append(s)
            ch.values = vals
        else:
            raise P3DError('unknown channel kind')
        if o != len(p):
            raise P3DError('channel size mismatch')
    except (struct.error, P3DError):
        bad = Channel(kind, '', [], [])
        bad.raw = ck
        return bad
    ch.interp = None
    for c in ck.children:
        if c.id == ID_CH_INTERP and len(c.payload) == 8:
            ch.interp = struct.unpack('<II', c.payload)
        else:
            ch.extra.append(c)
    return ch


def channel_to_chunk(ch):
    if ch.raw is not None:
        return ch.raw
    kind = ch.kind
    nk = len(ch.frames)
    p = bytearray(struct.pack('<I4s', ch.version, fourcc_pack(ch.param)))
    if kind in (CH_V1, CH_V2):
        p += struct.pack('<H3f', ch.mapping, *ch.const)
    elif kind == CH_BOOL:
        p += struct.pack('<H', ch.start)
    p += struct.pack('<I', nk)
    p += struct.pack('<%dH' % nk, *ch.frames)
    v = ch.values
    if kind in (CH_F1, CH_V1):
        p += struct.pack('<%df' % nk, *v)
    elif kind in (CH_F2, CH_V2, CH_V3, CH_QUAT):
        w = {CH_F2: 2, CH_V2: 2, CH_V3: 3, CH_QUAT: 4}[kind]
        p += struct.pack('<%df' % (w * nk), *[c for t in v for c in t])
    elif kind == CH_CQ16:
        p += struct.pack('<%dh' % (3 * nk), *[c for t in v for c in t])
    elif kind == CH_CQ8:
        p += struct.pack('<%db' % (3 * nk), *[c for t in v for c in t])
    elif kind in (CH_COLOR, CH_INT):
        p += struct.pack('<%dI' % nk, *v)
    elif kind == CH_STR:
        for s in v:
            p += pack_str(s)
    kids = []
    if ch.interp is not None:
        kids.append(Chunk(ID_CH_INTERP, struct.pack('<II', *ch.interp)))
    kids.extend(ch.extra)
    return Chunk(kind, bytes(p), kids)


# ---------------------------------------------------------------- animation
class Group(object):
    __slots__ = ('name', 'group_id', 'version', 'channels')

    def __init__(self, name='', group_id=0, version=0):
        self.name = name
        self.group_id = group_id
        self.version = version
        self.channels = []

    def channel(self, param, kinds=None):
        for c in self.channels:
            if c.param == param and (kinds is None or c.kind in kinds):
                return c
        return None


class Region(object):
    """0x121401 named frame ranges (foot-plant / leg-lock regions)"""
    __slots__ = ('name', 'version', 'ranges', 'raw')

    def __init__(self, name='', ranges=None, version=0):
        self.name = name
        self.version = version
        self.ranges = ranges if ranges is not None else []
        self.raw = None


class Animation(object):
    def __init__(self, name='', anim_type='PTRN', num_frames=1.0, fps=30.0, cyclic=0):
        self.version = 0
        self.name = name
        self.anim_type = anim_type
        self.num_frames = float(num_frames)
        self.fps = float(fps)
        self.cyclic = int(cyclic)
        self.hdr_tail = b''
        self.size_version = 0
        self.list_version = 0
        self.groups = []
        self.regions = []
        self.extra = []
        self.tail = struct.pack('<II', 0, 0)
        self.has_tail = True

    # ---- helpers
    def num_channels(self):
        return sum(len(g.channels) for g in self.groups)

    def group_names(self):
        return [g.name for g in self.groups]

    def group(self, name):
        for g in self.groups:
            if g.name == name:
                return g
        return None

    def copy(self):
        a = Animation(self.name, self.anim_type, self.num_frames, self.fps, self.cyclic)
        a.version, a.hdr_tail = self.version, self.hdr_tail
        a.size_version, a.list_version = self.size_version, self.list_version
        for g in self.groups:
            ng = Group(g.name, g.group_id, g.version)
            ng.channels = [c.copy() for c in g.channels]
            a.groups.append(ng)
        for r in self.regions:
            nr = Region(r.name, [tuple(x) for x in r.ranges], r.version)
            nr.raw = r.raw
            a.regions.append(nr)
        a.extra = list(self.extra)
        a.tail, a.has_tail = self.tail, self.has_tail
        return a

    # ---- decode
    @classmethod
    def from_chunk(cls, ck):
        p = ck.payload
        ver = struct.unpack_from('<I', p, 0)[0]
        name, o = read_str(p, 4)
        typ = fourcc_str(p[o:o + 4])
        nf, fps, cyc = struct.unpack_from('<ffI', p, o + 4)
        a = cls(name, typ, nf, fps, cyc)
        a.version = ver
        a.hdr_tail = bytes(p[o + 16:])
        for c in ck.children:
            if c.id == ID_ANIM_SIZE:
                a.size_version = struct.unpack_from('<I', c.payload, 0)[0]
            elif c.id == ID_ANIM_GROUP_LIST:
                a.list_version = struct.unpack_from('<I', c.payload, 0)[0]
                for g in c.children:
                    if g.id != ID_ANIM_GROUP:
                        a.extra.append(g)
                        continue
                    gp = g.payload
                    gver = struct.unpack_from('<I', gp, 0)[0]
                    gname, q = read_str(gp, 4)
                    gid = struct.unpack_from('<I', gp, q)[0]
                    grp = Group(gname, gid, gver)
                    grp.channels = [channel_from_chunk(x) for x in g.children]
                    a.groups.append(grp)
            elif c.id == ID_ANIM_REGION:
                a.regions.append(_region_from_chunk(c))
            elif c.id == ID_ANIM_TAIL:
                a.tail = c.payload
            else:
                a.extra.append(c)
        return a

    # ---- encode
    def to_chunk(self):
        payload = (struct.pack('<I', self.version) + pack_str(self.name) +
                   fourcc_pack(self.anim_type) +
                   struct.pack('<ffI', self.num_frames, self.fps, self.cyclic) + self.hdr_tail)
        root = Chunk(ID_ANIMATION, payload)
        # AnimationSize (regenerated, always consistent with the channel data)
        size = Chunk(ID_ANIM_SIZE, struct.pack('<II', self.size_version, len(self.groups)))
        if self.regions:
            size.children.append(Chunk(ID_ANIM_REGION_HDR, struct.pack('<II', 0, len(self.regions))))
        per_kind = {}
        for g in self.groups:
            for c in g.channels:
                if c.raw is None:
                    per_kind.setdefault(c.kind, []).append(c.size_entry())
        for kind in sorted(per_kind):
            lst = per_kind[kind]
            size.children.append(Chunk(ID_ANIM_SIZE_ENTRY,
                                       struct.pack('<III', 0, kind, len(lst)) +
                                       struct.pack('<%dH' % len(lst), *[min(v, 65535) for v in lst])))
        root.children.append(size)
        gl = Chunk(ID_ANIM_GROUP_LIST, struct.pack('<II', self.list_version, len(self.groups)))
        for g in self.groups:
            gc = Chunk(ID_ANIM_GROUP, struct.pack('<I', g.version) + pack_str(g.name) +
                       struct.pack('<II', g.group_id, len(g.channels)))
            gc.children = [channel_to_chunk(c) for c in g.channels]
            gl.children.append(gc)
        gl.children.extend(self.extra)
        root.children.append(gl)
        for r in self.regions:
            root.children.append(_region_to_chunk(r))
        if self.has_tail:
            root.children.append(Chunk(ID_ANIM_TAIL, self.tail))
        return root


def _region_from_chunk(c):
    p = c.payload
    r = Region()
    try:
        r.name, o = read_str(p, 0)
        r.version, n = struct.unpack_from('<II', p, o)
        fl = struct.unpack_from('<%df' % (2 * n), p, o + 8)
        if o + 8 + 8 * n != len(p):
            raise struct.error('size')
        r.ranges = [(fl[2 * i], fl[2 * i + 1]) for i in range(n)]
    except struct.error:
        r = Region()
        r.raw = c
    return r


def _region_to_chunk(r):
    if r.raw is not None:
        return r.raw
    p = pack_str(r.name) + struct.pack('<II', r.version, len(r.ranges))
    for a, b in r.ranges:
        p += struct.pack('<ff', a, b)
    return Chunk(ID_ANIM_REGION, p)


# ---------------------------------------------------------------- skeleton
class Joint(object):
    __slots__ = ('name', 'parent', 'rest')

    def __init__(self, name, parent, rest):
        self.name = name
        self.parent = parent
        self.rest = tuple(rest)     # 16 floats, row-major, row-vector convention (translation = last row)


class Skeleton(object):
    def __init__(self):
        self.name = ''
        self.joints = []
        self.masks = []      # (name, [words])
        self.chains = []     # (name, [3 joint ids], [words])
        self.chunk = None

    @classmethod
    def from_chunk(cls, ck):
        s = cls()
        s.chunk = ck
        s.name, _ = read_str(ck.payload, 0)
        for c in ck.children:
            p = c.payload
            if c.id == ID_SKEL_JOINT:
                nm, o = read_str(p, 0)
                par = struct.unpack_from('<I', p, o)[0]
                rest = struct.unpack_from('<16f', p, o + 4)
                s.joints.append(Joint(nm, par, rest))
            elif c.id == ID_SKEL_MASK:
                nm, o = read_str(p, 0)
                n = struct.unpack_from('<I', p, o)[0]
                s.masks.append((nm, list(struct.unpack_from('<%dI' % n, p, o + 4))))
            elif c.id == ID_SKEL_CHAIN:
                nm, o = read_str(p, 0)
                j = list(struct.unpack_from('<3I', p, o))
                n = struct.unpack_from('<I', p, o + 12)[0]
                s.chains.append((nm, j, list(struct.unpack_from('<%dI' % n, p, o + 16))))
        return s

    def index_of(self, name):
        for i, j in enumerate(self.joints):
            if j.name == name:
                return i
        return -1


# ---------------------------------------------------------------- poly skin
class PrimGroup(object):
    def __init__(self):
        self.shader = ''
        self.prim_type = 0
        self.flags = 0
        self.positions = []
        self.normals = []
        self.uvs = []          # list of uv sets, each a list of (u,v)
        self.indices = []
        self.matrix_idx = []   # per vertex 4 palette indices (reverse order of weights!)
        self.weights = []      # per vertex 3 floats (4th = 1 - sum)
        self.palette = []      # palette index -> skeleton joint index
        self.influences = None  # PS2: per vertex [(joint, weight)] decoded directly
        self.platform = 'XBOX'

    def vertex_influences(self, v):
        if self.influences is not None:
            return self.influences[v]
        """[(joint_index, weight)] for vertex v.

        The matrix list stores the palette indices in the opposite order of the weights:
        weight k belongs to matrix byte (3 - k).  (verified against bind pose proximity)"""
        w = self.weights[v]
        w4 = (w[0], w[1], w[2], 1.0 - w[0] - w[1] - w[2])
        mi = self.matrix_idx[v]
        res = {}
        for k in range(4):
            if w4[k] > 1e-5:
                pi = mi[3 - k]
                j = self.palette[pi] if pi < len(self.palette) else 0
                res[j] = res.get(j, 0.0) + w4[k]
        return sorted(res.items())

    def triangles(self):
        """index triples (game winding) for triangle list (0) or strip (1)"""
        idx = self.indices
        tris = []
        if self.prim_type == 0:
            for i in range(0, len(idx) - 2, 3):
                tris.append((idx[i], idx[i + 1], idx[i + 2]))
        elif self.prim_type == 1:
            for i in range(len(idx) - 2):
                a, b, c = idx[i], idx[i + 1], idx[i + 2]
                if a == b or b == c or a == c:
                    continue
                tris.append((a, b, c) if (i & 1) == 0 else (b, a, c))
        return tris


# ---------------------------------------------------------------- PS2 skin (VIF packet stream, chunk 0x10012)
_VIF_UNPACK = {0x0: (1, 32), 0x1: (1, 16), 0x2: (1, 8), 0x4: (2, 32), 0x5: (2, 16), 0x6: (2, 8),
               0x8: (3, 32), 0x9: (3, 16), 0xa: (3, 8), 0xc: (4, 32), 0xd: (4, 16), 0xe: (4, 8), 0xf: (1, 16)}
ID_PS2_PACKET = 0x10012


def vif_unpacks(p, start=12):
    """yield (vu_address, count, data_offset) for every UNPACK of a PS2 VIF stream"""
    o = start
    n = len(p)
    while o + 4 <= n:
        w = struct.unpack_from('<I', p, o)[0]
        cmd, num, imm = (w >> 24) & 0xff, (w >> 16) & 0xff, w & 0xffff
        o += 4
        if cmd >= 0x60:
            comps, bits = _VIF_UNPACK.get(cmd & 0xf, (4, 32))
            size = (comps * bits * num + 7) // 8
            size = (size + 3) & ~3
            yield imm & 0x3ff, num, o
            o += size
        elif cmd in (0x30, 0x31):
            o += 16
        elif cmd == 0x20:
            o += 4
        elif cmd == 0x4a:
            o += (num or 256) * 8
        elif cmd in (0x50, 0x51):
            o += imm * 16


def decode_ps2_group(ck, g):
    """Fill PrimGroup g from a PS2 primitive group chunk.

    Every VIF packet holds up to 48-54 vertices of ONE triangle strip:
        addr 0  header (vertex count, GIF tag)
        addr 3  V2-32  uv (float)  -- the lowest mantissa bit of u AND v is the GS 'ADC' flag (no triangle drawn)
        addr 4  V3-16  normal / 4096
        addr 5  V3-32  position (float)
        addr 6  V4-8   matrix slot * 4  (4 influences, palette index = byte / 4)
        addr 7  V4-16  chained weights / 4096: w0 = a0, w(k) = w(k-1) * a(k)
    """
    pk = None
    for k in ck.children:
        if k.id == 0x1000d and len(k.payload) >= 4:
            n = struct.unpack_from('<I', k.payload, 0)[0]
            g.palette = list(struct.unpack_from('<%dI' % n, k.payload, 4))
        elif k.id == ID_PS2_PACKET:
            pk = k.payload
    g.platform = 'PS2'
    g.prim_type = 0
    if pk is None:
        return g
    packets = []
    cur = None
    for addr, num, o in vif_unpacks(pk):
        if addr == 0:
            cur = {}
            packets.append(cur)
        elif cur is None:
            continue
        elif addr == 3:
            cur['uv'] = struct.unpack_from('<%dI' % (2 * num), pk, o)
        elif addr == 4:
            cur['nrm'] = struct.unpack_from('<%dh' % (3 * num), pk, o)
        elif addr == 5:
            cur['pos'] = struct.unpack_from('<%df' % (3 * num), pk, o)
        elif addr == 6:
            cur['idx'] = pk[o:o + 4 * num]
        elif addr == 7:
            cur['wt'] = struct.unpack_from('<%dh' % (4 * num), pk, o)
    weld = {}
    for x in packets:
        if not all(k in x for k in ('uv', 'nrm', 'pos', 'idx', 'wt')):
            continue
        n = len(x['pos']) // 3
        ids = []
        flags = []
        for v in range(n):
            ub, vb = x['uv'][2 * v], x['uv'][2 * v + 1]
            flags.append(bool(ub & 1) and bool(vb & 1))
            ub &= ~1
            vb &= ~1
            key = (x['pos'][3 * v:3 * v + 3], ub, vb, x['nrm'][3 * v:3 * v + 3],
                   x['idx'][4 * v:4 * v + 4], x['wt'][4 * v:4 * v + 4])
            i = weld.get(key)
            if i is None:
                i = weld[key] = len(g.positions)
                g.positions.append(tuple(key[0]))
                uv = struct.unpack('<2f', struct.pack('<2I', ub, vb))
                if not g.uvs:
                    g.uvs.append([])
                g.uvs[0].append(uv)
                g.normals.append(tuple(c / 4096.0 for c in key[3]))
                ib, wb = key[4], key[5]
                infl = {}
                w = 0.0
                for k in range(4):
                    a = wb[k] / 4096.0
                    w = a if k == 0 else w * a
                    if w <= 1e-5:
                        break
                    pi = ib[k] // 4
                    j = g.palette[pi] if pi < len(g.palette) else 0
                    infl[j] = infl.get(j, 0.0) + w
                tot = sum(infl.values())
                if tot > 0.0:                 # 1/4096 fixed point -> renormalise
                    infl = dict((j, w / tot) for j, w in infl.items())
                if g.influences is None:
                    g.influences = []
                g.influences.append(sorted(infl.items()))
            ids.append(i)
        for k in range(2, n):
            if flags[k]:
                continue                      # ADC: this kick draws nothing
            a, b, c = ids[k - 2], ids[k - 1], ids[k]
            if a == b or b == c or a == c:
                continue
            pa, pb, pc = g.positions[a], g.positions[b], g.positions[c]
            e1 = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
            e2 = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
            fn = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            vn = [sum(g.normals[t][q] for t in (a, b, c)) for q in range(3)]
            if fn[0] * vn[0] + fn[1] * vn[1] + fn[2] * vn[2] < 0:
                b, c = c, b                   # winding consistent with the stored normals
            g.indices.extend((a, b, c))
    return g


# ---------------------------------------------------------------- GameCube skin (display list + vertex arrays)
ID_GC_ARRAYS = 0x10012      # shares the id with the PS2 packet stream, told apart by the leading magic
ID_GC_DISPLAY = 0x10013
ID_GC_FORMAT = 0x10014
GC_MAGIC = 0x00030003
PS2_MAGIC = 0x00100007


def gc_parse_format(d):
    """0x10014 payload (without the 12 byte chunk header) -> dict"""
    f = {'nrec': struct.unpack_from('>I', d, 8)[0]}
    o = 12
    for name in ('pos', 'nrm', 'tex'):
        off = struct.unpack_from('>I', d, o)[0]
        attr, vcd, cnt, typ, frac, stride, count = struct.unpack_from('>BBBBBBH', d, o + 4)
        f[name] = dict(offset=off, attr=attr, vcd=vcd, cnt=cnt, type=typ, frac=frac, stride=stride, count=count)
        o += 12
    return f


def decode_gc_group(ck, g):
    """Fill PrimGroup g from a GameCube primitive group.

    0x10014 vertex format: 2 u32, number of weight records, then for position / normal / uv0 an array description
        offset(BE u32) | GX attr | GX_INDEX8(2)/INDEX16(3) | count code | component type | fraction bits | stride | count(BE u16)
    0x10012 arrays: [weight records (npos + nnrm) x 8 bytes][positions s16 / 2^12][normals s8 / 2^6][uv u16 / 2^15]
        record = count, slot0, slot1, slot2, weight0 (BE u16 / 32768), weight1 ; weight2 = remainder
        (records 0..npos-1 belong to positions, the following ones to normals)
    0x10013 display list: GX_DRAW_TRIANGLE_STRIP (0x98) commands, vertex = (pos idx, nrm idx, uv idx), big endian
    """
    kd = dict((k.id, k) for k in ck.children)
    g.platform = 'GC'
    g.prim_type = 0
    if 0x1000d in kd and len(kd[0x1000d].payload) >= 4:
        n = struct.unpack_from('<I', kd[0x1000d].payload, 0)[0]
        g.palette = list(struct.unpack_from('<%dI' % n, kd[0x1000d].payload, 4))
    if ID_GC_FORMAT not in kd or ID_GC_ARRAYS not in kd or ID_GC_DISPLAY not in kd:
        return g
    fmt = gc_parse_format(kd[ID_GC_FORMAT].payload[12:])
    arr = kd[ID_GC_ARRAYS].payload[12:]
    dl = kd[ID_GC_DISPLAY].payload[12:]
    P, N, T = fmt['pos'], fmt['nrm'], fmt['tex']
    pos = struct.unpack_from('>%dh' % (3 * P['count']), arr, P['offset'])
    nrm = struct.unpack_from('>%db' % (3 * N['count']), arr, N['offset'])
    tfmt = '>%dh' if T['type'] == 3 else '>%dH'
    tex = struct.unpack_from(tfmt % (2 * T['count']), arr, T['offset'])
    ps, ns, ts = 1.0 / (1 << P['frac']), 1.0 / (1 << N['frac']), 1.0 / (1 << T['frac'])
    infl = []
    for i in range(P['count']):
        r = arr[8 * i:8 * i + 8]
        cnt = r[0]
        w0, w1 = struct.unpack_from('>HH', r, 4)
        ws = [w0 / 32768.0, w1 / 32768.0]
        ws.append(max(0.0, 1.0 - ws[0] - ws[1]))
        d = {}
        for k in range(min(cnt, 3)):
            j = g.palette[r[1 + k]] if r[1 + k] < len(g.palette) else 0
            d[j] = d.get(j, 0.0) + (ws[k] if cnt > 1 else 1.0)
        tot = sum(d.values())
        infl.append(sorted((j, w / tot) for j, w in d.items()) if tot > 0 else [(0, 1.0)])
    widths = [1 if x['vcd'] == 2 else 2 for x in (P, N, T)]
    rec = sum(widths)
    weld = {}
    o = 0
    while o < len(dl):
        cmd = dl[o]
        if cmd == 0:
            o += 1
            continue
        n = struct.unpack_from('>H', dl, o + 1)[0]
        o += 3
        ids = []
        for v in range(n):
            q = o + v * rec
            idx = []
            for w in widths:
                idx.append(dl[q] if w == 1 else (dl[q] << 8) | dl[q + 1])
                q += w
            key = tuple(idx)
            i = weld.get(key)
            if i is None:
                i = weld[key] = len(g.positions)
                p, nn, tt = idx
                g.positions.append((pos[3 * p] * ps, pos[3 * p + 1] * ps, pos[3 * p + 2] * ps))
                g.normals.append((nrm[3 * nn] * ns, nrm[3 * nn + 1] * ns, nrm[3 * nn + 2] * ns))
                if not g.uvs:
                    g.uvs.append([])
                g.uvs[0].append((tex[2 * tt] * ts, tex[2 * tt + 1] * ts))
                if g.influences is None:
                    g.influences = []
                g.influences.append(infl[p])
            ids.append(i)
        o += n * rec
        if cmd & 0xf8 == 0x98:         # triangle strip
            for k in range(2, n):
                a, b, c = ids[k - 2], ids[k - 1], ids[k]
                if a == b or b == c or a == c:
                    continue
                g.indices.extend(_orient_tri(g, a, b, c))
        elif cmd & 0xf8 == 0x90:       # triangle list
            for k in range(0, n - 2, 3):
                a, b, c = ids[k], ids[k + 1], ids[k + 2]
                if a != b and b != c and a != c:
                    g.indices.extend(_orient_tri(g, a, b, c))
    return g


def _orient_tri(g, a, b, c):
    pa, pb, pc = g.positions[a], g.positions[b], g.positions[c]
    e1 = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
    e2 = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
    fn = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
    vn = [sum(g.normals[t][q] for t in (a, b, c)) for q in range(3)]
    return (a, c, b) if fn[0] * vn[0] + fn[1] * vn[1] + fn[2] * vn[2] < 0 else (a, b, c)


class PolySkin(object):
    def __init__(self):
        self.name = ''
        self.skeleton_name = ''
        self.groups = []

    @property
    def platform(self):
        return self.groups[0].platform if self.groups else 'XBOX'

    @classmethod
    def from_chunk(cls, ck):
        s = cls()
        p = ck.payload
        s.name, o = read_str(p, 0)
        o += 4
        s.skeleton_name, o = read_str(p, o)
        for c in ck.children:
            if c.id != ID_PRIMGROUP:
                continue
            g = PrimGroup()
            q = c.payload
            g.shader, o2 = read_str(q, 4)
            g.prim_type, g.flags, nv, ni, nm = struct.unpack_from('<5I', q, o2)
            arrays = [k for k in c.children if k.id == ID_PS2_PACKET]
            if arrays:
                magic = struct.unpack_from('<I', arrays[0].payload, 0)[0] if len(arrays[0].payload) >= 4 else 0
                if magic == GC_MAGIC:
                    decode_gc_group(c, g)
                else:
                    decode_ps2_group(c, g)
                s.groups.append(g)
                continue
            for k in c.children:
                kp = k.payload
                if k.id not in (0x10005, 0x10006, 0x10007, 0x1000b, 0x1000c, 0x1000d, 0x1000a):
                    continue
                n = struct.unpack_from('<I', kp, 0)[0]
                if k.id == 0x10005:
                    fl = struct.unpack_from('<%df' % (3 * n), kp, 4)
                    g.positions = [tuple(fl[3 * i:3 * i + 3]) for i in range(n)]
                elif k.id == 0x10006:
                    fl = struct.unpack_from('<%df' % (3 * n), kp, 4)
                    g.normals = [tuple(fl[3 * i:3 * i + 3]) for i in range(n)]
                elif k.id == 0x10007:
                    fl = struct.unpack_from('<%df' % (2 * n), kp, 8)
                    g.uvs.append([(fl[2 * i], fl[2 * i + 1]) for i in range(n)])
                elif k.id == 0x1000b:
                    g.matrix_idx = [tuple(kp[4 + 4 * i:8 + 4 * i]) for i in range(n)]
                elif k.id == 0x1000c:
                    fl = struct.unpack_from('<%df' % (3 * n), kp, 4)
                    g.weights = [tuple(fl[3 * i:3 * i + 3]) for i in range(n)]
                elif k.id == 0x1000d:
                    g.palette = list(struct.unpack_from('<%dI' % n, kp, 4))
                elif k.id == 0x1000a:
                    g.indices = list(struct.unpack_from('<%dI' % n, kp, 4))
            s.groups.append(g)
        return s


# ---------------------------------------------------------------- file
class P3DFile(object):
    def __init__(self):
        self.header_size = 12
        self.header_extra = b''
        self.items = []        # Chunk | Animation, original order

    # -- io
    @classmethod
    def parse(cls, data):
        data = bytes(data)
        if len(data) < 12:
            raise P3DError('file too small')
        if data[:4] != SIGNATURE:
            if data[:4] == b'P3DZ':
                raise P3DError('compressed (P3DZ) files are not supported')
            raise P3DError('not a Pure3D file (bad signature %r)' % data[:4])
        hs, total = struct.unpack_from('<II', data, 4)
        if hs < 12 or hs > len(data):
            raise P3DError('bad header size')
        f = cls()
        f.header_size = hs
        f.header_extra = data[12:hs]
        end = min(total, len(data)) if total >= hs else len(data)
        for ck in parse_chunks(data, hs, end):
            if ck.id == ID_ANIMATION:
                try:
                    f.items.append(Animation.from_chunk(ck))
                    continue
                except (struct.error, P3DError, IndexError):
                    pass
            f.items.append(ck)
        return f

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as fh:
            return cls.parse(fh.read())

    def to_bytes(self):
        body = bytearray()
        for it in self.items:
            ck = it.to_chunk() if isinstance(it, Animation) else it
            ck.write_into(body)
        head = SIGNATURE + struct.pack('<II', self.header_size, self.header_size + len(body)) + self.header_extra
        return bytes(head) + bytes(body)

    def save(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())

    # -- views
    @property
    def animations(self):
        return [i for i in self.items if isinstance(i, Animation)]

    def other_chunks(self):
        return [i for i in self.items if not isinstance(i, Animation)]

    def skeleton(self):
        for i in self.items:
            if isinstance(i, Chunk) and i.id == ID_SKELETON:
                return Skeleton.from_chunk(i)
        return None

    def polyskin(self):
        for i in self.items:
            if isinstance(i, Chunk) and i.id == ID_POLYSKIN:
                return PolySkin.from_chunk(i)
        return None

    def unique_name(self, name, ignore=None):
        used = set(a.name for a in self.animations if a is not ignore)
        if name not in used:
            return name
        i = 1
        while '%s.%03d' % (name, i) in used:
            i += 1
        return '%s.%03d' % (name, i)

    def add_animation(self, anim, replace=True):
        """append anim; replaces an animation with the same name when replace=True"""
        if replace:
            for k, it in enumerate(self.items):
                if isinstance(it, Animation) and it.name == anim.name:
                    self.items[k] = anim
                    return anim
        else:
            anim.name = self.unique_name(anim.name)
        # keep animations before skeleton/skin chunks when possible
        self.items.append(anim)
        return anim


def clone_shader_chunk(ck, new_name):
    """copy of a shader chunk (0x11000) under another name (children are shared, they are never mutated)"""
    _, o = read_str(ck.payload, 0)
    return Chunk(ck.id, pack_str(new_name) + ck.payload[o:], list(ck.children))


def new_animation_file(anims, extra_chunks=()):
    f = P3DFile()
    f.items = list(anims) + list(extra_chunks)
    return f
