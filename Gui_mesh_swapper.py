import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import struct
import os
import sys
import traceback

# ── Try importing UnityPy ────────────────────────────────────────────────────
try:
    import UnityPy
    import UnityPy.config
    from UnityPy.enums import ClassIDType
    UNITYPY_AVAILABLE = True
except ImportError:
    UNITYPY_AVAILABLE = False

UnityPy.config.FALLBACK_UNITY_VERSION = "6000.3.8f1"

# ── Helpers ──────────────────────────────────────────────────────────────────
def to_f16(v):
    return struct.pack('<e', float(v))

def to_f32(v):
    return struct.pack('<f', float(v))

def parse_obj(path):
    verts, normals, uvs, faces = [], [], [], []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('v '):
                x,y,z = map(float, line.split()[1:4])
                verts.append((x,y,z))
            elif line.startswith('vn '):
                x,y,z = map(float, line.split()[1:4])
                normals.append((x,y,z))
            elif line.startswith('vt '):
                u,v = map(float, line.split()[1:3])
                uvs.append((u,v))
            elif line.startswith('f '):
                parts = line.split()[1:]
                face = []
                for p in parts:
                    idx = p.split('/')
                    vi  = int(idx[0])-1
                    vti = int(idx[1])-1 if len(idx)>1 and idx[1] else 0
                    vni = int(idx[2])-1 if len(idx)>2 and idx[2] else 0
                    face.append((vi,vti,vni))
                for i in range(1, len(face)-1):
                    faces.append([face[0], face[i], face[i+1]])
    return verts, normals, uvs, faces

def build_vertex_buffer(verts, normals, uvs, faces):
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

    vertex_buffer = bytearray()
    for (vi,vti,vni) in new_verts:
        vx,vy,vz = verts[vi]
        vertex_buffer += to_f32(-vx)
        vertex_buffer += to_f32(vy)
        vertex_buffer += to_f32(vz)
        if normals and vni < len(normals):
            nx,ny,nz = normals[vni]
            vertex_buffer += to_f16(-nx)
            vertex_buffer += to_f16(ny)
            vertex_buffer += to_f16(nz)
            vertex_buffer += to_f16(0.0)
        else:
            for _ in range(4): vertex_buffer += to_f16(0.0)
        vertex_buffer += to_f16(1.0)
        vertex_buffer += to_f16(0.0)
        vertex_buffer += to_f16(0.0)
        vertex_buffer += to_f16(1.0)
        if uvs and vti < len(uvs):
            u,v = uvs[vti]
            vertex_buffer += to_f16(u)
            vertex_buffer += to_f16(1.0-v)
        else:
            vertex_buffer += to_f16(0.0)
            vertex_buffer += to_f16(0.0)

    if len(new_verts) > 65535:
        index_buffer = struct.pack(f'<{len(indices)}I', *indices)
        index_format = 1
    else:
        index_buffer = struct.pack(f'<{len(indices)}H', *indices)
        index_format = 0

    return bytes(vertex_buffer), index_buffer, len(new_verts), len(indices), index_format

def calc_aabb(verts, face_verts):
    used = [verts[vi] for (vi,_,_) in face_verts]
    xs=[v[0] for v in used]; ys=[v[1] for v in used]; zs=[v[2] for v in used]
    cx=(max(xs)+min(xs))/2; cy=(max(ys)+min(ys))/2; cz=(max(zs)+min(zs))/2
    ex=(max(xs)-min(xs))/2; ey=(max(ys)-min(ys))/2; ez=(max(zs)-min(zs))/2
    return cx,cy,cz,ex,ey,ez

def list_meshes_in_bundle(bundle_path, log):
    env = UnityPy.load(bundle_path)
    meshes = []
    for obj in env.objects:
        if obj.type == ClassIDType.Mesh:
            try:
                tree = obj.read_typetree()
                name = tree.get('m_Name', '(unnamed)')
                meshes.append(name)
                log(f"  Found mesh: {name}")
            except Exception as e:
                log(f"  Could not read mesh: {e}")
    return meshes

