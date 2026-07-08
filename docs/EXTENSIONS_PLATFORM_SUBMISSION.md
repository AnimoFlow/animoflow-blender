# extensions.blender.org submission — flip-track checklist

Status: **NOT SUBMITTED** — blocked on the public flip (the repo is private
and the platform requires a public source link). Everything below should be
green before we press the button.

## Pre-submission checklist

### Packaging & validation

- [x] `blender_manifest.toml` at the package root (`animoflow_blender/blender_manifest.toml`), schema_version 1.0.0
- [x] `blender --command extension validate animoflow_blender` passes (run it with a 4.2+ binary; part of `scripts/build.sh`)
- [x] Version single-sourced from the manifest; `bl_info` in `__init__.py` kept in sync for the 3.6–4.1 legacy channel
- [ ] Re-run validate with the **oldest supported** Blender (4.2 LTS) before submitting — we validate with whatever binary is installed locally
- [x] `blender_version_min = "4.2.0"`
- [x] Pure stdlib — no bundled wheels, so no `wheels = [...]` entry needed

### License / GPL compliance

- [x] `license = ["SPDX:GPL-3.0-or-later"]` in the manifest
- [ ] `LICENSE` file (GPL-3.0-or-later text) included **inside the package dir** so it ships in the zip — add before submission
- [x] No proprietary code or binary blobs in the addon; server-side code is a separate work (network boundary — GPL does not propagate across the API)
- [ ] Confirm all files have consistent authorship (AnimoFlow) — no vendored third-party snippets without attribution

### Permissions

- [x] `[permissions] network = "Connect to AnimoFlow cloud or a local server"` declared (reason ≤ 64 chars, no trailing punctuation — reviewers enforce this)
- [x] Runtime respect for the toggle: `api.ensure_online_access()` guards the single `_urlopen()` chokepoint and raises a friendly error when **Allow Online Access** is off. Reviewers check that network extensions fail gracefully, not silently
- [x] No `files` / `clipboard` / `camera` / `microphone` permissions needed (temp-file writes for downloaded results don't require the files permission — that's for *user-chosen* paths)

### Listing content

- [ ] Tagline: "Generate character animation from text prompts" — < 64 chars, **no trailing period** (platform rule), sentence case, no marketing superlatives
- [ ] Screenshots/media: the listing needs at least one image; prepare 3–5 (panel in the N-sidebar, a generated animation on a character, the trajectory-draw flow). 16:9 or wider renders best; keep UI text legible at thumbnail size. `blender-tools/` scripts can capture these
- [ ] Optional short demo video (the platform embeds them; strong conversion win)
- [ ] Description text: what it does, the four tasks, that generation runs on the user's own HF quota — **must disclose the online service dependency prominently** (reviewers require it for network extensions)
- [x] `website` points to the user guide: https://animoflow.ai/guide/blender
- [ ] **Source link must point to the PUBLIC repo** (github.com/AnimoFlow/animoflow-blender) — cannot submit while private

### Review-queue expectations

- First-time extension review has historically taken **days to a few weeks**; plan the submission ahead of any launch date, not on it
- Reviewers commonly bounce on: tagline punctuation, missing LICENSE in the zip, undeclared network use, `print()` spam in the console, and errors on enable with factory prefs — test enable/disable cycles with `--factory-startup`
- Updates after acceptance go through a lighter re-review; keep changelogs meaningful
- The moderators communicate through the platform; watch the listing's activity feed after submitting

## Submission steps (when unblocked)

1. Make the GitHub repo public; confirm README install section is current
2. Add the `LICENSE` file into `animoflow_blender/`, rebuild, re-validate
3. Create an account / verify the AnimoFlow maintainer identity on extensions.blender.org
4. Upload `dist/animoflow_blender-<version>.zip`, fill the listing (tagline, description, media, source URL)
5. Submit for review; track status; answer reviewer feedback promptly
6. After approval: keep the self-hosted repo (`/extensions/index.json`) running for pre-4.2-platform users and CI, but point new users at the official listing
