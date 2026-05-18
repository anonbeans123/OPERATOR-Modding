import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import json
import os
import shutil
import traceback

try:
    import UnityPy
    import UnityPy.config
    UNITYPY_OK = True
except ImportError:
    UNITYPY_OK = False

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.3.8f1"

# ── Attachment type map ───────────────────────────────────────────────────────
ATTACH_TYPES = {
    0:  "Top Rail",
    1:  "MLOK Grip / Left Rail / Bottom Rail",
    2:  "Mount Rail / Sight / Mount Sight",
    3:  "Barrel",
    4:  "Handguard",
    5:  "Stock",
    6:  "Top Front Rail / SKIFF Rail",
    7:  "Rear Sight / Iron Sight Base",
    9:  "Pistol Grip",
    10: "Muzzle Device (6.8x51 / 12ga / AKM)",
    11: "Trigger",
    13: "Micro Sight Mount / RMR Mount",
    15: "Upper Receiver",
    16: "Buffer Tube",
    18: "Scope (30mm / 34mm)",
    19: "Rail Right",
    20: "Rail Right (alt)",
    22: "Left Rail / Rail Right",
    23: "Suppressor / Muzzle (MP9/SVD/NT4)",
    25: "Accessory (Flashlight/Laser/Cover)",
    26: "MLOK Rail Sides",
    27: "Muzzle (.308)",
    28: "Gas Block",
    29: "Ammunition",
    30: "Base Plate / Mag Extension",
    31: "RMR Riser / Backplate",
    32: "RMR",
    33: "Muzzle (9x19)",
    34: "Rear Sight (TT 2011)",
    35: "RMR (alt)",
    36: "Muzzle (9x19 alt)",
    40: "Optic Plate",
    43: "RMR (alt2)",
    44: "RMR (alt3)",
    45: "RMR (alt4)",
    46: "Muzzle (9x19 alt2)",
    47: "Muzzle (9x19 alt3)",
    48: "Stock Riser (CTR)",
    49: "Stock Riser (B5)",
    50: "Barrel Muzzle (45ACP)",
    51: "Backplate",
}

NEXT_ASSET_INDEX = 1445  # Highest found + 1


def log_fn_placeholder(msg, end=False):
    print(msg)


def find_donor_bundle(asset_ref_index, bundle_dir, log_fn):
    """Find which bundle contains a WeaponMod with a given AssetReferenceIndex."""
    log_fn(f"Finding donor bundle for AssetReferenceIndex {asset_ref_index}...")
    files = [f for f in os.listdir(bundle_dir) if os.path.isfile(os.path.join(bundle_dir, f))]
    for i, filename in enumerate(files):
        filepath = os.path.join(bundle_dir, filename)
        if i % 200 == 0:
            log_fn(f"  Scanning... ({i}/{len(files)})", end=True)
        try:
            env = UnityPy.load(filepath)
            for obj in env.objects:
                if obj.type.name == "MonoBehaviour":
                    tree = obj.read_typetree()
                    if tree.get("AssetReferenceIndex") == asset_ref_index:
                        log_fn(f"\n  ✓ Found: {filename}")
                        return filepath
        except:
            pass
    log_fn("\n  ✗ Not found.")
    return None