def do_swap(bundle_path, obj_path, output_path, mesh_name, log):
    log("Parsing OBJ...")
    verts, normals, uvs, faces = parse_obj(obj_path)
    log(f"  {len(verts)} verts, {len(normals)} normals, {len(faces)} tris")

    log("Building vertex/index buffers...")
    vbuf, ibuf, vert_count, idx_count, idx_fmt = build_vertex_buffer(verts, normals, uvs, faces)
    log(f"  Built {vert_count} vertices, {idx_count//3} triangles")

    log(f"Loading bundle...")
    env = UnityPy.load(bundle_path)

    found = False
    for obj in env.objects:
        if obj.type == ClassIDType.Mesh:
            tree = obj.read_typetree()
            if tree.get('m_Name') != mesh_name:
                continue
            found = True
            log(f"Found target mesh: {mesh_name}")

            all_face_verts = [fv for face in faces for fv in face]
            cx,cy,cz,ex,ey,ez = calc_aabb(verts, all_face_verts)

            tree['m_IndexBuffer'] = ibuf
            tree['m_IndexFormat'] = idx_fmt
            tree['m_VertexData']['m_VertexCount'] = vert_count
            tree['m_VertexData']['m_DataSize'] = vbuf
            tree['m_StreamData']['offset'] = 0
            tree['m_StreamData']['size'] = 0
            tree['m_StreamData']['path'] = ''
            tree['m_SubMeshes'][0]['indexCount'] = idx_count
            tree['m_SubMeshes'][0]['vertexCount'] = vert_count
            tree['m_SubMeshes'][0]['firstByte'] = 0
            tree['m_SubMeshes'][0]['firstVertex'] = 0
            tree['m_SubMeshes'][0]['baseVertex'] = 0
            tree['m_SubMeshes'][0]['localAABB'] = {
                'm_Center': {'x':-cx,'y':cy,'z':cz},
                'm_Extent': {'x':ex,'y':ey,'z':ez}
            }
            tree['m_LocalAABB'] = {
                'm_Center': {'x':-cx,'y':cy,'z':cz},
                'm_Extent': {'x':ex,'y':ey,'z':ez}
            }
            obj.save_typetree(tree)
            log("✓ Mesh data updated")
            break

    if not found:
        log(f"✗ Mesh '{mesh_name}' not found in bundle!")
        log("Available meshes:")
        for obj in env.objects:
            if obj.type == ClassIDType.Mesh:
                try:
                    t = obj.read_typetree()
                    log(f"  - {t.get('m_Name','?')}")
                except:
                    pass
        return False

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(env.file.save())
    log(f"✓ Saved to: {output_path}")
    return True


