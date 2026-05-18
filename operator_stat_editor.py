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

GAME_BUNDLE_DIR = ""
DUMP_DIR = ""

# Field type hints for smart editing
BOOL_FIELDS = {
    "isSuppressed", "isSight", "isOptic", "isNVGOptic", "isGangsta",
    "isBoltRelease", "ForceIsMuzzle", "IsPartOfMagMod", "changeChargingHandleOffset",
    "mulitplePoses", "zeroInstant", "zeroTransform", "pistolMode", "isWeaponLight",
    "m_Enabled", "m_IsActive", "isAnimated", "acceptNullCompatibility",
    "acceptClassCompatibility", "enableOffset", "disableStockOffset", "RequiredPart"
}

INT_FIELDS = {
    "attachmentType", "MyModIndex", "barrelLength", "MLOK_Slot_Adjustment",
    "interactionHand", "magExtensionAmount", "AmmoReferenceIndex",
    "AssetReferenceIndex", "ModInfoIndex", "characterSlot", "modSlot",
    "ammoAmount", "ammoAmountMax", "ammoAmountMagExtension"
}

FLOAT_FIELDS = {
    "Weight", "Ergonomics", "Recoil", "SuppressedAmount", "LerpSpeed",
    "interactionTime", "transitionTime", "animTransitionTime", "fixedOffset",
    "maxPistolOffset", "minPistolOffset", "CantedRotation", "MinOffset",
    "MaxOffset", "WholeNumbersOffset", "BaseWeight", "BulletWeight",
    "MagnificationMin", "MagnificationMax", "ReticleScale"
}

SKIP_FIELDS = {
    "m_GameObject", "m_Script", "m_Component", "ChildAttachmentSlots",
    "AlsoFulfilsRequirementOf", "leftHands", "Lasers", "Flashlights",
    "allLights", "lightOriginalValues", "PIP", "nonPIP", "interactionSystem",
    "leftHand", "RightHand", "weaponMods", "animator", "ironSights",
    "myParent", "muzzleDevice", "ammunitionParent", "ObjectiveLensOverridePosition",
    "ScopeCamera", "NvgScopeController", "mainADS", "chargingHandleOffset",
    "adsToDisableIfHasAttachment", "bulletPositions", "allBullets",
    "bulletSpawnWith", "allMagazineMeshes", "allChildModParents",
    "_collider", "_rigidbody", "audioSource", "audioClips", "MyWeaponMod",
    "WeaponMods", "AttachedMod", "myParent", "magazine", "ScopeParent",
    "nvgScopeObjectiveOverride", "nvgScopeCamera", "NvgScopeController",
    "modUI", "characterSettings", "skinnedMesh", "skinnedMeshes",
    "CompatibleModIds", "attachmentType_list", "AllMaterialReferences",
    "m_Metadata", "m_TableData", "m_Entries", "m_SharedData"
}

EDITABLE_MONO_TYPES = {
    "WeaponMod", "WeaponModParent", "CharacterMod", "Magazine",
    "ModMaterialLink", "Bullet"
}


def find_mono_bundle(path_id, bundle_dir, assets_file, log_fn):
    """Search bundles and sharedassets for a MonoBehaviour by PathID."""
    log_fn(f"Searching sharedassets0.assets...")
    try:
        env = UnityPy.load(assets_file)
        for obj in env.objects:
            if obj.path_id == path_id:
                log_fn(f"  Found in sharedassets0.assets")
                return assets_file, obj.path_id
    except Exception as e:
        log_fn(f"  sharedassets error: {e}")

    log_fn(f"Searching bundles (this may take a minute)...")
    files = [f for f in os.listdir(bundle_dir) if os.path.isfile(os.path.join(bundle_dir, f))]
    for i, filename in enumerate(files):
        filepath = os.path.join(bundle_dir, filename)
        log_fn(f"  ({i+1}/{len(files)}) {filename[:60]}", end=True)
        try:
            env = UnityPy.load(filepath)
            for obj in env.objects:
                if obj.path_id == path_id:
                    log_fn(f"\n  ✓ Found in: {filename}")
                    return filepath, path_id
        except:
            pass
    log_fn("\n  ✗ Not found in any file.")
    return None, None


