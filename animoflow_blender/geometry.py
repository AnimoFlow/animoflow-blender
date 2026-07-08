"""
Pure geometry helpers — no bpy imports, unit-testable with plain pytest.

Coordinate systems
------------------
The AnimoFlow API lives in a glTF-style Y-up world: the ground plane is
(x, z) and `curve_2d` / waypoint coordinates are ground-plane pairs.
Blender is Z-up: the ground plane is (x, y).

Blender's glTF importer converts glTF `(gx, gy, gz)` to Blender
`(gx, -gz, gy)`. Inverting that for ground-plane coordinates gives

    blender (bx, by)  →  API [x, z] = [bx, -by]

which is what `blender_ground_to_api()` implements. The mapping is
deliberately isolated in this one function: the headless trajectory test
draws an L-shaped curve and asserts which side the imported character
turns to (chirality). If that assertion ever fails, flip the sign here —
one line — and re-run.

Curves are sent RAW (as drawn): since 2026-07-02 the server canonicalizes
internally for the model (origin start, +Z first segment) and places the
generated motion back on the original curve, so the imported clip lands
exactly on the drawn Blender curve.
"""
import math

# Web-UI parity constants (must match the web app's timeline math).
WP_FPS = 30                      # Kimodo native fps; t_frame = round(t_sec * 30)
WP_MAX_POINTS = 6                # hard cap incl. the locked origin pin
WP_DURATION_PADDING_S = 0.5      # last waypoint must not sit exactly at clip end
TIMELINE_MIN_SEGMENTS = 2
TIMELINE_SEG_MIN_S = 0.5
TIMELINE_SEG_MAX_S = 8.0
TIMELINE_MAX_TOTAL_S = 20.0


def blender_ground_to_api(x, y):
    """Map a Blender ground-plane point (x, y) to an API [x, z] pair.

    See module docstring for the derivation. Chirality is verified
    empirically by the headless L-curve test; a wrong sign here is a
    one-line fix.
    """
    return (x, -y)


def resample_polyline(points, max_points=200, min_spacing=0.01):
    """Drop near-coincident points and cap the total point count.

    Evaluated Blender curves can yield thousands of samples; the web UI
    sends at most a few hundred. Keeps the first and last points exactly.
    `points` is a sequence of (x, z) pairs; returns a list of tuples.
    """
    if not points:
        return []
    out = [tuple(points[0])]
    for p in points[1:]:
        px, pz = p
        lx, lz = out[-1]
        if math.hypot(px - lx, pz - lz) >= min_spacing:
            out.append((px, pz))
    # Always keep the true endpoint even if it landed inside min_spacing.
    if len(points) > 1 and out[-1] != tuple(points[-1]):
        out.append(tuple(points[-1]))
    if len(out) <= max_points:
        return out
    # Uniform index-space decimation, preserving both endpoints.
    step = (len(out) - 1) / (max_points - 1)
    idx = [round(i * step) for i in range(max_points)]
    idx[-1] = len(out) - 1
    return [out[i] for i in idx]


def waypoint_t_frames(t_sec, duration_sec, fps=WP_FPS):
    """Convert a waypoint time in seconds to a frame index, clamped to
    the clip's valid frame range (mirrors app.js submit-time conversion)."""
    num_frames = max(1, round(float(duration_sec) * fps))
    return min(num_frames - 1, max(0, round(float(t_sec) * fps)))


def inflate_duration_for(t_sec, current_duration, max_duration=9.0,
                         padding=WP_DURATION_PADDING_S):
    """Return the duration needed to fit a waypoint at `t_sec`, mirroring
    the web UI's `wpInflateDurationFor`: ceil to the next 0.5 s including
    padding, capped at `max_duration`. Returns `current_duration` when no
    bump is needed."""
    ceil_half = math.ceil((float(t_sec) + padding) * 2) / 2
    needed = min(float(max_duration), ceil_half)
    return needed if needed > float(current_duration) else float(current_duration)


def polyline_length(points):
    """Total length of a ground-plane polyline (for panel info display)."""
    total = 0.0
    for (ax, az), (bx, bz) in zip(points, points[1:]):
        total += math.hypot(bx - ax, bz - az)
    return total


# ── Pace feasibility ────────────────────────────────────────────────────
# Reference paces (m/s). Keep in sync with the webui (app.js PACE_*):
# a comfortable walk ~1.4, a run ~3.5; above ~2.0 a clip can no longer
# read as walking, so we warn (generation still allowed — the warning
# guides the user toward a feasible duration).
PACE_WALK_MPS = 1.4
PACE_RUN_MPS = 3.5
PACE_WARN_MPS = 2.0


def _suggest_durations(length_m):
    """(walk_s, run_s) durations for a path, rounded UP to 0.5 s."""
    walk_s = math.ceil((length_m / PACE_WALK_MPS) * 2) / 2
    run_s = math.ceil((length_m / PACE_RUN_MPS) * 2) / 2
    return walk_s, run_s


def pace_warning_lines(length_m, duration_s):
    """Warning as SHORT lines when *duration_s* is too short for a
    *length_m* path (implied speed above PACE_WARN_MPS), else None.
    Blender panel labels truncate instead of wrapping, so each returned
    line must stand on its own at N-panel width (~30 chars). Same
    thresholds/numbers as the webui's meta-line warning."""
    if not (length_m > 0) or not (duration_s > 0):
        return None
    speed = length_m / duration_s
    if speed <= PACE_WARN_MPS:
        return None
    walk_s, run_s = _suggest_durations(length_m)
    return (
        f"Too fast: {speed:.1f} m/s over {length_m:.1f} m",
        f"Walk ≥ {walk_s:g} s · Run ≥ {run_s:g} s",
    )


def waypoint_pace_warning_lines(points_with_times):
    """Short warning lines when any waypoint leg is too fast, else None.
    *points_with_times* is [(x, z, t_sec), ...] INCLUDING the origin pin
    at t=0, in time order. Suggested totals come from the full path
    length so the user can spread the pin times."""
    pts = list(points_with_times)
    if len(pts) < 2:
        return None
    total = 0.0
    worst_v, worst_leg = 0.0, None
    for i in range(1, len(pts)):
        ax, az, at = pts[i - 1]
        bx, bz, bt = pts[i]
        dist = math.hypot(bx - ax, bz - az)
        total += dist
        dt = bt - at
        if dt <= 0:
            continue  # ordering errors are reported separately
        v = dist / dt
        if v > worst_v:
            worst_v, worst_leg = v, i
    if worst_v <= PACE_WARN_MPS or worst_leg is None:
        return None
    walk_s, run_s = _suggest_durations(total)
    return (
        f"Leg #{worst_leg} too fast: {worst_v:.1f} m/s",
        f"Path {total:.1f} m — spread the times:",
        f"Walk ≥ {walk_s:g} s · Run ≥ {run_s:g} s",
    )