def create_new_mod_bundle(
    donor_bundle_path,
    new_mesh_obj_path,
    output_bundle_path,
    new_asset_index,
    mod_stats,
    log_fn
):
    """
    Duplicate a donor bundle, swap the mesh, update MonoBehaviour stats,
    assign new AssetReferenceIndex. Returns success bool.
    """
    import struct

    log_fn(f"\nLoading donor bundle...")
    env = UnityPy.load(donor_bundle_path)

    # ── Step 1: Update WeaponMod MonoBehaviour ────────────────────────────
    mono_updated = False
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
            if "AssetReferenceIndex" not in tree:
                continue
            log_fn(f"  Found WeaponMod — updating stats...")
            tree["AssetReferenceIndex"] = new_asset_index
            for field, val in mod_stats.items():
                if field in tree:
                    tree[field] = val
                    log_fn(f"    {field} = {val}")
            obj.save_typetree(tree)
            mono_updated = True
            break
        except:
            pass

    if not mono_updated:
        log_fn("  ✗ Could not find/update WeaponMod MonoBehaviour")
        return False

    # ── Step 2: Swap mesh if OBJ provided ────────────────────────────────
    if new_mesh_obj_path and os.path.isfile(new_mesh_obj_path):
        log_fn(f"\nSwapping mesh from: {os.path.basename(new_mesh_obj_path)}")
        try:
            verts, normals, uvs, faces = parse_obj(new_mesh_obj_path)
            log_fn(f"  OBJ: {len(verts)} verts, {len(faces)} tris")
            vbuf, ibuf, vc, ic, ifmt = build_vertex_buffer(verts, normals, uvs, faces)

            for obj in env.objects:
                if obj.type.name == "Mesh":
                    tree = obj.read_typetree()
                    cx,cy,cz,ex,ey,ez = calc_aabb(verts,
                        [fv for face in faces for fv in face])
                    tree["m_IndexBuffer"] = ibuf
                    tree["m_IndexFormat"] = ifmt
                    tree["m_VertexData"]["m_VertexCount"] = vc
                    tree["m_VertexData"]["m_DataSize"] = vbuf
                    tree["m_StreamData"]["offset"] = 0
                    tree["m_StreamData"]["size"] = 0
                    tree["m_StreamData"]["path"] = ""
                    for sm in tree["m_SubMeshes"]:
                        sm["indexCount"] = ic
                        sm["vertexCount"] = vc
                        sm["firstByte"] = 0
                        sm["firstVertex"] = 0
                        sm["baseVertex"] = 0
                        sm["localAABB"] = {
                            "m_Center": {"x": -cx, "y": cy, "z": cz},
                            "m_Extent": {"x": ex, "y": ey, "z": ez}
                        }
                    tree["m_LocalAABB"] = {
                        "m_Center": {"x": -cx, "y": cy, "z": cz},
                        "m_Extent": {"x": ex, "y": ey, "z": ez}
                    }
                    obj.save_typetree(tree)
                    log_fn(f"  ✓ Mesh swapped: {vc} verts, {ic//3} tris")
                    break
        except Exception as e:
            log_fn(f"  ⚠ Mesh swap failed: {e} — continuing without mesh swap")

    # ── Step 3: Save output bundle ────────────────────────────────────────
    os.makedirs(os.path.dirname(output_bundle_path), exist_ok=True)
    with open(output_bundle_path, "wb") as f:
        f.write(env.file.save())
    log_fn(f"\n✓ New bundle saved: {output_bundle_path}")
    return True


def register_mod_in_parent(
    parent_bundle_path,
    output_bundle_path,
    new_mod_index,
    log_fn
):
    """
    Add new_mod_index to ALL WeaponModParent.CompatibleModIds arrays
    in the given bundle. Returns count of parents updated.
    """
    log_fn(f"\nRegistering mod index {new_mod_index} in: {os.path.basename(parent_bundle_path)}")
    env = UnityPy.load(parent_bundle_path)
    updated = 0

    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
            if "CompatibleModIds" not in tree:
                continue
            ids = tree["CompatibleModIds"]
            if new_mod_index not in ids:
                ids.append(new_mod_index)
                tree["CompatibleModIds"] = ids
                obj.save_typetree(tree)
                name = tree.get("ModParentName", "?")
                log_fn(f"  ✓ Added to: {name} (now {len(ids)} compatible mods)")
                updated += 1
        except:
            pass

    if updated > 0:
        os.makedirs(os.path.dirname(output_bundle_path), exist_ok=True)
        with open(output_bundle_path, "wb") as f:
            f.write(env.file.save())
        log_fn(f"  Saved updated parent bundle")

    return updated


