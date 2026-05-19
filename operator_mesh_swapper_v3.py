"""
OPERATOR Mesh Swapper v3 — Pipeline-Aware Coordinate Engine
============================================================
Major improvements over v2:
  - Coordinate Conversion Presets (AssetStudio+Blender, Raw OBJ, Unity Native, Custom)
  - Optional Triangle Winding Reversal checkbox
  - Axis Flip toggles (X/Y/Z) for positions, normals, tangents
  - Tangent Preservation mode (copy original bytes, no regeneration)
  - Mesh Validation Panel (shows first 5 pos/nrm/tan + handedness)
  - OBJ Roundtrip Export (export reconstructed buffer as OBJ for debugging)
  - About tab with Buy Me a Coffee support button
  - All v2 channel-aware buffer building preserved and extended
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import struct
import math
import os
import webbrowser
import traceback

try:
    import UnityPy
    import UnityPy.config
    from UnityPy.enums import ClassIDType
    UNITYPY_OK = True
except ImportError:
    UNITYPY_OK = False

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.3.8f1"

# ── Unity vertex format enums ────────────────────────────────────────────────
VERTEX_FORMAT_SIZE = {
    0: 4,   # Float32
    1: 2,   # Float16
    2: 4,   # UNorm8
    3: 4,   # SNorm8
    4: 4,   # UNorm16
    5: 4,   # SNorm16
    6: 1,   # UInt8
    7: 1,   # SInt8
    8: 2,   # UInt16
    9: 2,   # SInt16
    10: 4,  # UInt32
    11: 4,  # SInt32
}

FORMAT_NAMES = {
    0: "Float32", 1: "Float16", 2: "UNorm8", 3: "SNorm8",
    4: "UNorm16", 5: "SNorm16", 6: "UInt8",  7: "SInt8",
    8: "UInt16",  9: "SInt16", 10: "UInt32", 11: "SInt32",
}

CHANNEL_NAMES = {
    0: "Position",   1: "Normal",    2: "Tangent",
    3: "Color",      4: "UV0",       5: "UV1",
    6: "UV2",        7: "UV3",       8: "UV4",
    9: "UV5",       10: "UV6",      11: "UV7",
    12: "BlendWeight", 13: "BlendIndices",
}

REPLACEABLE_CHANNELS = {0, 1, 2, 4, 5}
PRESERVE_CHANNELS    = {12, 13, 3}

# ── Coordinate conversion presets ────────────────────────────────────────────
COORD_PRESETS = {
    "AssetStudio + Blender": {
        "pos":  ( 1,  1, -1),   # x=px,  y=py,  z=-pz
        "nrm":  ( 1,  1, -1),
        "tan":  ( 1,  1, -1),
        "wind": False,
        "desc": "Recommended for OBJs exported via AssetStudio → Blender → re-import.\n"
                "Z-axis is flipped. No winding reversal.",
    },
    "Raw OBJ (Classic)": {
        "pos":  (-1,  1,  1),   # x=-px, y=py, z=pz
        "nrm":  (-1,  1,  1),
        "tan":  (-1,  1,  1),
        "wind": True,
        "desc": "Traditional OBJ→Unity: flip X, reverse winding.\n"
                "Use if your mesh is a fresh export not from AssetStudio.",
    },
    "Unity Native": {
        "pos":  ( 1,  1,  1),   # no flip
        "nrm":  ( 1,  1,  1),
        "tan":  ( 1,  1,  1),
        "wind": False,
        "desc": "No coordinate conversion. Use if the OBJ is already in Unity space.",
    },
    "Custom": {
        "pos":  ( 1,  1,  1),
        "nrm":  ( 1,  1,  1),
        "tan":  ( 1,  1,  1),
        "wind": False,
        "desc": "Manually configure axis flips and winding below.",
    },
}


# ── Channel layout parser ────────────────────────────────────────────────────

def parse_channels(vertex_data):
    channels_raw = vertex_data.get("m_Channels", [])
    result = []
    for i, ch in enumerate(channels_raw):
        dim = ch.get("dimension", 0)
        if dim == 0:
            continue
        fmt = ch.get("format", 0)
        fmt_size = VERTEX_FORMAT_SIZE.get(fmt, 4)
        real_dim = dim & 0x0F
        ch_size  = fmt_size * real_dim
        result.append({
            "index":     i,
            "stream":    ch.get("stream", 0),
            "offset":    ch.get("offset", 0),
            "format":    fmt,
            "dimension": real_dim,
            "size":      ch_size,
            "name":      CHANNEL_NAMES.get(i, f"Ch{i}"),
            "fmt_name":  FORMAT_NAMES.get(fmt, f"Fmt{fmt}"),
        })
    return result


def compute_stride(channels, stream_idx=0):
    stream_chs = [c for c in channels if c["stream"] == stream_idx]
    if not stream_chs:
        return 0
    return max(c["offset"] + c["size"] for c in stream_chs)


def describe_layout(channels, vertex_count):
    lines = []
    streams = sorted(set(c["stream"] for c in channels))
    for s in streams:
        stride = compute_stride(channels, s)
        chs = [c for c in channels if c["stream"] == s]
        lines.append(f"Stream {s}  stride={stride}  total={stride * vertex_count} bytes")
        for c in sorted(chs, key=lambda x: x["offset"]):
            flag = "🔒PRESERVE" if c["index"] in PRESERVE_CHANNELS else "✏ replace"
            lines.append(
                f"  +{c['offset']:3d}  [{c['name']:14s}]  "
                f"{c['fmt_name']} x{c['dimension']}  ({c['size']}B)  {flag}"
            )
    return "\n".join(lines)


# ── OBJ parser ───────────────────────────────────────────────────────────────

def parse_obj(path):
    verts, normals, uvs, faces = [], [], [], []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("v "):
                x, y, z = map(float, line.split()[1:4])
                verts.append((x, y, z))
            elif line.startswith("vn "):
                x, y, z = map(float, line.split()[1:4])
                normals.append((x, y, z))
            elif line.startswith("vt "):
                u, v = map(float, line.split()[1:3])
                uvs.append((u, v))
            elif line.startswith("f "):
                parts = line.split()[1:]
                face  = []
                for p in parts:
                    idx = p.split("/")
                    vi  = int(idx[0]) - 1
                    vti = int(idx[1]) - 1 if len(idx) > 1 and idx[1] else 0
                    vni = int(idx[2]) - 1 if len(idx) > 2 and idx[2] else 0
                    face.append((vi, vti, vni))
                for i in range(1, len(face) - 1):
                    faces.append([face[0], face[i], face[i + 1]])
    return verts, normals, uvs, faces


def weld_vertices(verts, normals, uvs, faces, reverse_winding=False):
    """Weld OBJ faces into flat vertex list + index buffer."""
    vert_map  = {}
    new_verts = []
    indices   = []

    work_faces = faces
    if reverse_winding:
        work_faces = [[f[0], f[2], f[1]] for f in faces]

    for face in work_faces:
        for key in face:
            if key not in vert_map:
                vert_map[key] = len(new_verts)
                new_verts.append(key)
            indices.append(vert_map[key])
    return new_verts, indices


# ── Half-float / format helpers ───────────────────────────────────────────────

def pack_f32(v):  return struct.pack("<f", float(v))
def pack_f16(v):  return struct.pack("<e", float(v))
def pack_u16(v):  return struct.pack("<H", int(v) & 0xFFFF)
def pack_i16(v):  return struct.pack("<h", int(v))
def pack_u8(v):   return struct.pack("<B", int(v) & 0xFF)
def pack_i8(v):   return struct.pack("<b", int(v))
def pack_unorm8(v):  return struct.pack("<B", max(0, min(255, int(round(v * 255)))))
def pack_snorm8(v):  return struct.pack("<b", max(-128, min(127, int(round(v * 127)))))
def pack_unorm16(v): return struct.pack("<H", max(0, min(65535, int(round(v * 65535)))))
def pack_snorm16(v): return struct.pack("<h", max(-32768, min(32767, int(round(v * 32767)))))

FORMAT_PACKERS = {
    0: pack_f32, 1: pack_f16, 2: pack_unorm8, 3: pack_snorm8,
    4: pack_unorm16, 5: pack_snorm16, 6: pack_u8, 7: pack_i8,
    8: pack_u16, 9: pack_i16,
    10: lambda v: struct.pack("<I", int(v)),
    11: lambda v: struct.pack("<i", int(v)),
}

def pack_value(fmt, value):
    return FORMAT_PACKERS.get(fmt, pack_f32)(value)


def unpack_f16(data, offset):
    try:
        return struct.unpack_from("<e", data, offset)[0]
    except Exception:
        return 0.0

def unpack_f32(data, offset):
    try:
        return struct.unpack_from("<f", data, offset)[0]
    except Exception:
        return 0.0


# ── Apply axis flips to a 3-tuple ─────────────────────────────────────────────

def apply_flips(x, y, z, flip_x, flip_y, flip_z):
    return (
        -x if flip_x else x,
        -y if flip_y else y,
        -z if flip_z else z,
    )


# ── Core vertex buffer builder ────────────────────────────────────────────────

def build_vertex_buffer_dynamic(
    new_verts, verts, normals, uvs,
    channels, orig_vbuf_bytes, orig_vert_count,
    log,
    # Coordinate conversion multipliers (sign per axis)
    pos_signs=(1, 1, 1),
    nrm_signs=(1, 1, 1),
    tan_signs=(1, 1, 1),
    # Manual axis flips (additive on top of preset)
    flip_x=False, flip_y=False, flip_z=False,
    # Tangent preservation
    preserve_tangents=False,
):
    """
    Build a new vertex buffer respecting the ORIGINAL channel layout.
    Supports coordinate conversion presets + per-axis flip overrides.
    """
    new_vert_count = len(new_verts)
    streams        = sorted(set(c["stream"] for c in channels))
    stream_buffers = {}

    for stream_idx in streams:
        stride   = compute_stride(channels, stream_idx)
        if stride == 0:
            continue

        stream_chs = sorted(
            [c for c in channels if c["stream"] == stream_idx],
            key=lambda x: x["offset"]
        )

        buf = bytearray(stride * new_vert_count)

        for vi, (pos_i, uv_i, nrm_i) in enumerate(new_verts):
            vert_base = vi * stride

            for ch in stream_chs:
                ch_offset = vert_base + ch["offset"]
                fmt  = ch["format"]
                dim  = ch["dimension"]
                cidx = ch["index"]

                # ── Position ──────────────────────────────────────────────────
                if cidx == 0 and pos_i < len(verts):
                    px, py, pz = verts[pos_i]
                    # Apply preset signs then manual flips
                    px *= pos_signs[0]; py *= pos_signs[1]; pz *= pos_signs[2]
                    px, py, pz = apply_flips(px, py, pz, flip_x, flip_y, flip_z)
                    vals = [px, py, pz]
                    for d in range(min(dim, 3)):
                        b = pack_value(fmt, vals[d])
                        buf[ch_offset:ch_offset + len(b)] = b
                        ch_offset += len(b)

                # ── Normal ────────────────────────────────────────────────────
                elif cidx == 1:
                    if normals and nrm_i < len(normals):
                        nx, ny, nz = normals[nrm_i]
                        nx *= nrm_signs[0]; ny *= nrm_signs[1]; nz *= nrm_signs[2]
                        nx, ny, nz = apply_flips(nx, ny, nz, flip_x, flip_y, flip_z)
                        vals = [nx, ny, nz, 0.0]
                    else:
                        vals = [0.0, 1.0, 0.0, 0.0]
                    for d in range(dim):
                        b = pack_value(fmt, vals[d] if d < len(vals) else 0.0)
                        buf[ch_offset:ch_offset + len(b)] = b
                        ch_offset += len(b)

                # ── Tangent ───────────────────────────────────────────────────
                elif cidx == 2:
                    if preserve_tangents and vi < orig_vert_count and orig_vbuf_bytes:
                        # Copy tangent bytes directly from original — no regen
                        orig_off  = vi * stride + ch["offset"]
                        ch_bytes  = ch["size"]
                        if orig_off + ch_bytes <= len(orig_vbuf_bytes):
                            buf[vert_base + ch["offset"]:vert_base + ch["offset"] + ch_bytes] = \
                                orig_vbuf_bytes[orig_off:orig_off + ch_bytes]
                            continue
                        # Fall through to reconstruct if original data missing

                    # Reconstruct tangent (Gram-Schmidt from normal)
                    if normals and nrm_i < len(normals):
                        nx, ny, nz = normals[nrm_i]
                        nx *= nrm_signs[0]; ny *= nrm_signs[1]; nz *= nrm_signs[2]
                        nx, ny, nz = apply_flips(nx, ny, nz, flip_x, flip_y, flip_z)

                        if abs(nx) < 0.9:
                            tx, ty, tz = 1.0, 0.0, 0.0
                        else:
                            tx, ty, tz = 0.0, 1.0, 0.0
                        dot = tx * nx + ty * ny + tz * nz
                        tx -= dot * nx; ty -= dot * ny; tz -= dot * nz
                        l = math.sqrt(tx*tx + ty*ty + tz*tz)
                        if l > 0:
                            tx /= l; ty /= l; tz /= l

                        # Apply tan_signs + flips to tangent direction
                        tx *= tan_signs[0]; ty *= tan_signs[1]; tz *= tan_signs[2]
                        tx, ty, tz = apply_flips(tx, ty, tz, flip_x, flip_y, flip_z)
                        vals = [tx, ty, tz, 1.0]
                    else:
                        vals = [1.0, 0.0, 0.0, 1.0]

                    for d in range(dim):
                        b = pack_value(fmt, vals[d] if d < len(vals) else 0.0)
                        buf[ch_offset:ch_offset + len(b)] = b
                        ch_offset += len(b)

                # ── UV0 ───────────────────────────────────────────────────────
                elif cidx == 4:
                    if uvs and uv_i < len(uvs):
                        u, v = uvs[uv_i]
                        vals = [u, 1.0 - v]
                    else:
                        vals = [0.0, 0.0]
                    for d in range(dim):
                        b = pack_value(fmt, vals[d] if d < len(vals) else 0.0)
                        buf[ch_offset:ch_offset + len(b)] = b
                        ch_offset += len(b)

                # ── UV1 — copy from original ──────────────────────────────────
                elif cidx == 5:
                    if vi < orig_vert_count and orig_vbuf_bytes:
                        orig_off = vi * stride + ch["offset"]
                        ch_bytes = ch["size"]
                        if orig_off + ch_bytes <= len(orig_vbuf_bytes):
                            buf[vert_base + ch["offset"]:vert_base + ch["offset"] + ch_bytes] = \
                                orig_vbuf_bytes[orig_off:orig_off + ch_bytes]
                            continue
                    sz = ch["size"]
                    buf[vert_base + ch["offset"]:vert_base + ch["offset"] + sz] = bytes(sz)

                # ── Preserve skinning/color ───────────────────────────────────
                elif cidx in PRESERVE_CHANNELS:
                    if vi < orig_vert_count and orig_vbuf_bytes:
                        orig_off = vi * stride + ch["offset"]
                        ch_bytes = ch["size"]
                        if orig_off + ch_bytes <= len(orig_vbuf_bytes):
                            buf[vert_base + ch["offset"]:vert_base + ch["offset"] + ch_bytes] = \
                                orig_vbuf_bytes[orig_off:orig_off + ch_bytes]

                # ── Unknown — zero fill ───────────────────────────────────────
                else:
                    sz = ch["size"]
                    buf[vert_base + ch["offset"]:vert_base + ch["offset"] + sz] = bytes(sz)

        stream_buffers[stream_idx] = bytes(buf)
        log(f"  Stream {stream_idx}: stride={stride}  {new_vert_count} verts"
            f"  = {len(buf)} bytes")

    final_buf = b"".join(stream_buffers[s] for s in sorted(stream_buffers))
    return final_buf, stream_buffers


# ── Validation: decode first N vertices from a built buffer ──────────────────

def extract_validation_samples(buf, channels, vert_count, n=5):
    """
    Return first n positions, normals, and tangents decoded from a vertex buffer.
    Returns list of dicts per vertex.
    """
    if not buf or not channels or vert_count == 0:
        return []

    # Only handles stream 0 for now
    stride = compute_stride(channels, 0)
    if stride == 0:
        return []

    pos_ch = next((c for c in channels if c["index"] == 0 and c["stream"] == 0), None)
    nrm_ch = next((c for c in channels if c["index"] == 1 and c["stream"] == 0), None)
    tan_ch = next((c for c in channels if c["index"] == 2 and c["stream"] == 0), None)

    samples = []
    for vi in range(min(n, vert_count)):
        base = vi * stride
        entry = {"vi": vi, "pos": None, "nrm": None, "tan": None, "tan_w": None}

        if pos_ch:
            off = base + pos_ch["offset"]
            fmt = pos_ch["format"]
            if fmt == 0:   # Float32
                try:
                    x, y, z = struct.unpack_from("<fff", buf, off)
                    entry["pos"] = (x, y, z)
                except Exception:
                    pass
            elif fmt == 1: # Float16
                try:
                    x = unpack_f16(buf, off)
                    y = unpack_f16(buf, off + 2)
                    z = unpack_f16(buf, off + 4)
                    entry["pos"] = (x, y, z)
                except Exception:
                    pass

        if nrm_ch:
            off = base + nrm_ch["offset"]
            fmt = nrm_ch["format"]
            vals = []
            elem_size = VERTEX_FORMAT_SIZE.get(fmt, 2)
            for d in range(min(nrm_ch["dimension"], 3)):
                eo = off + d * elem_size
                if fmt == 1:
                    vals.append(unpack_f16(buf, eo))
                elif fmt == 0:
                    vals.append(unpack_f32(buf, eo))
            if len(vals) >= 3:
                entry["nrm"] = tuple(vals[:3])

        if tan_ch:
            off = base + tan_ch["offset"]
            fmt = tan_ch["format"]
            elem_size = VERTEX_FORMAT_SIZE.get(fmt, 2)
            vals = []
            for d in range(min(tan_ch["dimension"], 4)):
                eo = off + d * elem_size
                if fmt == 1:
                    vals.append(unpack_f16(buf, eo))
                elif fmt == 0:
                    vals.append(unpack_f32(buf, eo))
            if len(vals) >= 3:
                entry["tan"] = tuple(vals[:3])
                if len(vals) >= 4:
                    entry["tan_w"] = vals[3]

        samples.append(entry)
    return samples


def format_validation_report(samples, preset_name, flip_x, flip_y, flip_z,
                              reverse_winding, preserve_tangents):
    lines = [
        "═" * 62,
        "  MESH VALIDATION PANEL",
        "═" * 62,
        f"  Coordinate preset : {preset_name}",
        f"  Axis flips        : X={flip_x}  Y={flip_y}  Z={flip_z}",
        f"  Winding reversal  : {reverse_winding}",
        f"  Preserve tangents : {preserve_tangents}",
        "─" * 62,
    ]
    if not samples:
        lines.append("  (no sample data)")
        return "\n".join(lines)

    for s in samples:
        lines.append(f"  Vertex [{s['vi']}]")
        if s["pos"]:
            lines.append(f"    Pos : ({s['pos'][0]:+.4f}, {s['pos'][1]:+.4f}, {s['pos'][2]:+.4f})")
        if s["nrm"]:
            lines.append(f"    Nrm : ({s['nrm'][0]:+.4f}, {s['nrm'][1]:+.4f}, {s['nrm'][2]:+.4f})")
        if s["tan"]:
            w_str = f"  W={s['tan_w']:+.2f}" if s["tan_w"] is not None else ""
            lines.append(f"    Tan : ({s['tan'][0]:+.4f}, {s['tan'][1]:+.4f}, {s['tan'][2]:+.4f}){w_str}")
        if s["tan_w"] is not None:
            hand = "RIGHT-handed ✓" if s["tan_w"] > 0 else "LEFT-handed (flipped)"
            lines.append(f"    Handedness: {hand}")
    lines.append("═" * 62)
    return "\n".join(lines)


# ── OBJ roundtrip export ──────────────────────────────────────────────────────

def export_reconstructed_obj(out_path, buf, channels, welded, verts, uvs, normals,
                              indices, log):
    """
    Write a simple OBJ from the welded vertex data (post-conversion).
    Reads positions back out of the built buffer for accuracy.
    """
    stride  = compute_stride(channels, 0)
    pos_ch  = next((c for c in channels if c["index"] == 0 and c["stream"] == 0), None)
    uv_ch   = next((c for c in channels if c["index"] == 4 and c["stream"] == 0), None)
    nrm_ch  = next((c for c in channels if c["index"] == 1 and c["stream"] == 0), None)

    n_verts = len(welded)

    def read_floats(ch, vi, count):
        if not ch:
            return [0.0] * count
        off  = vi * stride + ch["offset"]
        fmt  = ch["format"]
        esz  = VERTEX_FORMAT_SIZE.get(fmt, 4)
        vals = []
        for d in range(count):
            eo = off + d * esz
            if fmt == 1:
                vals.append(unpack_f16(buf, eo))
            elif fmt == 0:
                vals.append(unpack_f32(buf, eo))
            else:
                vals.append(0.0)
        return vals

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("# OPERATOR Mesh Swapper v3 — reconstructed OBJ\n")
            for vi in range(n_verts):
                p = read_floats(pos_ch, vi, 3)
                f.write(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")
            for vi in range(n_verts):
                uv = read_floats(uv_ch, vi, 2)
                f.write(f"vt {uv[0]:.6f} {1.0 - uv[1]:.6f}\n")  # un-flip V
            for vi in range(n_verts):
                n = read_floats(nrm_ch, vi, 3)
                f.write(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n")
            f.write("g reconstructed\n")
            for tri in range(len(indices) // 3):
                ia = indices[tri*3+0] + 1
                ib = indices[tri*3+1] + 1
                ic = indices[tri*3+2] + 1
                f.write(f"f {ia}/{ia}/{ia} {ib}/{ib}/{ib} {ic}/{ic}/{ic}\n")
        log(f"✓ Roundtrip OBJ saved: {out_path}")
    except Exception as e:
        log(f"✗ OBJ export failed: {e}")


# ── Calc AABB ─────────────────────────────────────────────────────────────────

def calc_aabb(verts, welded_verts):
    used = [verts[vi] for (vi, _, _) in welded_verts]
    if not used:
        return 0, 0, 0, 0.1, 0.1, 0.1
    xs = [v[0] for v in used]
    ys = [v[1] for v in used]
    zs = [v[2] for v in used]
    cx = (max(xs) + min(xs)) / 2
    cy = (max(ys) + min(ys)) / 2
    cz = (max(zs) + min(zs)) / 2
    ex = max((max(xs) - min(xs)) / 2, 0.0001)
    ey = max((max(ys) - min(ys)) / 2, 0.0001)
    ez = max((max(zs) - min(zs)) / 2, 0.0001)
    return cx, cy, cz, ex, ey, ez


# ── Mesh inspection ───────────────────────────────────────────────────────────

def inspect_mesh(bundle_path, mesh_name):
    env = UnityPy.load(bundle_path)
    for obj in env.objects:
        if obj.type != ClassIDType.Mesh:
            continue
        tree = obj.read_typetree()
        if tree.get("m_Name") != mesh_name:
            continue
        vd       = tree.get("m_VertexData", {})
        vc       = vd.get("m_VertexCount", 0)
        channels = parse_channels(vd)
        layout   = describe_layout(channels, vc)
        sub_count = len(tree.get("m_SubMeshes", []))
        compressed = tree.get("m_CompressedMesh", {})
        has_compressed = any(
            compressed.get(k, {}).get("m_NumItems", 0) > 0
            for k in ["m_Vertices", "m_Normals", "m_UV"]
        )
        stream_data = tree.get("m_StreamData", {})
        is_external = stream_data.get("size", 0) > 0
        return {
            "vertex_count":   vc,
            "sub_count":      sub_count,
            "channels":       channels,
            "layout":         layout,
            "has_compressed": has_compressed,
            "is_external":    is_external,
            "index_count":    len(tree.get("m_IndexBuffer", b"")) // (
                                  4 if tree.get("m_IndexFormat", 0) == 1 else 2),
        }
    return None


def list_meshes(bundle_path, log):
    env = UnityPy.load(bundle_path)
    meshes = []
    for obj in env.objects:
        if obj.type == ClassIDType.Mesh:
            try:
                tree = obj.read_typetree()
                name = tree.get("m_Name", "(unnamed)")
                vd   = tree.get("m_VertexData", {})
                vc   = vd.get("m_VertexCount", 0)
                channels = parse_channels(vd)
                has_skin = any(c["index"] in (12, 13) for c in channels)
                tag  = " [SKINNED]" if has_skin else ""
                meshes.append((name, vc, len(channels), has_skin))
                log(f"  {name}  ({vc} verts, {len(channels)} channels{tag})")
            except Exception as e:
                log(f"  (unreadable: {e})")
    return meshes


# ── Main swap function ────────────────────────────────────────────────────────

def do_swap(bundle_path, obj_path, output_path, mesh_name, log,
            pos_signs=(1, 1, 1), nrm_signs=(1, 1, 1), tan_signs=(1, 1, 1),
            flip_x=False, flip_y=False, flip_z=False,
            reverse_winding=False,
            preserve_tangents=False,
            export_roundtrip_obj=False,
            roundtrip_obj_path=None):

    log("Parsing OBJ...")
    verts, normals, uvs, faces = parse_obj(obj_path)
    log(f"  {len(verts)} positions, {len(normals)} normals, "
        f"{len(uvs)} UVs, {len(faces)} tris")

    log(f"Welding vertices (winding_reversal={reverse_winding})...")
    welded, indices = weld_vertices(verts, normals, uvs, faces,
                                    reverse_winding=reverse_winding)
    log(f"  {len(welded)} unique vertices, {len(indices)//3} triangles")

    log("Loading bundle...")
    env = UnityPy.load(bundle_path)

    found = False
    validation_report = None
    built_buf = None
    built_channels = None
    built_welded  = None
    built_indices = None

    for obj in env.objects:
        if obj.type != ClassIDType.Mesh:
            continue
        tree = obj.read_typetree()
        if tree.get("m_Name") != mesh_name:
            continue
        found = True

        vd              = tree["m_VertexData"]
        orig_vert_count = vd.get("m_VertexCount", 0)
        orig_vbuf       = bytes(vd.get("m_DataSize", b""))
        channels        = parse_channels(vd)

        log(f"\nOriginal mesh: {mesh_name}")
        log(f"  Vertices: {orig_vert_count}")
        log(f"  Channels: {len(channels)}")
        log(f"  External resS: {tree.get('m_StreamData', {}).get('size', 0) > 0}")
        log(f"\nVertex layout:")
        for line in describe_layout(channels, orig_vert_count).split("\n"):
            log(f"  {line}")

        if not channels:
            log("✗ No channel data — cannot determine vertex layout!")
            return False, None

        log(f"\nBuilding channel-aware vertex buffer...")
        log(f"  pos_signs={pos_signs}  nrm_signs={nrm_signs}  tan_signs={tan_signs}")
        log(f"  flip_x={flip_x}  flip_y={flip_y}  flip_z={flip_z}")
        log(f"  preserve_tangents={preserve_tangents}")

        new_vbuf, stream_bufs = build_vertex_buffer_dynamic(
            welded, verts, normals, uvs,
            channels, orig_vbuf, orig_vert_count, log,
            pos_signs=pos_signs, nrm_signs=nrm_signs, tan_signs=tan_signs,
            flip_x=flip_x, flip_y=flip_y, flip_z=flip_z,
            preserve_tangents=preserve_tangents,
        )

        built_buf      = new_vbuf
        built_channels = channels
        built_welded   = welded
        built_indices  = indices

        # Index buffer
        if len(welded) > 65535:
            ibuf    = struct.pack(f"<{len(indices)}I", *indices)
            idx_fmt = 1
        else:
            ibuf    = struct.pack(f"<{len(indices)}H", *indices)
            idx_fmt = 0
        log(f"  Index buffer: {len(indices)} indices "
            f"({'32' if idx_fmt else '16'}-bit)")

        # Validate stride
        for s in set(c["stream"] for c in channels):
            expected = compute_stride(channels, s)
            actual   = len(stream_bufs.get(s, b"")) // len(welded) if welded else 0
            if actual != expected:
                log(f"⚠ Stream {s} stride mismatch: expected {expected}, got {actual}")
            else:
                log(f"  Stream {s} stride validated: {expected} bytes ✓")

        # Clear compressed mesh
        if tree.get("m_CompressedMesh"):
            compressed = tree["m_CompressedMesh"]
            for key in compressed:
                val = compressed[key]
                if isinstance(val, dict):
                    val.pop("m_Data", None)
                    val["m_Data"]    = []
                    val["m_NumItems"] = 0
                    val["m_BitSize"]  = 0
            tree["m_CompressedMesh"] = compressed
            log("  Cleared m_CompressedMesh ✓")

        # Write new data
        tree["m_IndexBuffer"]    = ibuf
        tree["m_IndexFormat"]    = idx_fmt
        vd["m_VertexCount"]      = len(welded)
        vd["m_DataSize"]         = new_vbuf
        tree["m_VertexData"]     = vd
        tree["m_StreamData"]     = {"offset": 0, "size": 0, "path": ""}

        # Submeshes
        sub_meshes = tree.get("m_SubMeshes", [])
        if sub_meshes:
            tris_per_sub = len(indices) // len(sub_meshes)
            for i, sm in enumerate(sub_meshes):
                sm["firstByte"]   = i * tris_per_sub * (4 if idx_fmt else 2)
                sm["indexCount"]  = tris_per_sub
                sm["vertexCount"] = len(welded)
                sm["firstVertex"] = 0
                sm["baseVertex"]  = 0
            tree["m_SubMeshes"] = sub_meshes
            log(f"  Updated {len(sub_meshes)} submesh(es) ✓")

        # AABB
        cx, cy, cz, ex, ey, ez = calc_aabb(verts, welded)
        aabb = {
            "m_Center": {"x": cx, "y": cy, "z": cz},
            "m_Extent": {"x": ex, "y": ey, "z": ez},
        }
        tree["m_LocalAABB"] = aabb
        for sm in tree.get("m_SubMeshes", []):
            sm["localAABB"] = aabb

        obj.save_typetree(tree)
        log(f"\n✓ Mesh written successfully")
        log(f"  New vertex count: {len(welded)}")
        log(f"  New index count:  {len(indices)}")
        log(f"  New buffer size:  {len(new_vbuf)} bytes")
        break

    if not found:
        log(f"✗ Mesh '{mesh_name}' not found!")
        env2 = UnityPy.load(bundle_path)
        for o in env2.objects:
            if o.type == ClassIDType.Mesh:
                try:
                    t = o.read_typetree()
                    log(f"  • {t.get('m_Name', '?')}")
                except Exception:
                    pass
        return False, None

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(env.file.save())
    log(f"✓ Saved: {output_path}")

    # Validation samples
    samples = extract_validation_samples(built_buf, built_channels,
                                         len(built_welded) if built_welded else 0)

    # OBJ roundtrip export
    if export_roundtrip_obj and roundtrip_obj_path and built_buf:
        log("\nExporting roundtrip OBJ...")
        export_reconstructed_obj(
            roundtrip_obj_path, built_buf, built_channels,
            built_welded, verts, uvs, normals, built_indices, log,
        )

    return True, samples


# ═══════════════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OPERATOR Mesh Swapper  v3")
        self.geometry("900x720")
        self.resizable(True, True)
        self.configure(bg="#0d0d0f")
        self._meshes_cache  = []
        self._last_samples  = []
        self._setup_styles()
        self._build_ui()
        if not UNITYPY_OK:
            messagebox.showerror(
                "Missing dependency",
                "UnityPy is not installed.\n\nRun:  pip install UnityPy"
            )

    # ── Styles ────────────────────────────────────────────────────────────────
    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        bg, panel, accent = "#0d0d0f", "#111116", "#58a6ff"
        text, dim, entry_bg = "#c9d1d9", "#484f58", "#161b22"
        warn = "#f0883e"

        s.configure(".", background=bg, foreground=text, font=("Consolas", 10))
        s.configure("TFrame",      background=bg)
        s.configure("TLabel",      background=bg, foreground=text)
        s.configure("TCheckbutton", background=bg, foreground=text,
                    indicatorcolor=accent)
        s.map("TCheckbutton", background=[("active", bg)])
        s.configure("TEntry", fieldbackground=entry_bg, foreground=text,
                    insertcolor=accent, bordercolor="#30363d", relief="flat")
        s.configure("TButton", background="#21262d", foreground=text,
                    bordercolor="#30363d", relief="flat", padding=6)
        s.map("TButton", background=[("active", "#30363d")])
        s.configure("Accent.TButton", background=accent, foreground="#0d1117",
                    font=("Consolas", 10, "bold"), padding=8)
        s.map("Accent.TButton", background=[("active", "#79c0ff")])
        s.configure("Warn.TButton", background=warn, foreground="#0d1117",
                    font=("Consolas", 10, "bold"), padding=8)
        s.map("Warn.TButton", background=[("active", "#ffa657")])
        s.configure("TNotebook",     background=bg, bordercolor="#21262d")
        s.configure("TNotebook.Tab", background="#161b22", foreground=dim,
                    padding=[14, 5])
        s.map("TNotebook.Tab",
              background=[("selected", bg)],
              foreground=[("selected", accent)])
        s.configure("TLabelframe",       background=bg, bordercolor="#21262d")
        s.configure("TLabelframe.Label", background=bg, foreground=dim,
                    font=("Consolas", 9))
        s.configure("TCombobox", fieldbackground=entry_bg, foreground=text,
                    background=entry_bg, selectbackground="#1f6feb",
                    arrowcolor=accent)
        s.configure("TScrollbar", background="#21262d",
                    troughcolor="#0d0d0f", arrowcolor=dim)
        s.configure("Treeview", background="#161b22", foreground=text,
                    fieldbackground="#161b22", font=("Consolas", 10))
        s.configure("Treeview.Heading", background="#21262d", foreground=accent,
                    font=("Consolas", 10, "bold"))
        s.map("Treeview", background=[("selected", "#1f6feb")])

    # ── Top-level UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg="#0d0d0f", pady=8)
        hdr.pack(fill="x", padx=16)
        tk.Label(hdr, text="◈  OPERATOR MESH SWAPPER  v3",
                 bg="#0d0d0f", fg="#58a6ff",
                 font=("Consolas", 13, "bold")).pack(side="left")
        tk.Label(hdr, text="Pipeline-Aware Coordinate Engine",
                 bg="#0d0d0f", fg="#30363d",
                 font=("Consolas", 9)).pack(side="right", pady=2)
        tk.Frame(self, bg="#21262d", height=1).pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.nb = nb

        self._build_swap_tab(nb)
        self._build_coord_tab(nb)
        self._build_inspect_tab(nb)
        self._build_validation_tab(nb)
        self._build_log_tab(nb)
        self._build_about_tab(nb)

    # ── Swap tab ──────────────────────────────────────────────────────────────
    def _build_swap_tab(self, nb):
        frame = tk.Frame(nb, bg="#0d0d0f")
        nb.add(frame, text="  Mesh Swap  ")

        self._vars = {}

        def path_row(parent, label, key, mode, ext, row):
            tk.Label(parent, text=label, bg="#0d0d0f", fg="#8b949e",
                     font=("Consolas", 10), width=20,
                     anchor="w").grid(row=row, column=0, padx=(12, 4), pady=4, sticky="w")
            var = tk.StringVar()
            self._vars[key] = var
            ttk.Entry(parent, textvariable=var, width=46).grid(
                row=row, column=1, pady=4, sticky="w")
            def cmd(v=var, m=mode, e=ext):
                if m == "file":
                    p = filedialog.askopenfilename(
                        filetypes=[(e.upper(), f"*.{e}"), ("All", "*.*")])
                elif m == "save":
                    p = filedialog.asksaveasfilename(
                        defaultextension=f".{e}",
                        filetypes=[(e.upper(), f"*.{e}")])
                else:
                    p = ""
                if p:
                    v.set(p)
                    if key == "bundle":
                        self._auto_output()
            ttk.Button(parent, text="Browse", width=8,
                       command=cmd).grid(row=row, column=2, pady=4, padx=(4, 12))

        paths = tk.Frame(frame, bg="#0d0d0f")
        paths.pack(fill="x", pady=(8, 0))
        path_row(paths, "Bundle (.bundle):", "bundle", "file",  "bundle", 0)
        path_row(paths, "Replacement OBJ:", "obj",    "file",  "obj",    1)
        path_row(paths, "Output Bundle:",   "output", "save",  "bundle", 2)

        tk.Frame(frame, bg="#21262d", height=1).pack(fill="x", pady=8)

        mesh_frame = ttk.LabelFrame(frame, text="Target Mesh", padding=8)
        mesh_frame.pack(fill="x", padx=12, pady=(0, 6))

        top_row = tk.Frame(mesh_frame, bg="#0d0d0f")
        top_row.pack(fill="x", pady=(0, 6))
        tk.Label(top_row, text="Mesh Name:", bg="#0d0d0f", fg="#8b949e",
                 font=("Consolas", 10)).pack(side="left", padx=(0, 6))
        self._mesh_name_var = tk.StringVar()
        ttk.Entry(top_row, textvariable=self._mesh_name_var,
                  width=42).pack(side="left", padx=(0, 6))
        ttk.Button(top_row, text="Scan Bundle",
                   command=self._scan_bundle).pack(side="left", padx=2)
        ttk.Button(top_row, text="Inspect",
                   command=self._inspect_selected).pack(side="left", padx=2)

        list_frame = tk.Frame(mesh_frame, bg="#0d0d0f")
        list_frame.pack(fill="x")
        self._mesh_list = tk.Listbox(
            list_frame, height=5, bg="#161b22", fg="#c9d1d9",
            selectbackground="#1f6feb", activestyle="none",
            relief="flat", bd=0, font=("Consolas", 10))
        vsb = ttk.Scrollbar(list_frame, orient="vertical",
                            command=self._mesh_list.yview)
        self._mesh_list.configure(yscrollcommand=vsb.set)
        self._mesh_list.pack(side="left", fill="x", expand=True)
        vsb.pack(side="right", fill="y")
        self._mesh_list.bind("<<ListboxSelect>>", self._on_mesh_select)

        ttk.Button(frame, text="▶  Run Mesh Swap",
                   style="Accent.TButton",
                   command=self._run_swap).pack(pady=10, padx=12, fill="x")

    # ── Coordinate Conversion tab ─────────────────────────────────────────────
    def _build_coord_tab(self, nb):
        frame = tk.Frame(nb, bg="#0d0d0f")
        nb.add(frame, text="  Coordinates  ")

        tk.Label(frame, text="Coordinate Conversion",
                 bg="#0d0d0f", fg="#58a6ff",
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))

        tk.Label(frame,
                 text="Choose a pipeline preset or configure custom axis handling below.\n"
                      "Settings here affect positions, normals, and tangents.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9), justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        tk.Frame(frame, bg="#21262d", height=1).pack(fill="x")

        # ── Preset dropdown ───────────────────────────────────────────────────
        preset_frame = ttk.LabelFrame(frame, text="Pipeline Preset", padding=10)
        preset_frame.pack(fill="x", padx=12, pady=10)

        self._preset_var = tk.StringVar(value="AssetStudio + Blender")
        preset_names = list(COORD_PRESETS.keys())
        cb = ttk.Combobox(preset_frame, textvariable=self._preset_var,
                          values=preset_names, state="readonly", width=34)
        cb.pack(anchor="w")

        self._preset_desc = tk.Label(preset_frame, text="", bg="#0d0d0f", fg="#79c0ff",
                                      font=("Consolas", 9), justify="left", wraplength=700)
        self._preset_desc.pack(anchor="w", pady=(6, 0))

        def on_preset_change(*_):
            name = self._preset_var.get()
            p    = COORD_PRESETS.get(name, {})
            self._preset_desc.config(text=p.get("desc", ""))
            # Auto-apply preset values to flip checkboxes if not Custom
            if name != "Custom":
                signs = p.get("pos", (1, 1, 1))
                # Reflect sign as flip checkbox (negative = checked)
                self._flip_x.set(signs[0] < 0)
                self._flip_y.set(signs[1] < 0)
                self._flip_z.set(signs[2] < 0)
                self._wind_var.set(p.get("wind", False))

        self._preset_var.trace_add("write", on_preset_change)

        # ── Winding reversal ──────────────────────────────────────────────────
        wind_frame = ttk.LabelFrame(frame, text="Triangle Winding", padding=10)
        wind_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._wind_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(wind_frame,
                         text="Reverse Triangle Winding  "
                              "(swap vertex 1 and 2 of each triangle)",
                         variable=self._wind_var).pack(anchor="w")
        tk.Label(wind_frame,
                 text="Enable if faces are inside-out or culled incorrectly.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9)).pack(anchor="w")

        # ── Axis flips ────────────────────────────────────────────────────────
        flip_frame = ttk.LabelFrame(frame, text="Manual Axis Flips  (applied after preset)", padding=10)
        flip_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._flip_x = tk.BooleanVar(value=False)
        self._flip_y = tk.BooleanVar(value=False)
        self._flip_z = tk.BooleanVar(value=False)

        row = tk.Frame(flip_frame, bg="#0d0d0f")
        row.pack(anchor="w")
        ttk.Checkbutton(row, text="Flip X", variable=self._flip_x).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(row, text="Flip Y", variable=self._flip_y).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(row, text="Flip Z", variable=self._flip_z).pack(side="left")

        tk.Label(flip_frame,
                 text="Applies negation to X/Y/Z components of positions, normals, and tangents.\n"
                      "Use to fine-tune orientation without changing the preset.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9), justify="left").pack(anchor="w", pady=(6, 0))

        # ── Tangent preservation ──────────────────────────────────────────────
        tan_frame = ttk.LabelFrame(frame, text="Tangent Handling", padding=10)
        tan_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._preserve_tan = tk.BooleanVar(value=False)
        ttk.Checkbutton(tan_frame,
                         text="Preserve Original Tangents  "
                              "(copy bytes from original mesh, skip regeneration)",
                         variable=self._preserve_tan).pack(anchor="w")
        tk.Label(tan_frame,
                 text="Useful for isolating whether tangent reconstruction is causing\n"
                      "subtle shading issues. Only works when vertex count is unchanged.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9), justify="left").pack(anchor="w")

        # ── OBJ roundtrip export ──────────────────────────────────────────────
        rt_frame = ttk.LabelFrame(frame, text="OBJ Roundtrip Debugging", padding=10)
        rt_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._roundtrip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rt_frame,
                         text="Export reconstructed mesh as OBJ after swap",
                         variable=self._roundtrip_var).pack(anchor="w")

        rt_path_row = tk.Frame(rt_frame, bg="#0d0d0f")
        rt_path_row.pack(anchor="w", pady=(6, 0), fill="x")
        tk.Label(rt_path_row, text="Roundtrip OBJ path:", bg="#0d0d0f", fg="#8b949e",
                 font=("Consolas", 10)).pack(side="left", padx=(0, 6))
        self._roundtrip_path_var = tk.StringVar()
        ttk.Entry(rt_path_row, textvariable=self._roundtrip_path_var, width=36).pack(side="left")
        def browse_rt():
            p = filedialog.asksaveasfilename(defaultextension=".obj",
                                              filetypes=[("OBJ", "*.obj")])
            if p:
                self._roundtrip_path_var.set(p)
        ttk.Button(rt_path_row, text="Browse", width=8,
                   command=browse_rt).pack(side="left", padx=(4, 0))

        tk.Label(rt_frame,
                 text="Compare original OBJ, reconstructed buffer, and final import\n"
                      "to pinpoint exactly where corruption occurs.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9), justify="left").pack(anchor="w", pady=(6, 0))

        # Trigger initial preset description display
        on_preset_change()

    # ── Inspector tab ─────────────────────────────────────────────────────────
    def _build_inspect_tab(self, nb):
        frame = tk.Frame(nb, bg="#0d0d0f")
        nb.add(frame, text="  Inspector  ")

        tk.Label(frame, text="Mesh Layout Inspector",
                 bg="#0d0d0f", fg="#58a6ff",
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(frame,
                 text="Shows the exact vertex channel layout of the selected mesh.\n"
                      "🔒 PRESERVE channels (skinning) are copied from original.\n"
                      "✏ replace channels are rebuilt from your OBJ.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9), justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        tk.Frame(frame, bg="#21262d", height=1).pack(fill="x")

        self._inspect_box = scrolledtext.ScrolledText(
            frame, bg="#0a0c10", fg="#79c0ff",
            font=("Consolas", 10), relief="flat",
            state="disabled", wrap="none")
        self._inspect_box.pack(fill="both", expand=True, padx=8, pady=8)

    # ── Validation tab ────────────────────────────────────────────────────────
    def _build_validation_tab(self, nb):
        frame = tk.Frame(nb, bg="#0d0d0f")
        nb.add(frame, text="  Validation  ")

        tk.Label(frame, text="Mesh Validation Panel",
                 bg="#0d0d0f", fg="#58a6ff",
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(frame,
                 text="Populated automatically after each swap. Shows decoded positions,\n"
                      "normals, tangents, and handedness of first 5 vertices.",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 9), justify="left").pack(anchor="w", padx=12, pady=(0, 8))

        tk.Frame(frame, bg="#21262d", height=1).pack(fill="x")

        self._validation_box = scrolledtext.ScrolledText(
            frame, bg="#0a0c10", fg="#c9d1d9",
            font=("Consolas", 10), relief="flat",
            state="disabled", wrap="none")
        self._validation_box.pack(fill="both", expand=True, padx=8, pady=8)

    # ── Log tab ───────────────────────────────────────────────────────────────
    def _build_log_tab(self, nb):
        frame = tk.Frame(nb, bg="#0d0d0f")
        nb.add(frame, text="  Log  ")
        self._log_box = scrolledtext.ScrolledText(
            frame, bg="#0a0c10", fg="#3fb950",
            font=("Consolas", 9), relief="flat",
            state="disabled", wrap="word")
        self._log_box.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(frame, text="Clear Log",
                   command=self._clear_log).pack(pady=(0, 8))

    # ── About tab ─────────────────────────────────────────────────────────────
    def _build_about_tab(self, nb):
        frame = tk.Frame(nb, bg="#0d0d0f")
        nb.add(frame, text="  About  ")

        # Center container
        center = tk.Frame(frame, bg="#0d0d0f")
        center.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(center, text="◈  OPERATOR MESH SWAPPER",
                 bg="#0d0d0f", fg="#58a6ff",
                 font=("Consolas", 16, "bold")).pack(pady=(0, 4))

        tk.Label(center, text="v3  —  Pipeline-Aware Coordinate Engine",
                 bg="#0d0d0f", fg="#30363d",
                 font=("Consolas", 10)).pack(pady=(0, 20))

        tk.Frame(center, bg="#21262d", height=1, width=460).pack(pady=(0, 20))

        desc = (
            "A dynamic channel-aware Unity mesh replacement tool\n"
            "built for the milsim game OPERATOR.\n\n"
            "Supports static and skinned meshes, multi-stream layouts,\n"
            "coordinate conversion presets, axis flip debugging,\n"
            "tangent preservation, and OBJ roundtrip validation.\n\n"
            "Unity version:  6000.3.8f1\n"
            "Built with:     UnityPy  +  Python  +  Tkinter"
        )
        tk.Label(center, text=desc,
                 bg="#0d0d0f", fg="#8b949e",
                 font=("Consolas", 10),
                 justify="center").pack(pady=(0, 24))

        tk.Frame(center, bg="#21262d", height=1, width=460).pack(pady=(0, 24))

        tk.Label(center, text="If this tool saved you hours of frustration,\nconsider buying the dev a coffee ☕",
                 bg="#0d0d0f", fg="#c9d1d9",
                 font=("Consolas", 10),
                 justify="center").pack(pady=(0, 12))

        coffee_btn = tk.Button(
            center,
            text="☕  Buy Me a Coffee",
            bg="#FFDD00",
            fg="#000000",
            font=("Consolas", 11, "bold"),
            relief="flat",
            padx=20, pady=10,
            cursor="hand2",
            activebackground="#ffe74d",
            activeforeground="#000000",
            command=lambda: webbrowser.open("https://www.buymeacoffee.com/Frogger_101")
        )
        coffee_btn.pack(pady=(0, 6))

        tk.Label(center, text="buymeacoffee.com/Frogger_101",
                 bg="#0d0d0f", fg="#484f58",
                 font=("Consolas", 8)).pack()

        tk.Frame(center, bg="#21262d", height=1, width=460).pack(pady=(24, 0))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _auto_output(self):
        bp = self._vars["bundle"].get()
        if not bp:
            return
        name = os.path.basename(bp)
        base, ext = os.path.splitext(name)
        out = os.path.join(os.path.dirname(bp), f"{base}_modified{ext}")
        self._vars["output"].set(out)

    def _scan_bundle(self):
        bp = self._vars["bundle"].get()
        if not bp or not os.path.isfile(bp):
            messagebox.showwarning("No bundle", "Select a bundle file first.")
            return
        self._mesh_list.delete(0, "end")
        self._meshes_cache.clear()
        self._log(f"Scanning: {os.path.basename(bp)}")
        self.nb.select(4)  # Log tab

        def run():
            try:
                meshes = list_meshes(bp, self._log)
                self._meshes_cache = meshes
                for name, vc, ch_count, is_skinned in meshes:
                    tag     = " ⚠SKINNED" if is_skinned else ""
                    display = f"{name}  ({vc}v, {ch_count}ch{tag})"
                    self.after(0, lambda d=display:
                               self._mesh_list.insert("end", d))
                self.after(0, lambda: self.nb.select(0))
            except Exception as e:
                self._log(f"✗ {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    def _on_mesh_select(self, _):
        sel = self._mesh_list.curselection()
        if sel and sel[0] < len(self._meshes_cache):
            name = self._meshes_cache[sel[0]][0]
            self._mesh_name_var.set(name)

    def _inspect_selected(self):
        bp = self._vars["bundle"].get()
        mn = self._mesh_name_var.get().strip()
        if not bp or not mn:
            messagebox.showwarning("Missing", "Select a bundle and mesh first.")
            return
        self.nb.select(2)  # Inspector tab

        def run():
            try:
                info = inspect_mesh(bp, mn)
                if not info:
                    self._set_inspect("Mesh not found.")
                    return
                lines = [
                    f"Mesh:           {mn}",
                    f"Vertex count:   {info['vertex_count']}",
                    f"Index count:    {info['index_count']}",
                    f"Submesh count:  {info['sub_count']}",
                    f"External .resS: {info['is_external']}",
                    f"Has compressed: {info['has_compressed']}",
                    "",
                    "Vertex Channel Layout:",
                    "─" * 60,
                    info["layout"],
                    "",
                    "─" * 60,
                    "Channels marked 🔒PRESERVE will be copied from original.",
                    "Channels marked ✏ replace will be rebuilt from your OBJ.",
                    "",
                    "Skinning channels present: "
                    + ("YES ⚠ — preserve mode active"
                       if any(c["index"] in (12, 13) for c in info["channels"])
                       else "No (static mesh)"),
                ]
                self._set_inspect("\n".join(lines))
            except Exception as e:
                self._set_inspect(f"Error: {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    def _set_inspect(self, text):
        def _do():
            self._inspect_box.configure(state="normal")
            self._inspect_box.delete("1.0", "end")
            self._inspect_box.insert("end", text)
            self._inspect_box.configure(state="disabled")
        self.after(0, _do)

    def _set_validation(self, text):
        def _do():
            self._validation_box.configure(state="normal")
            self._validation_box.delete("1.0", "end")
            self._validation_box.insert("end", text)
            self._validation_box.configure(state="disabled")
        self.after(0, _do)

    def _get_coord_params(self):
        """Resolve current preset + manual override into sign tuples."""
        preset_name = self._preset_var.get()
        preset      = COORD_PRESETS.get(preset_name, COORD_PRESETS["Unity Native"])

        if preset_name == "Custom":
            # For Custom, signs are all 1 and manual flips do the work
            pos_signs = (1, 1, 1)
            nrm_signs = (1, 1, 1)
            tan_signs = (1, 1, 1)
        else:
            pos_signs = preset["pos"]
            nrm_signs = preset["nrm"]
            tan_signs = preset["tan"]

        return {
            "pos_signs":       pos_signs,
            "nrm_signs":       nrm_signs,
            "tan_signs":       tan_signs,
            "flip_x":          self._flip_x.get(),
            "flip_y":          self._flip_y.get(),
            "flip_z":          self._flip_z.get(),
            "reverse_winding": self._wind_var.get(),
            "preserve_tangents": self._preserve_tan.get(),
            "preset_name":     preset_name,
        }

    def _run_swap(self):
        bp  = self._vars["bundle"].get().strip()
        op  = self._vars["obj"].get().strip()
        out = self._vars["output"].get().strip()
        mn  = self._mesh_name_var.get().strip()

        errs = []
        if not bp or not os.path.isfile(bp): errs.append("Bundle file missing")
        if not op or not os.path.isfile(op): errs.append("OBJ file missing")
        if not out:                          errs.append("Output path missing")
        if not mn:                           errs.append("Mesh name missing")
        if errs:
            messagebox.showerror("Missing inputs", "\n".join(errs))
            return

        cp = self._get_coord_params()

        export_rt  = self._roundtrip_var.get()
        rt_path    = self._roundtrip_path_var.get().strip() or None

        self._log("─" * 60)
        self._log(f"Starting pipeline-aware mesh swap  (v3)")
        self._log(f"  Bundle:  {os.path.basename(bp)}")
        self._log(f"  OBJ:     {os.path.basename(op)}")
        self._log(f"  Mesh:    {mn}")
        self._log(f"  Preset:  {cp['preset_name']}")
        self.nb.select(4)  # Log tab

        def run():
            try:
                ok, samples = do_swap(
                    bp, op, out, mn, self._log,
                    pos_signs      = cp["pos_signs"],
                    nrm_signs      = cp["nrm_signs"],
                    tan_signs      = cp["tan_signs"],
                    flip_x         = cp["flip_x"],
                    flip_y         = cp["flip_y"],
                    flip_z         = cp["flip_z"],
                    reverse_winding     = cp["reverse_winding"],
                    preserve_tangents   = cp["preserve_tangents"],
                    export_roundtrip_obj= export_rt,
                    roundtrip_obj_path  = rt_path,
                )
                if ok:
                    self._log("─" * 60)
                    self._log("✓ DONE!")

                    # Build validation report
                    if samples:
                        report = format_validation_report(
                            samples,
                            cp["preset_name"],
                            cp["flip_x"], cp["flip_y"], cp["flip_z"],
                            cp["reverse_winding"], cp["preserve_tangents"],
                        )
                        self._set_validation(report)
                        # Switch to validation tab
                        self.after(200, lambda: self.nb.select(3))

                    self.after(0, lambda: messagebox.showinfo(
                        "Done!",
                        f"Mesh swap complete!\n\nOutput:\n{out}"
                    ))
            except Exception as e:
                self._log(f"✗ {e}\n{traceback.format_exc()}")
                self.after(0, lambda: messagebox.showerror("Error", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _log(self, msg):
        def _do():
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
