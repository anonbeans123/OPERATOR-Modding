# OPERATOR — Unofficial Modding Pipeline

> **Pioneered April–May 2026** | Unity 6000.3.8f1 | IL2CPP | No official mod support
>
> This document covers the first known working modding toolchain for the game [OPERATOR](https://store.steampowered.com/app/1913370/OPERATOR/) — including mesh swapping, texture replacement, MonoBehaviour stat editing, and the groundwork for adding entirely new items.

---

## Table of Contents

- [What's Possible](#whats-possible)
- [Tools Required](#tools-required)
- [File Locations](#file-locations)
- [Pipeline 1 — Mesh Swapping](#pipeline-1--mesh-swapping)
- [Pipeline 2 — Texture Swapping](#pipeline-2--texture-swapping)
- [Pipeline 3 — Stat Editing](#pipeline-3--stat-editing)
- [Pipeline 4 — New Item Creation](#pipeline-4--new-item-creation)
- [Key Technical Discoveries](#key-technical-discoveries)
- [Known Limitations](#known-limitations)
- [What Needs Community Help](#what-needs-community-help)
- [Tool Reference](#tool-reference)
- [Attachment Type Map](#attachment-type-map)

---

## What's Possible

| Feature | Status | Notes |
|---|---|---|
| Mesh swapping | ✅ Working | Confirmed in-game |
| Texture swapping | ✅ Working | Via UABEA plugin |
| Stat editing | ✅ Working | Weight, recoil, ergonomics etc. confirmed in-game |
| New item creation | 🔄 In progress | Catalog patching approach identified, needs testing |
| IL2CPP code hooks | ❌ Blocked | Metadata v39 unsupported by all current tools |

---

## Tools Required

| Tool | Version | Purpose | Download |
|---|---|---|---|
| UABEAvalonia | Latest | Asset inspection, texture import | [GitHub](https://github.com/nesrak1/UABEA) |
| AssetStudioGUI (aelurum fork) | Latest | Mesh/texture extraction, MonoBehaviour dump | [GitHub](https://github.com/aelurum/AssetStudio) |
| Blender | 3.x+ | Mesh editing and export | [blender.org](https://www.blender.org) |
| Python | 3.10+ | Running the pipeline scripts | [python.org](https://www.python.org) |
| UnityPy | Latest | Bundle read/write | `pip install UnityPy` |
| Unity Editor | **6000.3.8f1 exactly** | Mesh serialization (optional path) | Unity Hub |

### Python scripts included in this repo

- `operator_mesh_swapper.py` — GUI tool for mesh swapping
- `operator_stat_editor.py` — GUI tool for MonoBehaviour stat editing
- `operator_new_item.py` — GUI wizard for new item creation

---

## File Locations

```
OPERATOR_Data\
  sharedassets0.assets              ← base game assets (weapons, props, audio)
  StreamingAssets\aa\
    catalog.bin                     ← Addressables catalog (BinaryStorageBuffer format)
    catalog.hash                    ← catalog version hash
    settings.json                   ← Addressables settings
    StandaloneWindows64\
      *.bundle                      ← weapon mods and attachments (4799 total)
      *.bundle.resS                 ← companion vertex data files (auto-handled by scripts)
```

### Suggested working folder structure

```
Modwork\
  Originals\    ← backup copies of original game files BEFORE modifying
  OBJ\          ← meshes exported from AssetStudio
  Output\       ← finished bundles and catalog ready to deploy
```

---

## Pipeline 1 — Mesh Swapping

### How it works

Unity 6 stores vertex data in external `.resS` companion files. The key insight is to **bypass `.resS` entirely** by embedding vertex data directly into `m_VertexData.m_DataSize` inside the bundle and zeroing out `m_StreamData`. This is handled automatically by the script.

### Step 1 — Identify your target mesh

1. Open **UABEAvalonia** and load the relevant `.bundle` from `StandaloneWindows64\`
2. Find the mesh asset in the list
3. Note in the Inspector:
   - Exact mesh name (case sensitive)
   - `m_SubMeshes` → `size` value — **your replacement must have this exact number of material slots**

### Step 2 — Export the original mesh

1. Open **AssetStudioGUI** → File → Load File → select the same bundle
2. Filter by **Mesh** type
3. Right-click your mesh → **Export Selected Assets** → save as OBJ

### Step 3 — Prepare your replacement in Blender

1. Import the original OBJ as a scale/position reference
2. Import or create your replacement mesh
3. Before exporting, run this checklist:
   - `Ctrl+A` → **All Transforms** (apply all transforms)
   - Edit Mode → `A` → **Mesh → Faces → Triangulate Faces**
   - Material slots must match original `m_SubMeshes` count exactly
   - Overlays → Face Orientation → all faces should be **blue** (no red)
   - If red faces: **Mesh → Normals → Recalculate Outside** (`Shift+N`)
4. **File → Export → Wavefront OBJ**:
   - Forward Axis: `-Z`
   - Up Axis: `Y`
   - ✅ Apply Modifiers
   - ✅ Triangulate Faces

### Step 4 — Swap the mesh

1. Run `python operator_mesh_swapper.py`
2. Browse to the original `.bundle`
3. Browse to your replacement OBJ
4. Click **Scan Bundle for Meshes** → select the target mesh from the list
5. Set output path and click **Run Mesh Swap**

### Step 5 — Deploy

1. Back up the original bundle to `Modwork\Originals\`
2. Copy the output bundle over the original in `StandaloneWindows64\`
3. Launch the game

### Troubleshooting

| Problem | Fix |
|---|---|
| Game crashes on load | Submesh count mismatch — check material slots in Blender match original `m_SubMeshes` size |
| Mesh invisible | Missing UV map — ensure at least one UV map exists before exporting |
| See-through / transparent faces | Normals issue — Mesh → Normals → Recalculate Outside in Blender |
| Mesh disappears at distance | LOD issue — swap all LOD variants (same base name with `.001`, `.002` suffixes) |

---

## Pipeline 2 — Texture Swapping

### How it works

UABEA's texture plugin works natively on Unity 6 bundles even though most other UABEA features are broken for this engine version.

### Substance Painter export settings

- **Template:** `Unity Universal Render Pipeline (Metallic Standard)`
  > ⚠️ Do NOT use HD Render Pipeline — OPERATOR uses URP
- **Format:** PNG, match original texture resolution (check in AssetStudio)
- This exports: `Albedo`, `Normal` (OpenGL), `Mask` (packed Metallic/AO/Smoothness), `Emission`

### Step-by-step

1. Export textures from Substance Painter using the URP preset above
2. Open **UABEAvalonia** → load the bundle containing your textures
3. Find the **Texture2D** asset — note its exact name
4. Rename your exported PNG to match the asset name exactly (case sensitive, no extra suffixes)
5. Select the Texture2D in UABEA → click **Plugins** → **Import PNG/TGA**
6. Select your renamed PNG
7. File → Save As (save to a new file first as backup)
8. Deploy to game folder

---

## Pipeline 3 — Stat Editing

### How it works

UABEA's Edit Data is broken for Unity 6 MonoBehaviours. The workaround:

1. **AssetStudioGUI's Dump tab** reads MonoBehaviour data as JSON using its own internal parser — this works where everything else fails
2. **UnityPy** with `FALLBACK_UNITY_VERSION = "6000.3.8f1"` can read and write the typetree back to the bundle

> ⚠️ All 10,265 MonoBehaviours in `sharedassets0.assets` have zero built-in typetrees. Only bundles in `StandaloneWindows64\` work reliably with UnityPy stat editing.

### Finding the right bundle

Use the **Quick Search** tab in `operator_stat_editor.py` — enter the PathID shown in UABEA and it scans all bundles to find which file contains that object.

### Editable WeaponMod fields

| Field | Type | Description |
|---|---|---|
| `Weight` | float | Weight in kg |
| `Recoil` | float | Recoil modifier (negative = less recoil) |
| `Ergonomics` | float | Ergonomics modifier |
| `SuppressedAmount` | float | 1.0 = no suppression, 0.0 = fully suppressed |
| `isSuppressed` | bool | Counts as a suppressor |
| `isSight` | bool | Counts as a sight |
| `isOptic` | bool | Counts as an optic |
| `attachmentType` | int | Slot category (see Attachment Type Map) |
| `AssetReferenceIndex` | int | Unique ID in the item registry |
| `barrelLength` | int | Barrel length modifier |

### Step-by-step

1. Run `python operator_stat_editor.py`
2. **Bundle Editor tab** → Browse to the `.bundle` containing your target mod
3. Click **Load** — all objects appear in the left panel (MonoBehaviours highlighted in gold)
4. Click the MonoBehaviour with `AssetReferenceIndex` in its fields
5. Edit values in the right panel
6. Set output path → **Save Modified Bundle**
7. Deploy to game folder

### Confirmed working in-game

MG338 suppressor weight changed to `99.0kg` — verified: base weapon 24.09kg, with suppressor 123.09kg (difference = exactly 99kg ✅)

---

## Pipeline 4 — New Item Creation

> 🔄 **Status: Partially working — community help needed**

### The challenge

OPERATOR uses Unity Addressables. The game does not scan the bundle folder freely — it reads `catalog.bin` (Unity's BinaryStorageBuffer format) which registers every known bundle. New bundles dropped in the folder are ignored unless they're in the catalog.

### What we know about catalog.bin

- Binary format — NOT plain JSON
- Header contains an **offset table** pointing to every section in the file
- Inserting or removing bytes anywhere corrupts all offsets → game loads with missing assets
- Bundle names are stored as **length-prefixed UTF-8 strings**: `[4-byte uint32 length][string bytes]`
- Each bundle appears twice: once as a short name, once with the full `{UnityEngine.AddressableAssets.Addressables.RuntimePath}\StandaloneWindows64\` prefix
- **The only safe edit is a same-size byte replacement** — swap an existing bundle name for a new one of identical length

### The approach

1. Find an existing bundle whose catalog name is the **exact same byte length** as your desired new name
2. Replace that bundle's name in `catalog.bin` with your new name (pure byte swap, no size change)
3. Overwrite that bundle's file on disk with your custom bundle content
4. The catalog remains valid and the game loads your new bundle as if it always existed

### Identified repurposing targets (length 84)

Both of these exist in the catalog and on disk:

```
weaponmods_stripped_assets_m110suppressorfde_17cd0bfc43ab6a90327aa2ea3a286ad9.bundle   (len=84)
weaponmods_stripped_assets_qdssnt4suppressor_7547fbe4fb118622d83513fde8060ff1.bundle   (len=84)
```

Using `m110suppressorfde` as the repurpose target (keeping NT4 intact as donor):

```python
OLD_NAME = "weaponmods_stripped_assets_m110suppressorfde_17cd0bfc43ab6a90327aa2ea3a286ad9.bundle"
NEW_NAME = "weaponmods_stripped_assets_qdssnt4suppressorcstm_7547fbe4fb118622d83513fde8060ff1.bundle"
# Both exactly 84 bytes — safe to swap
```

### Registering the new mod in weapon slots

Each weapon's attachment slots are defined by `WeaponModParent` MonoBehaviours. Each has a `CompatibleModIds` array listing which `AssetReferenceIndex` values are accepted.

To make your new mod appear in a slot, add its `AssetReferenceIndex` to the relevant `WeaponModParent.CompatibleModIds` array using `operator_stat_editor.py` or `operator_new_item.py`.

**NT4 suppressor slots** (already registered, accept indices 650 and 651):
```
WeaponModParent #109020 — Slot: Suppressor | Weapon: NT4 | Types: [23]
WeaponModParent #109042 — Slot: Suppressor | Weapon: NT4 | Types: [23]
```

### What the community can help with

- [ ] Fully test the catalog same-size swap approach
- [ ] Map all `AssetReferenceIndex` values to their in-game display names
- [ ] Figure out where display names are stored (likely in a localization MonoBehaviour or TextAsset)
- [ ] Rebuild `catalog.bin` from scratch with a proper parser to allow true new entries
- [ ] Find a way to read `sharedassets0.assets` MonoBehaviours without IL2CPP metadata

---

## Key Technical Discoveries

### Unity 6 + IL2CPP blocks most tools

| Tool | Status | Reason |
|---|---|---|
| Il2CppDumper | ❌ Fails | Metadata version 39 unsupported (supports up to v31) |
| Cpp2IL / TypeTreeGeneratorAPI | ❌ Fails | Same — metadata v39 not implemented |
| Il2CppInspector | ❌ Suspended | No active development |
| UABEA Edit Data | ❌ Fails | Unity 6 asset deserialization broken |
| UABEA Mesh Plugin | ❌ Fails | Not supported for Unity 6 |
| UABEA Texture Plugin | ✅ Works | Texture format unchanged |
| AssetRipper (full folder) | ⚠️ Freezes | Too heavy for full game folder load |
| AssetRipper (single file) | ✅ Works | Single `.assets` file loads fine |
| AssetStudioGUI | ✅ Works | Best tool for Unity 6 extraction |
| UnityPy | ✅ Works | With `FALLBACK_UNITY_VERSION = "6000.3.8f1"` |

### The .resS bypass

Unity 6 stores mesh vertex data in external `.resS` companion files. Standard modding approaches that try to update the `.resS` file separately fail because UABEA can't locate them correctly. The solution: **embed vertex data directly in the bundle** by zeroing out `m_StreamData` and writing to `m_VertexData.m_DataSize`. The game accepts inline vertex data correctly at runtime.

### MonoBehaviour typetree discovery

All MonoBehaviours in `sharedassets0.assets` have zero built-in typetrees (the game was built with type tree stripping). This blocks standard deserialization. **AssetStudioGUI's Dump tab** bypasses this with its own internal type resolution — making it the only tool that can read MonoBehaviour data from this game.

### catalog.bin format (Unity BinaryStorageBuffer)

```
[4 bytes] magic/version
[4 bytes] section count  
[N × 8 bytes] offset table: (section_size, section_offset) pairs
... section data ...
```

Strings are length-prefixed: `[uint32 little-endian length][UTF-8 bytes]`
Every bundle appears twice per catalog entry — short name + full RuntimePath name.

---

## Known Limitations

- **No IL2CPP code access** — weapon behavior, damage, fire rate, and anything controlled by compiled code cannot be modded until a tool supporting metadata v39 is released
- **catalog.bin is fragile** — only same-size name replacements are safe; adding truly new entries requires rebuilding the catalog
- **No display name editing** — in-game item names appear to come from a source not yet fully mapped
- **sharedassets0.assets MonoBehaviours** — UnityPy can see them (10,265 objects) but cannot deserialize them without typetrees; only bundle MonoBehaviours are editable
- **LOD variants** — swapping one LOD level only causes mesh to pop at distance; all LOD variants must be swapped

---

## What Needs Community Help

If you want to push this further, here's where things stand:

### High priority
- **Metadata v39 support** — if anyone adds this to Cpp2IL or Il2CppDumper, it unlocks full MonoBehaviour reading for `sharedassets0.assets` and potentially code-level modding
- **catalog.bin rebuilder** — a tool that parses the full BinaryStorageBuffer format and can regenerate it with new entries would allow truly unlimited new item addition
- **Display name mapping** — finding where in-game item names (shown in the weapon builder UI) are stored and how to change them

### Medium priority
- **Full WeaponMod schema documentation** — map all `AssetReferenceIndex` values (1–1444) to their in-game items
- **Attachment type mapping refinement** — types 8, 12, 13, 17, 20, 22 need better documentation
- **Animation-aware mesh swapping** — currently static meshes only; skinned/animated meshes need bone weight matching

### Nice to have
- **GUI improvements** — the three Python tools work but could be more polished
- **Batch processing** — tools to process multiple bundles at once
- **Mod manager** — a tool to manage and toggle mods without manually replacing files

---

## Tool Reference

### operator_mesh_swapper.py

```
python operator_mesh_swapper.py
```

**Tabs:**
- **Mesh Swap** — Browse bundle + OBJ, scan for mesh names, run swap
- **Log** — Full output

### operator_stat_editor.py

```
python operator_stat_editor.py
```

**Tabs:**
- **Bundle Editor** — Load bundle, click any object, edit scalar fields, save
- **Batch Edit** — Apply one field change to all matching objects in a bundle
- **Log** — Full output
- **Settings** — Save default paths, PathID quick search

### operator_new_item.py

```
python operator_new_item.py
```

**Tabs:**
- **1 · Define Mod** — Set stats, pick donor bundle, optional mesh swap, create bundle
- **2 · Register** — Add mod index to WeaponModParent CompatibleModIds
- **3 · Deploy** — Step-by-step deployment instructions

---

## Attachment Type Map

Decoded from `WeaponModParent` MonoBehaviour data:

| Type | Slot Name |
|---|---|
| 0 | Top Rail |
| 1 | MLOK Grip / Left Rail / Bottom Rail |
| 2 | Mount Rail / Sight / Mount Sight |
| 3 | Barrel |
| 4 | Handguard |
| 5 | Stock |
| 6 | Top Front Rail / SKIFF Rail |
| 7 | Rear Sight / Iron Sight Base |
| 9 | Pistol Grip |
| 10 | Muzzle Device |
| 11 | Trigger |
| 13 | Micro Sight Mount / RMR Mount |
| 15 | Upper Receiver |
| 16 | Buffer Tube |
| 18 | Scope (30mm / 34mm) |
| 19 | Rail Right |
| 22 | Left Rail / Rail Right |
| 23 | Suppressor / Muzzle (type 23 specific) |
| 25 | Accessory (Flashlight / Laser / Cover) |
| 26 | MLOK Rail Sides |
| 27 | Muzzle (.308) |
| 28 | Gas Block |
| 29 | Ammunition |
| 30 | Base Plate / Mag Extension |
| 31 | RMR Riser / Backplate |
| 32 | RMR |
| 33 | Muzzle (9x19) |
| 34 | Rear Sight (TT 2011) |
| 40 | Optic Plate |
| 46–47 | Muzzle (9x19 variants) |
| 48 | Stock Riser (CTR) |
| 49 | Stock Riser (B5) |
| 50 | Barrel Muzzle (45ACP) |
| 51 | Backplate |

---

## Contributing

This is a community research project. If you figure something out — especially around:

- IL2CPP metadata v39 parsing
- catalog.bin structure and rebuilding
- In-game display name sourcing
- New asset types (animations, sounds, UI)

...please share your findings. The more people working on this the faster we can build a proper mod ecosystem for OPERATOR.

---

*Last updated: May 2026 | Game version: 0.13.x | Engine: Unity 6000.3.8f1 IL2CPP*