# ── Mesh helpers (same as mesh swap tool) ────────────────────────────────────
def parse_obj(path):
    verts, normals, uvs, faces = [], [], [], []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("v "):
                x,y,z = map(float, line.split()[1:4])
                verts.append((x,y,z))
            elif line.startswith("vn "):
                x,y,z = map(float, line.split()[1:4])
                normals.append((x,y,z))
            elif line.startswith("vt "):
                u,v = map(float, line.split()[1:3])
                uvs.append((u,v))
            elif line.startswith("f "):
                parts = line.split()[1:]
                face = []
                for p in parts:
                    idx = p.split("/")
                    vi  = int(idx[0])-1
                    vti = int(idx[1])-1 if len(idx)>1 and idx[1] else 0
                    vni = int(idx[2])-1 if len(idx)>2 and idx[2] else 0
                    face.append((vi,vti,vni))
                for i in range(1, len(face)-1):
                    faces.append([face[0], face[i], face[i+1]])
    return verts, normals, uvs, faces


def build_vertex_buffer(verts, normals, uvs, faces):
    import struct
    def f16(v): return struct.pack("<e", float(v))
    def f32(v): return struct.pack("<f", float(v))

    vert_map = {}
    new_verts = []
    indices = []
    for face in faces:
        for (vi,vti,vni) in face:
            key = (vi,vti,vni)
            if key not in vert_map:
                vert_map[key] = len(new_verts)
                new_verts.append((vi,vti,vni))
            indices.append(vert_map[key])

    vbuf = bytearray()
    for (vi,vti,vni) in new_verts:
        vx,vy,vz = verts[vi]
        vbuf += f32(-vx); vbuf += f32(vy); vbuf += f32(vz)
        if normals and vni < len(normals):
            nx,ny,nz = normals[vni]
            vbuf += f16(-nx); vbuf += f16(ny); vbuf += f16(nz); vbuf += f16(0)
        else:
            for _ in range(4): vbuf += f16(0)
        vbuf += f16(1); vbuf += f16(0); vbuf += f16(0); vbuf += f16(1)
        if uvs and vti < len(uvs):
            u,v = uvs[vti]
            vbuf += f16(u); vbuf += f16(1-v)
        else:
            vbuf += f16(0); vbuf += f16(0)

    if len(new_verts) > 65535:
        ibuf = struct.pack(f"<{len(indices)}I", *indices)
        ifmt = 1
    else:
        ibuf = struct.pack(f"<{len(indices)}H", *indices)
        ifmt = 0
    return bytes(vbuf), ibuf, len(new_verts), len(indices), ifmt


def calc_aabb(verts, face_verts):
    used = [verts[vi] for (vi,_,_) in face_verts]
    xs=[v[0] for v in used]; ys=[v[1] for v in used]; zs=[v[2] for v in used]
    cx=(max(xs)+min(xs))/2; cy=(max(ys)+min(ys))/2; cz=(max(zs)+min(zs))/2
    ex=(max(xs)-min(xs))/2; ey=(max(ys)-min(ys))/2; ez=(max(zs)-min(zs))/2
    return cx,cy,cz,ex,ey,ez


