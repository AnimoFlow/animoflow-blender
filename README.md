# animoflow-blender

Blender addon for [AnimoFlow](https://animoflow.ai) — generate character motion and import it straight into your scene. Supports all four AnimoFlow tasks:

| Task | How you author it | Models |
|------|-------------------|--------|
| **Single sequence** | Type a prompt | MDM, Kimodo, MoMask |
| **Trajectory** | Draw (or pick) a curve on the floor; the character follows it | PriorMDM, Kimodo |
| **Timeline** | A list of (prompt, duration) segments, stitched into one long clip | PriorMDM |
| **Waypoints** | Drop marker empties on the floor, give each a time | Kimodo |

## Install

### Blender 4.2+ — extension repository (recommended, auto-updates)

> *Available with the public release.*

1. In Blender: **Edit → Preferences → Get Extensions → Repositories → + → Add Remote Repository**
2. Paste the URL: `https://animoflow.ai/extensions/index.json`
3. Search for **AnimoFlow** in Get Extensions and click **Install** — Blender keeps it up to date from then on

### Blender 4.2+ — drag-and-drop

1. Download `animoflow_blender-<version>.zip` from [Releases](https://github.com/AnimoFlow/animoflow-blender/releases)
2. Drag the zip into the Blender window and confirm — done

### Blender 3.6–4.1 — legacy install

1. Download `animoflow_blender-<version>-legacy.zip` from [Releases](https://github.com/AnimoFlow/animoflow-blender/releases)
2. In Blender: **Edit → Preferences → Add-ons → Install** → select the zip
3. Enable **AnimoFlow** in the addon list

### After installing (all versions)

1. In the addon preferences, pick **AnimoFlow Cloud**, paste your [Hugging Face token](https://huggingface.co/settings/tokens), and click **Connect**
2. Open the **AnimoFlow** tab in the **N-sidebar** (View3D → press N)

### Supported Blender versions

| Blender | Install path | Package |
|---------|-------------|---------|
| 4.2+ | Extension repo (auto-updates) or drag-and-drop | `animoflow_blender-<version>.zip` |
| 3.6–4.1 | Add-ons → Install from Disk | `animoflow_blender-<version>-legacy.zip` |

Note for Blender 4.2+: AnimoFlow talks to the network, so **Allow Online Access** (Preferences → System) must be enabled — the addon will tell you if it isn't.

Tasks, models and characters load automatically on startup once a token is saved — the Connect button is there for the first run and for retrying after errors.

## Usage

1. Pick a **Task** and a **Character**
2. Author the input:
   - *Single sequence*: type a motion prompt (e.g. "a person doing a cartwheel")
   - *Trajectory*: click **Draw Trajectory**, sketch a path on the floor, press Tab — or point the Curve field at any existing curve
   - *Timeline*: add at least 2 segments (each 0.5–8 s, 20 s total cap)
   - *Waypoints*: place the 3D cursor, click **+** to drop a marker, edit its time; up to 5 markers plus the locked origin
3. Hit **Generate**
4. The result imports automatically when ready (glTF, scene FPS set to match)

Generation runs on your own Hugging Face ZeroGPU quota (free ≈ 5 min GPU/day, PRO ≈ 25 min/day).

## Settings

**Edit → Preferences → Add-ons → AnimoFlow**

| Setting | Default | Description |
|---------|---------|-------------|
| Server | *Not configured* | AnimoFlow Cloud (HF token) or your own local server |
| Hugging Face Token | *(empty)* | Cloud mode — your quota, your control |
| Server URL | `http://localhost:8090` | Local mode only |

## Requirements

- Blender 3.6 or newer (tested on 5.0)
- No Python dependencies — the addon is pure stdlib

## Dev

```bash
git clone https://github.com/AnimoFlow/animoflow-blender.git
cd animoflow-blender

# Pure unit tests (no Blender needed; needs a python with pytest)
./run_tests.sh

# Headless end-to-end pipeline: builds the zip, installs it into an
# ISOLATED Blender sandbox, connects to the HF Space, runs real
# generations and asserts on the imported armatures. Your installed
# addon copy is never touched.
SMOKE=1 scripts/test_headless.sh          # 1 generation (text/mdm)
scripts/test_headless.sh                  # full matrix (~7 generations)
MATRIX=none scripts/test_headless.sh      # UI + connect only, no GPU
ANIMOFLOW_ONLY="trajectory" scripts/test_headless.sh  # filter rows

# Token for the headless tests: env ANIMOFLOW_HF_TOKEN, or put it in
# gitignored tests/headless/hf_token.txt.
# NOTE: the full matrix burns ~70 GPU-seconds of the token's daily
# ZeroGPU budget (free tier ≈ 5 GPU-minutes/day).

# Build zips for distribution (version read from blender_manifest.toml):
#   dist/animoflow_blender-<version>.zip         Blender 4.2+ extension
#   dist/animoflow_blender-<version>-legacy.zip  Blender 3.6-4.1 addon
# Uses `blender --command extension build/validate` when a Blender 4.2+
# binary is found; otherwise falls back to plain zip (unvalidated).
./scripts/build.sh

# Generate the self-hosted extension repository index (Blender 4.2+ binary
# required) — output in dist/extension-repo/, served from the website at
# /extensions/ once the extension repository goes live:
./scripts/gen_extension_index.sh
```

## More documentation

- **User guide** — all the ways to use AnimoFlow: <https://animoflow.ai/guide/>
- **All AnimoFlow repositories**: <https://github.com/AnimoFlow>
- Run your own server for the addon to connect to: [animoflow-api](https://github.com/AnimoFlow/animoflow-api)

## License

This add-on is **open source under [GPL-3.0](LICENSE)** (the license family
Blender requires for add-ons), copyright (c) 2026 Guy Tevet ("AnimoFlow").
Use it freely, including commercially; if you distribute a modified
version, it must stay GPL-3.0. External contributions require a one-time
CLA (a bot asks on your first pull request). Full AnimoFlow license map,
FAQ, and CLA text: [AnimoFlow/legal](https://github.com/AnimoFlow/legal).