# ── GUI ──────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OPERATOR Mesh Swapper")
        self.resizable(False, False)
        self.configure(bg="#1e1e1e")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background="#1e1e1e", foreground="#e0e0e0",
                        fieldbackground="#2d2d2d", bordercolor="#444")
        style.configure("TButton", background="#3a3a3a", foreground="#e0e0e0",
                        padding=6, relief="flat")
        style.map("TButton", background=[("active","#505050")])
        style.configure("Accent.TButton", background="#0078d4", foreground="white", padding=8)
        style.map("Accent.TButton", background=[("active","#005fa3")])
        style.configure("TEntry", fieldbackground="#2d2d2d", foreground="#e0e0e0",
                        insertcolor="#e0e0e0")
        style.configure("TLabel", background="#1e1e1e", foreground="#e0e0e0")
        style.configure("TLabelframe", background="#1e1e1e", foreground="#aaa")
        style.configure("TLabelframe.Label", background="#1e1e1e", foreground="#aaa")
        style.configure("TNotebook", background="#1e1e1e")
        style.configure("TNotebook.Tab", background="#2d2d2d", foreground="#ccc", padding=[12,4])
        style.map("TNotebook.Tab", background=[("selected","#1e1e1e")], foreground=[("selected","white")])

        if not UNITYPY_AVAILABLE:
            tk.Label(self, text="⚠ UnityPy not found!\nRun: pip install UnityPy",
                     bg="#1e1e1e", fg="#ff6b6b", font=("Segoe UI",11),
                     justify="center").pack(pady=20, padx=20)
            return

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Tab 1: Swap ──────────────────────────────────────────────────────
        swap_frame = tk.Frame(nb, bg="#1e1e1e")
        nb.add(swap_frame, text="  Mesh Swap  ")

        self._make_section(swap_frame, "Paths", [
            ("Bundle File (.bundle):",   "bundle",  "file",   "bundle"),
            ("Replacement OBJ:",         "obj",     "file",   "obj"),
            ("Output Bundle:",           "output",  "save",   "bundle"),
        ])

        mf = ttk.LabelFrame(swap_frame, text="Mesh Name", padding=8)
        mf.pack(fill="x", padx=12, pady=(0,6))

        name_row = tk.Frame(mf, bg="#1e1e1e")
        name_row.pack(fill="x")
        self.mesh_name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.mesh_name_var, width=45).pack(side="left", padx=(0,6))
        ttk.Button(name_row, text="Scan Bundle for Meshes",
                   command=self._scan_bundle).pack(side="left")

        self.mesh_listbox = tk.Listbox(mf, height=4, bg="#2d2d2d", fg="#e0e0e0",
                                       selectbackground="#0078d4", activestyle="none",
                                       relief="flat", bd=0)
        self.mesh_listbox.pack(fill="x", pady=(6,0))
        self.mesh_listbox.bind("<<ListboxSelect>>", self._on_mesh_select)

        ttk.Button(swap_frame, text="▶  Run Mesh Swap", style="Accent.TButton",
                   command=self._run_swap).pack(pady=10, padx=12, fill="x")

        # ── Tab 2: Log ───────────────────────────────────────────────────────
        log_frame = tk.Frame(nb, bg="#1e1e1e")
        nb.add(log_frame, text="  Log  ")

        self.log_box = scrolledtext.ScrolledText(
            log_frame, bg="#0d0d0d", fg="#00ff88", insertbackground="white",
            font=("Consolas",10), relief="flat", state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=8)

        ttk.Button(log_frame, text="Clear Log", command=self._clear_log).pack(pady=(0,8))

        self.nb = nb
        self._log("OPERATOR Mesh Swapper ready.")
        self._log("UnityPy version: " + UnityPy.__version__ if hasattr(UnityPy,'__version__') else "UnityPy loaded")

    # ── Path section builder ─────────────────────────────────────────────────
    def _make_section(self, parent, title, fields):
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", padx=12, pady=(8,4))
        self._vars = getattr(self, '_vars', {})
        for label, key, mode, ext in fields:
            row = tk.Frame(frame, bg="#1e1e1e")
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg="#1e1e1e", fg="#aaa",
                     width=22, anchor="w").pack(side="left")
            var = tk.StringVar()
            self._vars[key] = var
            ttk.Entry(row, textvariable=var, width=38).pack(side="left", padx=(0,6))
            def make_cmd(v=var, m=mode, e=ext):
                def cmd():
                    if m == "file":
                        p = filedialog.askopenfilename(filetypes=[(e.upper(), f"*.{e}"), ("All","*.*")])
                    elif m == "save":
                        p = filedialog.asksaveasfilename(defaultextension=f".{e}",
                            filetypes=[(e.upper(), f"*.{e}")])
                    else:
                        p = filedialog.askdirectory()
                    if p: v.set(p)
                return cmd
            ttk.Button(row, text="Browse", command=make_cmd(), width=8).pack(side="left")

    # ── Actions ──────────────────────────────────────────────────────────────
    def _scan_bundle(self):
        bp = self._vars.get('bundle', tk.StringVar()).get()
        if not bp or not os.path.isfile(bp):
            messagebox.showwarning("No bundle", "Please select a bundle file first.")
            return
        self.mesh_listbox.delete(0, "end")
        self._log(f"Scanning: {os.path.basename(bp)}")
        def run():
            try:
                meshes = list_meshes_in_bundle(bp, self._log)
                for m in meshes:
                    self.mesh_listbox.insert("end", m)
                if not meshes:
                    self._log("No meshes found.")
            except Exception as e:
                self._log(f"✗ Error: {e}")
        threading.Thread(target=run, daemon=True).start()
        self.nb.select(1)

    def _on_mesh_select(self, _):
        sel = self.mesh_listbox.curselection()
        if sel:
            self.mesh_name_var.set(self.mesh_listbox.get(sel[0]))

    def _run_swap(self):
        bp  = self._vars['bundle'].get()
        op  = self._vars['obj'].get()
        out = self._vars['output'].get()
        mn  = self.mesh_name_var.get().strip()

        errors = []
        if not bp or not os.path.isfile(bp):   errors.append("Bundle file missing")
        if not op or not os.path.isfile(op):   errors.append("OBJ file missing")
        if not out:                             errors.append("Output path missing")
        if not mn:                              errors.append("Mesh name missing")
        if errors:
            messagebox.showerror("Missing fields", "\n".join(errors))
            return

        self._log("─" * 50)
        self._log(f"Starting swap...")
        self._log(f"  Bundle:  {os.path.basename(bp)}")
        self._log(f"  OBJ:     {os.path.basename(op)}")
        self._log(f"  Mesh:    {mn}")
        self._log(f"  Output:  {out}")
        self.nb.select(1)

        def run():
            try:
                success = do_swap(bp, op, out, mn, self._log)
                if success:
                    self._log("─" * 50)
                    self._log("✓ DONE! Copy the output bundle to your game folder.")
                    self.after(0, lambda: messagebox.showinfo(
                        "Success!",
                        f"Mesh swap complete!\n\nOutput:\n{out}\n\nCopy this file to your game's bundle folder and rename it to match the original."))
            except Exception as e:
                self._log(f"✗ FAILED: {e}")
                self._log(traceback.format_exc())
                self.after(0, lambda: messagebox.showerror("Error", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _log(self, msg):
        def _do():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