def get_all_objects_in_bundle(bundle_path):
    """Return all readable objects with their typetrees."""
    env = UnityPy.load(bundle_path)
    results = []
    for obj in env.objects:
        try:
            tree = obj.read_typetree()
            results.append({
                "path_id": obj.path_id,
                "type": obj.type.name,
                "name": tree.get("m_Name", ""),
                "tree": tree
            })
        except:
            try:
                results.append({
                    "path_id": obj.path_id,
                    "type": obj.type.name,
                    "name": "",
                    "tree": None
                })
            except:
                pass
    return env, results


def write_modified_bundle(env, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(env.file.save())


class StatEditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OPERATOR Stat Editor")
        self.configure(bg="#0a0a0a")
        self.resizable(True, True)
        self.geometry("1100x780")

        self._current_env = None
        self._current_bundle_path = None
        self._current_obj = None
        self._current_tree = None
        self._field_vars = {}
        self._objects_cache = []

        self._setup_styles()
        self._build_ui()

        if not UNITYPY_OK:
            messagebox.showerror("Missing Dependency", "UnityPy not found!\nRun: pip install UnityPy")

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        bg = "#0a0a0a"
        panel = "#111111"
        accent = "#c8a96e"
        text = "#e8e0d0"
        dim = "#555555"
        entry_bg = "#1a1a1a"

        style.configure(".", background=bg, foreground=text, font=("Consolas", 10))
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text)
        style.configure("Dim.TLabel", background=bg, foreground=dim)
        style.configure("Accent.TLabel", background=bg, foreground=accent, font=("Consolas", 11, "bold"))
        style.configure("TEntry", fieldbackground=entry_bg, foreground=text,
                        insertcolor=accent, bordercolor="#333", relief="flat")
        style.configure("TButton", background="#1e1e1e", foreground=text,
                        bordercolor="#333", relief="flat", padding=6)
        style.map("TButton", background=[("active", "#2a2a2a")])
        style.configure("Accent.TButton", background=accent, foreground="#0a0a0a",
                        font=("Consolas", 10, "bold"), padding=8)
        style.map("Accent.TButton", background=[("active", "#d4b87a")])
        style.configure("TNotebook", background=bg, bordercolor="#222")
        style.configure("TNotebook.Tab", background="#111", foreground=dim,
                        padding=[14, 5], font=("Consolas", 10))
        style.map("TNotebook.Tab", background=[("selected", bg)],
                  foreground=[("selected", accent)])
        style.configure("TLabelframe", background=bg, bordercolor="#2a2a2a")
        style.configure("TLabelframe.Label", background=bg, foreground=dim,
                        font=("Consolas", 9))
        style.configure("Treeview", background="#111", foreground=text,
                        fieldbackground="#111", bordercolor="#222",
                        font=("Consolas", 10))
        style.configure("Treeview.Heading", background="#1a1a1a", foreground=accent,
                        font=("Consolas", 10, "bold"))
        style.map("Treeview", background=[("selected", "#2a2010")])
        style.configure("TSeparator", background="#222")
        style.configure("TCheckbutton", background=bg, foreground=text)
        style.map("TCheckbutton", background=[("active", bg)])
        style.configure("TScrollbar", background="#1a1a1a", troughcolor="#0a0a0a",
                        bordercolor="#0a0a0a", arrowcolor=dim)

    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg="#0a0a0a", pady=12)
        header.pack(fill="x", padx=16)
        tk.Label(header, text="◈  OPERATOR STAT EDITOR",
                 bg="#0a0a0a", fg="#c8a96e",
                 font=("Consolas", 15, "bold")).pack(side="left")
        tk.Label(header, text="MonoBehaviour Editor  //  Unity 6000.3.8f1",
                 bg="#0a0a0a", fg="#444",
                 font=("Consolas", 9)).pack(side="right", pady=4)

        tk.Frame(self, bg="#2a2a2a", height=1).pack(fill="x", padx=0)

        # Main notebook
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=0, pady=0)
        self.nb = nb

        # Tab 1: Bundle Editor
        self._build_bundle_tab(nb)

        # Tab 2: Batch Stats
        self._build_batch_tab(nb)

        # Tab 3: Log
        self._build_log_tab(nb)

        # Tab 4: Settings
        self._build_settings_tab(nb)

    # ── Bundle Editor Tab ─────────────────────────────────────────────────────
    def _build_bundle_tab(self, nb):
        frame = tk.Frame(nb, bg="#0a0a0a")
        nb.add(frame, text="  Bundle Editor  ")

        # Top bar
        top = tk.Frame(frame, bg="#111111", pady=8)
        top.pack(fill="x", padx=0)

        tk.Label(top, text="Bundle:", bg="#111111", fg="#888",
                 font=("Consolas", 10)).pack(side="left", padx=(12, 4))
        self._bundle_var = tk.StringVar()
        bundle_entry = ttk.Entry(top, textvariable=self._bundle_var, width=55)
        bundle_entry.pack(side="left", padx=(0, 4))
        ttk.Button(top, text="Browse", command=self._browse_bundle).pack(side="left", padx=2)
        ttk.Button(top, text="Load", style="Accent.TButton",
                   command=self._load_bundle).pack(side="left", padx=(6, 12))

        tk.Frame(frame, bg="#1a1a1a", height=1).pack(fill="x")

        # Split pane
        pane = tk.PanedWindow(frame, orient="horizontal", bg="#0a0a0a",
                               sashwidth=4, sashrelief="flat")
        pane.pack(fill="both", expand=True)

        # Left: object list
        left = tk.Frame(pane, bg="#0a0a0a", width=280)
        pane.add(left, minsize=200)

        tk.Label(left, text="OBJECTS", bg="#0a0a0a", fg="#555",
                 font=("Consolas", 9)).pack(anchor="w", padx=10, pady=(8, 2))

        tree_frame = tk.Frame(left, bg="#0a0a0a")
        tree_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._obj_tree = ttk.Treeview(tree_frame, columns=("type", "name"),
                                       show="headings", selectmode="browse")
        self._obj_tree.heading("type", text="Type")
        self._obj_tree.heading("name", text="Name")
        self._obj_tree.column("type", width=110, anchor="w")
        self._obj_tree.column("name", width=150, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self._obj_tree.yview)
        self._obj_tree.configure(yscrollcommand=vsb.set)
        self._obj_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._obj_tree.bind("<<TreeviewSelect>>", self._on_obj_select)

        # Right: field editor
        right = tk.Frame(pane, bg="#0a0a0a")
        pane.add(right, minsize=400)

        # Object info bar
        self._obj_info_var = tk.StringVar(value="No object selected")
        tk.Label(right, textvariable=self._obj_info_var,
                 bg="#111", fg="#c8a96e",
                 font=("Consolas", 10, "bold"),
                 anchor="w", padx=12, pady=6).pack(fill="x")

        tk.Frame(right, bg="#1a1a1a", height=1).pack(fill="x")

        # Fields scroll area
        fields_outer = tk.Frame(right, bg="#0a0a0a")
        fields_outer.pack(fill="both", expand=True)

        self._fields_canvas = tk.Canvas(fields_outer, bg="#0a0a0a",
                                         highlightthickness=0)
        fields_vsb = ttk.Scrollbar(fields_outer, orient="vertical",
                                    command=self._fields_canvas.yview)
        self._fields_canvas.configure(yscrollcommand=fields_vsb.set)
        self._fields_canvas.pack(side="left", fill="both", expand=True)
        fields_vsb.pack(side="right", fill="y")

        self._fields_frame = tk.Frame(self._fields_canvas, bg="#0a0a0a")
        self._fields_window = self._fields_canvas.create_window(
            (0, 0), window=self._fields_frame, anchor="nw")
        self._fields_frame.bind("<Configure>", self._on_fields_configure)
        self._fields_canvas.bind("<Configure>", self._on_canvas_configure)
        self._fields_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # Bottom action bar
        tk.Frame(right, bg="#1a1a1a", height=1).pack(fill="x")
        action_bar = tk.Frame(right, bg="#111", pady=8)
        action_bar.pack(fill="x")

        ttk.Button(action_bar, text="↺  Reset Fields",
                   command=self._reset_fields).pack(side="left", padx=(12, 4))
        self._output_var = tk.StringVar()
        ttk.Entry(action_bar, textvariable=self._output_var,
                  width=30).pack(side="left", padx=4)
        ttk.Button(action_bar, text="📁",
                   command=self._browse_output).pack(side="left", padx=2)
        ttk.Button(action_bar, text="✓  Save Modified Bundle",
                   style="Accent.TButton",
                   command=self._save_bundle).pack(side="right", padx=12)

    # ── Batch Stats Tab ───────────────────────────────────────────────────────
    def _build_batch_tab(self, nb):
        frame = tk.Frame(nb, bg="#0a0a0a")
        nb.add(frame, text="  Batch Edit  ")

        tk.Label(frame, text="BATCH STAT MODIFIER",
                 bg="#0a0a0a", fg="#c8a96e",
                 font=("Consolas", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(frame,
                 text="Apply stat changes to all matching MonoBehaviours across all bundles.",
                 bg="#0a0a0a", fg="#555",
                 font=("Consolas", 9)).pack(anchor="w", padx=16, pady=(0, 8))

        tk.Frame(frame, bg="#1a1a1a", height=1).pack(fill="x")

        form = tk.Frame(frame, bg="#0a0a0a")
        form.pack(fill="x", padx=16, pady=12)

        def row(parent, label, var, row_idx, width=20):
            tk.Label(parent, text=label, bg="#0a0a0a", fg="#888",
                     font=("Consolas", 10), width=22,
                     anchor="w").grid(row=row_idx, column=0, pady=3, sticky="w")
            ttk.Entry(parent, textvariable=var, width=width).grid(
                row=row_idx, column=1, pady=3, sticky="w", padx=(0, 20))

        self._batch_bundle_var = tk.StringVar()
        self._batch_output_var = tk.StringVar()
        self._batch_field_var = tk.StringVar(value="Weight")
        self._batch_value_var = tk.StringVar()
        self._batch_filter_var = tk.StringVar()

        row(form, "Bundle File:", self._batch_bundle_var, 0, 40)
        tk.Button(form, text="Browse", bg="#1e1e1e", fg="#ccc",
                  relief="flat", cursor="hand2",
                  command=lambda: self._batch_bundle_var.set(
                      filedialog.askopenfilename())).grid(
                  row=0, column=2, padx=4)

        row(form, "Output Path:", self._batch_output_var, 1, 40)
        tk.Button(form, text="Browse", bg="#1e1e1e", fg="#ccc",
                  relief="flat", cursor="hand2",
                  command=lambda: self._batch_output_var.set(
                      filedialog.asksaveasfilename(
                          defaultextension=".bundle"))).grid(
                  row=1, column=2, padx=4)

        row(form, "Field Name:", self._batch_field_var, 2)
        row(form, "New Value:", self._batch_value_var, 3)
        row(form, "Filter (name contains):", self._batch_filter_var, 4)

        tk.Label(form, text="Common fields: Weight  Recoil  Ergonomics  SuppressedAmount  isSuppressed  isSight  isOptic",
                 bg="#0a0a0a", fg="#444",
                 font=("Consolas", 8)).grid(row=5, column=0, columnspan=3,
                                             pady=(0, 8), sticky="w")

        ttk.Button(form, text="▶  Run Batch Edit",
                   style="Accent.TButton",
                   command=self._run_batch).grid(row=6, column=0,
                                                  columnspan=3, sticky="w",
                                                  pady=8)

    # ── Log Tab ───────────────────────────────────────────────────────────────
    def _build_log_tab(self, nb):
        frame = tk.Frame(nb, bg="#0a0a0a")
        nb.add(frame, text="  Log  ")

        self._log_box = scrolledtext.ScrolledText(
            frame, bg="#050505", fg="#4af", insertbackground="white",
            font=("Consolas", 9), relief="flat", state="disabled",
            wrap="word", selectbackground="#1a3a5a")
        self._log_box.pack(fill="both", expand=True, padx=8, pady=8)

        ttk.Button(frame, text="Clear",
                   command=self._clear_log).pack(pady=(0, 8))

    # ── Settings Tab ──────────────────────────────────────────────────────────
    def _build_settings_tab(self, nb):
        frame = tk.Frame(nb, bg="#0a0a0a")
        nb.add(frame, text="  Settings  ")

        tk.Label(frame, text="DEFAULT PATHS",
                 bg="#0a0a0a", fg="#c8a96e",
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=16, pady=(12, 4))

        f = tk.Frame(frame, bg="#0a0a0a")
        f.pack(fill="x", padx=16, pady=4)

        self._cfg_bundle_dir = tk.StringVar(
            value=r"A:\SteamLibrary\steamapps\common\OPERATOR\OPERATOR_Data\StreamingAssets\aa\StandaloneWindows64")
        self._cfg_assets = tk.StringVar(
            value=r"A:\SteamLibrary\steamapps\common\OPERATOR\OPERATOR_Data\sharedassets0.assets")
        self._cfg_output_dir = tk.StringVar(
            value=r"A:\Extracted_PAKS_modding stuff\OPERATOR\Modwork\Output")
        self._cfg_dump_dir = tk.StringVar(
            value=r"A:\Extracted_PAKS_modding stuff\OPERATOR\Modwork\RipOutput\MonoBehaviour")

        def cfg_row(label, var):
            r = tk.Frame(f, bg="#0a0a0a")
            r.pack(fill="x", pady=3)
            tk.Label(r, text=label, bg="#0a0a0a", fg="#888",
                     font=("Consolas", 10), width=20,
                     anchor="w").pack(side="left")
            ttk.Entry(r, textvariable=var, width=60).pack(side="left", padx=(0, 6))
            tk.Button(r, text="📁", bg="#1a1a1a", fg="#ccc", relief="flat",
                      cursor="hand2",
                      command=lambda v=var: v.set(
                          filedialog.askdirectory() or v.get())).pack(side="left")

        cfg_row("Bundle Dir:", self._cfg_bundle_dir)
        cfg_row("sharedassets0:", self._cfg_assets)
        cfg_row("Output Dir:", self._cfg_output_dir)
        cfg_row("Dump Dir:", self._cfg_dump_dir)

        tk.Frame(frame, bg="#1a1a1a", height=1).pack(fill="x", pady=12)

        tk.Label(frame, text="QUICK SEARCH  —  find which bundle contains a PathID",
                 bg="#0a0a0a", fg="#c8a96e",
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=16, pady=(0, 4))

        qs = tk.Frame(frame, bg="#0a0a0a")
        qs.pack(fill="x", padx=16)

        tk.Label(qs, text="PathID:", bg="#0a0a0a", fg="#888",
                 font=("Consolas", 10)).pack(side="left")
        self._qs_var = tk.StringVar()
        ttk.Entry(qs, textvariable=self._qs_var, width=28).pack(side="left", padx=6)
        ttk.Button(qs, text="Search", style="Accent.TButton",
                   command=self._quick_search).pack(side="left")

    # ── Canvas helpers ────────────────────────────────────────────────────────
    def _on_fields_configure(self, e):
        self._fields_canvas.configure(
            scrollregion=self._fields_canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._fields_canvas.itemconfig(
            self._fields_window, width=e.width)

    def _on_mousewheel(self, e):
        if self._fields_canvas.winfo_containing(e.x_root, e.y_root):
            self._fields_canvas.yview_scroll(-1 * (e.delta // 120), "units")

    # ── Bundle loading ────────────────────────────────────────────────────────
    def _browse_bundle(self):
        p = filedialog.askopenfilename(
            initialdir=self._cfg_bundle_dir.get(),
            filetypes=[("Bundle files", "*.bundle *.assets"), ("All", "*.*")])
        if p:
            self._bundle_var.set(p)
            # Auto-set output path
            name = os.path.basename(p)
            base, ext = os.path.splitext(name)
            out = os.path.join(self._cfg_output_dir.get(),
                               f"{base}_modified{ext}")
            self._output_var.set(out)

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
            initialdir=self._cfg_output_dir.get(),
            defaultextension=".bundle")
        if p:
            self._output_var.set(p)

    def _load_bundle(self):
        bp = self._bundle_var.get().strip()
        if not bp or not os.path.isfile(bp):
            messagebox.showwarning("No file", "Please select a valid bundle or .assets file.")
            return
        self._log(f"Loading: {os.path.basename(bp)}")
        self.nb.select(2)

        def run():
            try:
                env, objects = get_all_objects_in_bundle(bp)
                self._current_env = env
                self._current_bundle_path = bp
                self._objects_cache = objects
                self.after(0, lambda: self._populate_obj_tree(objects))
                self._log(f"  Loaded {len(objects)} objects")
                self.after(0, lambda: self.nb.select(0))
            except Exception as e:
                self._log(f"  ✗ Error: {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    def _populate_obj_tree(self, objects):
        self._obj_tree.delete(*self._obj_tree.get_children())
        for i, obj in enumerate(objects):
            name = obj["name"] or "(unnamed)"
            tag = "mono" if obj["type"] == "MonoBehaviour" else ""
            iid = self._obj_tree.insert("", "end",
                                         values=(obj["type"], name),
                                         tags=(tag,), iid=str(i))
        self._obj_tree.tag_configure("mono", foreground="#c8a96e")

    def _on_obj_select(self, _):
        sel = self._obj_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        obj = self._objects_cache[idx]
        self._current_obj = obj
        self._current_tree = obj["tree"]
        info = f"PathID: {obj['path_id']}  │  {obj['type']}  │  {obj['name'] or '(unnamed)'}"
        self._obj_info_var.set(info)
        self._build_fields(obj["tree"])

    # ── Field editor ──────────────────────────────────────────────────────────
    def _build_fields(self, tree):
        for w in self._fields_frame.winfo_children():
            w.destroy()
        self._field_vars.clear()

        if not tree:
            tk.Label(self._fields_frame, text="No typetree data available.",
                     bg="#0a0a0a", fg="#555",
                     font=("Consolas", 10)).pack(pady=20)
            return

        row = 0
        for key, val in tree.items():
            if key in SKIP_FIELDS:
                continue
            if isinstance(val, dict) or isinstance(val, list):
                continue

            # Label
            lbl_color = "#c8a96e" if key in FLOAT_FIELDS else \
                        "#7ab8d4" if key in INT_FIELDS else \
                        "#a8d4a8" if key in BOOL_FIELDS else "#888"

            tk.Label(self._fields_frame, text=key,
                     bg="#0a0a0a", fg=lbl_color,
                     font=("Consolas", 10), width=28,
                     anchor="w").grid(row=row, column=0, padx=(12, 6),
                                      pady=2, sticky="w")

            if key in BOOL_FIELDS:
                var = tk.IntVar(value=int(val) if val is not None else 0)
                cb = tk.Checkbutton(self._fields_frame, variable=var,
                                    bg="#0a0a0a", fg="#a8d4a8",
                                    selectcolor="#1a1a1a",
                                    activebackground="#0a0a0a",
                                    relief="flat", cursor="hand2")
                cb.grid(row=row, column=1, sticky="w", pady=2)
            else:
                var = tk.StringVar(value=str(val) if val is not None else "")
                e = ttk.Entry(self._fields_frame, textvariable=var, width=24)
                e.grid(row=row, column=1, sticky="w", pady=2, padx=(0, 12))

            self._field_vars[key] = (var, val)
            row += 1

        if row == 0:
            tk.Label(self._fields_frame,
                     text="No editable scalar fields found on this object.\n(Complex/reference fields are hidden)",
                     bg="#0a0a0a", fg="#555",
                     font=("Consolas", 10),
                     justify="center").pack(pady=20)

    def _reset_fields(self):
        if not self._current_tree:
            return
        self._build_fields(self._current_tree)

    def _save_bundle(self):
        if not self._current_env or not self._current_obj:
            messagebox.showwarning("Nothing loaded", "Load a bundle and select an object first.")
            return
        out = self._output_var.get().strip()
        if not out:
            messagebox.showwarning("No output", "Set an output path first.")
            return

        # Apply field changes back to the tree
        tree = dict(self._current_tree)
        changes = []
        for key, (var, original) in self._field_vars.items():
            raw = var.get()
            try:
                if key in BOOL_FIELDS:
                    new_val = int(raw)
                elif key in FLOAT_FIELDS:
                    new_val = float(raw)
                elif key in INT_FIELDS:
                    new_val = int(raw)
                elif isinstance(original, float):
                    new_val = float(raw)
                elif isinstance(original, int):
                    new_val = int(raw)
                else:
                    new_val = raw

                if new_val != original:
                    changes.append(f"  {key}: {original} → {new_val}")
                tree[key] = new_val
            except ValueError:
                self._log(f"  ⚠ Could not parse '{key}' = '{raw}', keeping original")
                tree[key] = original

        if not changes:
            if not messagebox.askyesno("No changes", "No fields were modified. Save anyway?"):
                return

        self._log(f"\nSaving bundle...")
        if changes:
            self._log("Changes:")
            for c in changes:
                self._log(c)

        def run():
            try:
                # Find and update the object
                path_id = self._current_obj["path_id"]
                for obj in self._current_env.objects:
                    if obj.path_id == path_id:
                        obj.save_typetree(tree)
                        break

                write_modified_bundle(self._current_env, out)
                self._log(f"✓ Saved to: {out}")
                self.after(0, lambda: messagebox.showinfo(
                    "Saved!",
                    f"Bundle saved to:\n{out}\n\n"
                    f"Deploy it to your game folder to test."))
            except Exception as e:
                self._log(f"✗ Save failed: {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    # ── Batch edit ────────────────────────────────────────────────────────────
    def _run_batch(self):
        bp = self._batch_bundle_var.get().strip()
        out = self._batch_output_var.get().strip()
        field = self._batch_field_var.get().strip()
        raw_val = self._batch_value_var.get().strip()
        name_filter = self._batch_filter_var.get().strip().lower()

        if not all([bp, out, field, raw_val]):
            messagebox.showwarning("Missing fields", "Fill in all fields.")
            return

        self._log(f"\nBatch edit: {field} = {raw_val}")
        self.nb.select(2)

        def run():
            try:
                env, objects = get_all_objects_in_bundle(bp)
                modified = 0
                for obj_data in objects:
                    tree = obj_data["tree"]
                    if not tree or field not in tree:
                        continue
                    name = tree.get("m_Name", "").lower()
                    if name_filter and name_filter not in name:
                        continue
                    try:
                        original = tree[field]
                        if isinstance(original, float):
                            tree[field] = float(raw_val)
                        elif isinstance(original, int):
                            tree[field] = int(raw_val)
                        else:
                            tree[field] = raw_val

                        for o in env.objects:
                            if o.path_id == obj_data["path_id"]:
                                o.save_typetree(tree)
                                break

                        self._log(f"  Modified {obj_data['type']} "
                                  f"'{obj_data['name']}': "
                                  f"{field} {original} → {tree[field]}")
                        modified += 1
                    except Exception as e:
                        self._log(f"  ⚠ Skip {obj_data['path_id']}: {e}")

                if modified > 0:
                    write_modified_bundle(env, out)
                    self._log(f"\n✓ Modified {modified} objects")
                    self._log(f"✓ Saved to: {out}")
                    self.after(0, lambda: messagebox.showinfo(
                        "Done", f"Modified {modified} objects.\nSaved to:\n{out}"))
                else:
                    self._log(f"  No matching objects found with field '{field}'")
            except Exception as e:
                self._log(f"✗ Error: {e}\n{traceback.format_exc()}")

        threading.Thread(target=run, daemon=True).start()

    # ── Quick search ──────────────────────────────────────────────────────────
    def _quick_search(self):
        raw = self._qs_var.get().strip()
        if not raw:
            return
        try:
            path_id = int(raw)
        except ValueError:
            messagebox.showerror("Invalid", "PathID must be an integer.")
            return

        self._log(f"\nSearching for PathID: {path_id}")
        self.nb.select(2)

        def run():
            bundle_dir = self._cfg_bundle_dir.get()
            assets_file = self._cfg_assets.get()

            self._log(f"Checking sharedassets0.assets...")
            try:
                env = UnityPy.load(assets_file)
                for obj in env.objects:
                    if obj.path_id == path_id:
                        self._log(f"✓ FOUND in sharedassets0.assets")
                        return
            except Exception as e:
                self._log(f"  Error: {e}")

            files = [f for f in os.listdir(bundle_dir)
                     if os.path.isfile(os.path.join(bundle_dir, f))]
            self._log(f"Scanning {len(files)} bundles...")
            for i, filename in enumerate(files):
                filepath = os.path.join(bundle_dir, filename)
                if i % 100 == 0:
                    self._log(f"  Progress: {i}/{len(files)}", end=True)
                try:
                    env = UnityPy.load(filepath)
                    for obj in env.objects:
                        if obj.path_id == path_id:
                            self._log(f"\n✓ FOUND in: {filename}")
                            self._log(f"  Full path: {filepath}")
                            self.after(0, lambda fp=filepath: self._bundle_var.set(fp))
                            return
                except:
                    pass
            self._log(f"\n✗ PathID {path_id} not found.")

        threading.Thread(target=run, daemon=True).start()

    # ── Log helpers ───────────────────────────────────────────────────────────
    def _log(self, msg, end=False):
        def _do():
            self._log_box.configure(state="normal")
            if end:
                # Overwrite last line
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
    app = StatEditorApp()
    app.mainloop()