# ── GUI ───────────────────────────────────────────────────────────────────────
class NewItemApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OPERATOR — New Item Creator")
        self.configure(bg="#080c0a")
        self.geometry("980x820")
        self.resizable(True, True)
        self._setup_styles()
        self._build_ui()
        if not UNITYPY_OK:
            messagebox.showerror("Missing", "pip install UnityPy")

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        bg = "#080c0a"
        panel = "#0e140f"
        accent = "#4ecf7a"
        text = "#d0e8d8"
        dim = "#3a5a42"
        entry_bg = "#111a12"

        style.configure(".", background=bg, foreground=text, font=("Consolas", 10))
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text)
        style.configure("TEntry", fieldbackground=entry_bg, foreground=text,
                        insertcolor=accent, bordercolor="#1a2a1a", relief="flat")
        style.configure("TButton", background="#111a12", foreground=text,
                        bordercolor="#1a3a1a", relief="flat", padding=6)
        style.map("TButton", background=[("active", "#1a2a1a")])
        style.configure("Accent.TButton", background=accent,
                        foreground="#080c0a",
                        font=("Consolas", 10, "bold"), padding=10)
        style.map("Accent.TButton", background=[("active", "#5ede8a")])
        style.configure("TNotebook", background=bg, bordercolor="#1a2a1a")
        style.configure("TNotebook.Tab", background="#0e140f",
                        foreground=dim, padding=[14, 5],
                        font=("Consolas", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", bg)],
                  foreground=[("selected", accent)])
        style.configure("TLabelframe", background=bg, bordercolor="#1a3a1a")
        style.configure("TLabelframe.Label", background=bg,
                        foreground=dim, font=("Consolas", 9))
        style.configure("TCombobox", fieldbackground=entry_bg,
                        foreground=text, selectbackground="#1a3a1a")
        style.configure("TScrollbar", background="#111a12",
                        troughcolor="#080c0a", arrowcolor=dim)

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg="#080c0a", pady=10)
        hdr.pack(fill="x", padx=16)
        tk.Label(hdr, text="⬡  OPERATOR NEW ITEM CREATOR",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 14, "bold")).pack(side="left")
        tk.Label(hdr, text="next AssetReferenceIndex: 1445",
                 bg="#080c0a", fg="#2a4a2a",
                 font=("Consolas", 9)).pack(side="right")
        tk.Frame(self, bg="#1a3a1a", height=1).pack(fill="x")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.nb = nb

        self._build_step1(nb)
        self._build_step2(nb)
        self._build_step3(nb)
        self._build_log(nb)

    # ── Step 1: Define the new mod ────────────────────────────────────────────
    def _build_step1(self, nb):
        frame = tk.Frame(nb, bg="#080c0a")
        nb.add(frame, text="  1 · Define Mod  ")

        tk.Label(frame, text="STEP 1 — DEFINE YOUR NEW MOD",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 12, "bold")).pack(anchor="w", padx=16, pady=(12,2))
        tk.Label(frame,
                 text="Configure the stats and type of your new weapon mod.",
                 bg="#080c0a", fg="#2a4a2a",
                 font=("Consolas", 9)).pack(anchor="w", padx=16, pady=(0,8))
        tk.Frame(frame, bg="#1a3a1a", height=1).pack(fill="x")

        scroll = tk.Frame(frame, bg="#080c0a")
        scroll.pack(fill="both", expand=True, padx=16, pady=12)

        # Asset Reference Index
        self._new_asset_idx = tk.StringVar(value=str(NEXT_ASSET_INDEX))
        self._form_row(scroll, "AssetReferenceIndex *",
                       self._new_asset_idx, 0,
                       tip="Unique ID — must not exist yet. Default is next available.")

        # Attachment type dropdown
        tk.Label(scroll, text="Attachment Type *",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 10), width=24,
                 anchor="w").grid(row=1, column=0, pady=4, sticky="w")

        type_options = [f"{k} — {v}" for k, v in sorted(ATTACH_TYPES.items())]
        self._attach_type_var = tk.StringVar(value=type_options[0])
        cb = ttk.Combobox(scroll, textvariable=self._attach_type_var,
                          values=type_options, width=44, state="readonly")
        cb.grid(row=1, column=1, pady=4, sticky="w")

        # Stats
        self._weight_var    = tk.StringVar(value="0.5")
        self._recoil_var    = tk.StringVar(value="0.0")
        self._ergo_var      = tk.StringVar(value="0.0")
        self._suppamt_var   = tk.StringVar(value="1.0")
        self._issuppressed  = tk.IntVar(value=0)
        self._issight       = tk.IntVar(value=0)
        self._isoptic       = tk.IntVar(value=0)
        self._barrellen_var = tk.StringVar(value="0")

        self._form_row(scroll, "Weight (kg)",        self._weight_var,  2)
        self._form_row(scroll, "Recoil modifier",    self._recoil_var,  3,
                       tip="Negative = less recoil")
        self._form_row(scroll, "Ergonomics modifier",self._ergo_var,    4)
        self._form_row(scroll, "SuppressedAmount",   self._suppamt_var, 5,
                       tip="1.0 = no suppression, 0.0 = fully suppressed")
        self._form_row(scroll, "Barrel Length",      self._barrellen_var, 6)

        # Boolean flags
        bool_frame = tk.Frame(scroll, bg="#080c0a")
        bool_frame.grid(row=7, column=0, columnspan=3, pady=8, sticky="w")

        def bool_toggle(var, label, col):
            tk.Checkbutton(bool_frame, text=label, variable=var,
                           bg="#080c0a", fg="#8ecfa8",
                           selectcolor="#0e2a1a",
                           activebackground="#080c0a",
                           font=("Consolas", 10),
                           relief="flat", cursor="hand2").grid(
                row=0, column=col, padx=(0, 20))

        bool_toggle(self._issuppressed, "isSuppressed", 0)
        bool_toggle(self._issight,      "isSight",      1)
        bool_toggle(self._isoptic,      "isOptic",      2)

        # Donor bundle section
        tk.Frame(scroll, bg="#1a3a1a", height=1).grid(
            row=8, column=0, columnspan=3, sticky="ew", pady=8)

        tk.Label(scroll, text="DONOR BUNDLE",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 10, "bold")).grid(
            row=9, column=0, sticky="w", pady=(0,4))

        tk.Label(scroll,
                 text="Choose a similar existing mod as the base.\n"
                      "Your new mod will be a duplicate with updated stats and mesh.",
                 bg="#080c0a", fg="#2a5a2a",
                 font=("Consolas", 9),
                 justify="left").grid(row=10, column=0, columnspan=3,
                                      sticky="w", pady=(0,6))

        self._donor_bundle_var = tk.StringVar()
        donor_row = tk.Frame(scroll, bg="#080c0a")
        donor_row.grid(row=11, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Entry(donor_row, textvariable=self._donor_bundle_var,
                  width=55).pack(side="left")
        ttk.Button(donor_row, text="Browse",
                   command=self._browse_donor).pack(side="left", padx=6)

        # Mesh OBJ
        tk.Label(scroll, text="Replacement Mesh (OBJ)",
                 bg="#080c0a", fg="#8ecfa8",
                 font=("Consolas", 10),
                 width=24, anchor="w").grid(row=12, column=0, pady=4, sticky="w")

        self._new_mesh_var = tk.StringVar()
        mesh_row = tk.Frame(scroll, bg="#080c0a")
        mesh_row.grid(row=12, column=1, sticky="w")
        ttk.Entry(mesh_row, textvariable=self._new_mesh_var,
                  width=45).pack(side="left")
        ttk.Button(mesh_row, text="Browse",
                   command=lambda: self._new_mesh_var.set(
                       filedialog.askopenfilename(
                           filetypes=[("OBJ", "*.obj"), ("All", "*.*")])
                       or self._new_mesh_var.get())).pack(side="left", padx=4)

        tk.Label(scroll, text="(optional — leave blank to keep donor mesh)",
                 bg="#080c0a", fg="#1a4a1a",
                 font=("Consolas", 8)).grid(row=12, column=2, sticky="w", padx=4)

        # Output
        tk.Label(scroll, text="Output Bundle Path *",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 10),
                 width=24, anchor="w").grid(row=13, column=0, pady=4, sticky="w")

        self._new_output_var = tk.StringVar()
        out_row = tk.Frame(scroll, bg="#080c0a")
        out_row.grid(row=13, column=1, sticky="w")
        ttk.Entry(out_row, textvariable=self._new_output_var,
                  width=45).pack(side="left")
        ttk.Button(out_row, text="Browse",
                   command=lambda: self._new_output_var.set(
                       filedialog.asksaveasfilename(
                           defaultextension=".bundle",
                           filetypes=[("Bundle", "*.bundle")])
                       or self._new_output_var.get())).pack(side="left", padx=4)

        tk.Frame(frame, bg="#1a3a1a", height=1).pack(fill="x", side="bottom")
        ttk.Button(frame, text="▶  Create New Bundle →",
                   style="Accent.TButton",
                   command=self._run_create).pack(
            side="bottom", pady=10, padx=16, anchor="e")

    # ── Step 2: Register the mod ──────────────────────────────────────────────
    def _build_step2(self, nb):
        frame = tk.Frame(nb, bg="#080c0a")
        nb.add(frame, text="  2 · Register  ")

        tk.Label(frame, text="STEP 2 — REGISTER IN WEAPON SLOTS",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 12, "bold")).pack(anchor="w", padx=16, pady=(12,2))
        tk.Label(frame,
                 text="Add your new mod's ID to WeaponModParent bundles so it\n"
                      "appears as an option in the correct attachment slots in-game.",
                 bg="#080c0a", fg="#2a4a2a",
                 font=("Consolas", 9),
                 justify="left").pack(anchor="w", padx=16, pady=(0,8))
        tk.Frame(frame, bg="#1a3a1a", height=1).pack(fill="x")

        f = tk.Frame(frame, bg="#080c0a")
        f.pack(fill="x", padx=16, pady=12)

        self._reg_bundle_var = tk.StringVar()
        self._reg_output_var = tk.StringVar()
        self._reg_mod_idx_var = tk.StringVar(value=str(NEXT_ASSET_INDEX))

        def reg_row(label, var, row, browse_fn=None):
            tk.Label(f, text=label, bg="#080c0a", fg="#8ecfa8",
                     font=("Consolas", 10), width=26,
                     anchor="w").grid(row=row, column=0, pady=4, sticky="w")
            r = tk.Frame(f, bg="#080c0a")
            r.grid(row=row, column=1, sticky="w")
            ttk.Entry(r, textvariable=var, width=50).pack(side="left")
            if browse_fn:
                ttk.Button(r, text="Browse",
                           command=browse_fn).pack(side="left", padx=4)

        reg_row("WeaponModParent bundle:",
                self._reg_bundle_var, 0,
                lambda: self._reg_bundle_var.set(
                    filedialog.askopenfilename(
                        filetypes=[("Bundle", "*.bundle"), ("All", "*.*")])))
        reg_row("Output bundle path:",
                self._reg_output_var, 1,
                lambda: self._reg_output_var.set(
                    filedialog.asksaveasfilename(
                        defaultextension=".bundle")))
        reg_row("Mod index to register:",
                self._reg_mod_idx_var, 2)

        tk.Label(f,
                 text="TIP: Load sharedassets0.assets here to register across\n"
                      "all weapon mod parent slots at once.",
                 bg="#080c0a", fg="#1a4a1a",
                 font=("Consolas", 8),
                 justify="left").grid(row=3, column=0, columnspan=2,
                                      pady=(4,0), sticky="w")

        ttk.Button(frame, text="▶  Register Mod ID →",
                   style="Accent.TButton",
                   command=self._run_register).pack(
            side="bottom", pady=10, padx=16, anchor="e")
        tk.Frame(frame, bg="#1a3a1a", height=1).pack(
            fill="x", side="bottom")

    # ── Step 3: Deploy ────────────────────────────────────────────────────────
    def _build_step3(self, nb):
        frame = tk.Frame(nb, bg="#080c0a")
        nb.add(frame, text="  3 · Deploy  ")

        tk.Label(frame, text="STEP 3 — DEPLOY TO GAME",
                 bg="#080c0a", fg="#4ecf7a",
                 font=("Consolas", 12, "bold")).pack(anchor="w", padx=16, pady=(12,2))

        instructions = """HOW TO DEPLOY YOUR NEW MOD:

1. Copy the new .bundle file from Step 1 into:
   OPERATOR_Data\\StreamingAssets\\aa\\StandaloneWindows64\\
   
   Give it any filename — the game loads all .bundle files in that folder.
   Suggested name: weaponmods_stripped_assets_YOURMODNAME_RANDOMHASH.bundle

2. Copy the modified WeaponModParent bundle from Step 2 back to:
   OPERATOR_Data\\StreamingAssets\\aa\\StandaloneWindows64\\
   replacing the original file.

3. Launch OPERATOR — your new mod should appear in the appropriate
   attachment slot when building a weapon loadout.

TROUBLESHOOTING:

  Game crashes on launch
    → AssetReferenceIndex conflict — try a higher index value
    → Submesh count mismatch in mesh — check Blender material slots

  Mod doesn't appear in slot
    → Registration incomplete — make sure CompatibleModIds was updated
    → Wrong attachmentType — check the type map and use correct number

  Mod appears but invisible
    → Missing UV map in OBJ export
    → Mesh normals flipped — recalculate outside in Blender

  Weight/stats wrong
    → Check values in Step 1 and recreate bundle

NOTES:
  • The game discovers bundles dynamically — no catalog file to edit
  • Each new mod needs a unique AssetReferenceIndex
  • You can add the same mod to multiple WeaponModParent bundles
    to make it compatible with multiple weapons"""

        txt = scrolledtext.ScrolledText(
            frame, bg="#050c06", fg="#4a8a5a",
            font=("Consolas", 10), relief="flat",
            wrap="word", state="normal")
        txt.pack(fill="both", expand=True, padx=12, pady=8)
        txt.insert("1.0", instructions)
        txt.configure(state="disabled")

    # ── Log tab ───────────────────────────────────────────────────────────────
    def _build_log(self, nb):
        frame = tk.Frame(nb, bg="#080c0a")
        nb.add(frame, text="  Log  ")
        self._log_box = scrolledtext.ScrolledText(
            frame, bg="#030c04", fg="#3ecf6a",
            font=("Consolas", 9), relief="flat",
            state="disabled", wrap="word")
        self._log_box.pack(fill="both", expand=True, padx=8, pady=8)
        ttk.Button(frame, text="Clear",
                   command=self._clear_log).pack(pady=(0,8))

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _form_row(self, parent, label, var, row, tip=None):
        tk.Label(parent, text=label,
                 bg="#080c0a", fg="#4ecf7a" if row == 0 else "#8ecfa8",
                 font=("Consolas", 10), width=24,
                 anchor="w").grid(row=row, column=0, pady=3, sticky="w")
        ttk.Entry(parent, textvariable=var, width=18).grid(
            row=row, column=1, pady=3, sticky="w")
        if tip:
            tk.Label(parent, text=tip, bg="#080c0a", fg="#1a4a1a",
                     font=("Consolas", 8)).grid(
                row=row, column=2, sticky="w", padx=8)

    def _browse_donor(self):
        p = filedialog.askopenfilename(
            filetypes=[("Bundle", "*.bundle"), ("All", "*.*")])
        if p:
            self._donor_bundle_var.set(p)
            name = os.path.basename(p)
            base = name.replace(".bundle", "")
            # Suggest output name
            out = os.path.join(
                os.path.dirname(p),
                f"CUSTOM_{base[:40]}.bundle")
            self._new_output_var.set(out)

    def _run_create(self):
        donor = self._donor_bundle_var.get().strip()
        output = self._new_output_var.get().strip()
        mesh = self._new_mesh_var.get().strip()

        if not donor or not os.path.isfile(donor):
            messagebox.showwarning("Missing", "Select a donor bundle file.")
            return
        if not output:
            messagebox.showwarning("Missing", "Set an output path.")
            return

        try:
            asset_idx = int(self._new_asset_idx.get())
            attach_raw = self._attach_type_var.get()
            attach_type = int(attach_raw.split(" — ")[0])
        except ValueError:
            messagebox.showerror("Error", "Invalid AssetReferenceIndex or attachment type.")
            return

        stats = {
            "AssetReferenceIndex": asset_idx,
            "attachmentType":      attach_type,
            "Weight":              float(self._weight_var.get() or 0),
            "Recoil":              float(self._recoil_var.get() or 0),
            "Ergonomics":          float(self._ergo_var.get() or 0),
            "SuppressedAmount":    float(self._suppamt_var.get() or 1),
            "isSuppressed":        self._issuppressed.get(),
            "isSight":             self._issight.get(),
            "isOptic":             self._isoptic.get(),
            "barrelLength":        int(self._barrellen_var.get() or 0),
        }

        self._log(f"\n{'─'*50}")
        self._log(f"Creating new mod bundle...")
        self._log(f"  AssetReferenceIndex: {asset_idx}")
        self._log(f"  Attachment type: {attach_type} ({ATTACH_TYPES.get(attach_type, '?')})")
        self.nb.select(3)

        def run():
            try:
                ok = create_new_mod_bundle(
                    donor, mesh or None, output, asset_idx, stats, self._log)
                if ok:
                    self._log(f"\n✓ SUCCESS!")
                    self._log(f"  Now go to Step 2 to register this mod in weapon slots.")
                    self.after(0, lambda: messagebox.showinfo(
                        "Bundle Created!",
                        f"New mod bundle saved to:\n{output}\n\n"
                        f"Next: Go to Step 2 to register mod index {asset_idx} "
                        f"in weapon slot bundles so it appears in-game."))
                    self.after(0, lambda: self.nb.select(1))
                else:
                    self._log("✗ Bundle creation failed — check log.")
            except Exception as e:
                self._log(f"✗ Error: {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    def _run_register(self):
        bundle = self._reg_bundle_var.get().strip()
        output = self._reg_output_var.get().strip()
        try:
            mod_idx = int(self._reg_mod_idx_var.get())
        except ValueError:
            messagebox.showerror("Error", "Invalid mod index.")
            return
        if not bundle or not os.path.isfile(bundle):
            messagebox.showwarning("Missing", "Select a WeaponModParent bundle.")
            return
        if not output:
            messagebox.showwarning("Missing", "Set output path.")
            return

        self._log(f"\nRegistering mod index {mod_idx}...")
        self.nb.select(3)

        def run():
            try:
                count = register_mod_in_parent(bundle, output, mod_idx, self._log)
                if count > 0:
                    self._log(f"\n✓ Registered in {count} parent slot(s)")
                    self._log(f"  Go to Step 3 for deployment instructions.")
                    self.after(0, lambda: messagebox.showinfo(
                        "Registered!",
                        f"Mod index {mod_idx} added to {count} slot(s).\n\n"
                        f"See Step 3 for how to deploy both files to the game."))
                    self.after(0, lambda: self.nb.select(2))
                else:
                    self._log("  No WeaponModParent slots found in this bundle.")
                    self._log("  Try a different bundle or sharedassets0.assets.")
            except Exception as e:
                self._log(f"✗ Error: {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    def _log(self, msg, end=False):
        def _do():
            self._log_box.configure(state="normal")
            if end:
                self._log_box.delete("end-2l", "end-1l")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")


if __name__ == "__main__":
    app = NewItemApp()
    app.mainloop()