"""
AnimoFlow API client — pure stdlib, no third-party deps.

Two dispatch paths:

* Cloud (`https://*.hf.space`) — talks to the HF Space's `/generate_full`
  Gradio endpoint over the Queue HTTP API (POST to enqueue → SSE stream
  for the result). The user's HF token is forwarded as
  `Authorization: Bearer <token>` so ZeroGPU attributes generation quota
  to the user's HF account (free ~5 min/day, PRO ~25 min/day). Single
  blocking call — no polling. Mirrors the web app's JS submission
  pattern.

* Local (`http://localhost:8090` or any non-Space URL) — POST `/v1/jobs`,
  poll `/v1/jobs/{id}` until done, download via `/v1/files/{name}`.

`is_hf_space(url)` is the dispatcher key.
"""
import json
import os
import ssl
import tempfile
import urllib.error
import urllib.request


def is_hf_space(base_url: str) -> bool:
    """True if base_url points at an HF Space — cloud-mode dispatch key."""
    return ".hf.space" in (base_url or "").lower()


def _running_as_extension():
    """True when installed as a Blender 4.2+ extension (package prefixed
    `bl_ext.`), False for the legacy add-on (bare `animoflow_blender`)."""
    return (__package__ or "").startswith("bl_ext")


def ensure_online_access():
    """Respect Blender's 'Allow Online Access' preference, but only where
    it genuinely applies — DON'T block the legacy add-on on a soft user
    preference.

    `bpy.app.online_access` is *advisory*: Python's urllib isn't gated by
    it, so the add-on connected fine regardless before this guard existed.
    Hard-blocking a cloud-generation tool — whose entire purpose is
    talking to a server, and only after the user pasted an HF token — is
    user-hostile. So we only raise when honoring the flag actually
    matters:

    * running as a Blender **extension** (extensions.blender.org listing
      policy requires respecting online_access), or
    * the block is **overridden** by the command line / an org policy
      (`online_access_overridden` — the user can't just tick a checkbox
      to undo it, so proceeding would be wrong).

    A legacy add-on with a merely-unchecked soft preference proceeds, which
    is exactly how it behaved before the guard. bpy is imported lazily so
    this module stays importable in pure-Python test environments.
    """
    try:
        import bpy
    except ImportError:
        return  # not running inside Blender — nothing to enforce
    if getattr(bpy.app, "online_access", True) is not False:
        return  # online access allowed — nothing to enforce
    overridden = getattr(bpy.app, "online_access_overridden", False)
    if _running_as_extension() or overridden:
        raise RuntimeError(
            "AnimoFlow needs internet access — enable 'Allow Online Access' "
            "in Preferences > System, or your organization has disabled it."
        )


_ssl_ctx = None


def _https_context():
    """Build (once) an SSLContext with a CA bundle Blender's Python can read.

    Blender ships its own Python that does NOT consult the OS trust store
    (macOS Keychain / Windows cert store). So urllib's default context
    frequently can't verify a public certificate and raises
    `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
    certificate` — the classic reason a Blender add-on can't reach an
    HTTPS endpoint that `curl` and browsers hit fine. HF's edge cert (the
    cloud AnimoFlow Space) trips this.

    Blender bundles `certifi`, so prefer its bundle; fall back to known
    system bundles, then the stdlib default. Verification is NEVER
    disabled — an unreachable-with-verification server surfaces its real
    error rather than silently trusting anything.
    """
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    cafile = None
    try:
        import certifi
        cafile = certifi.where()
    except Exception:
        for path in (
            "/etc/ssl/cert.pem",                    # macOS / BSD
            "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu
            "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL/Fedora
        ):
            if os.path.isfile(path):
                cafile = path
                break
    try:
        _ssl_ctx = ssl.create_default_context(cafile=cafile)
    except Exception:
        # Bad/missing bundle — fall back to the platform default context
        # (still verifying, just without our explicit bundle).
        _ssl_ctx = ssl.create_default_context()
    return _ssl_ctx


def _urlopen(req, timeout=None):
    """Single chokepoint for ALL network requests in this addon.

    Checks Blender's online-access preference before opening the socket,
    so every code path (local REST, cloud enqueue/stream/download) is
    covered by one guard. HTTPS requests get an explicit CA bundle so
    Blender's isolated Python can verify public certs (see
    `_https_context`); plain-HTTP local-server requests are unaffected."""
    ensure_online_access()
    kwargs = {}
    if timeout is not None:
        kwargs["timeout"] = timeout
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if url.lower().startswith("https"):
        kwargs["context"] = _https_context()
    return urllib.request.urlopen(req, **kwargs)


def _post_json(url, data, timeout=30, api_key=""):
    body = json.dumps(data).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with _urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get_json(url, timeout=30, api_key=""):
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with _urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _delete(url, timeout=10, api_key=""):
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    with _urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(base_url, payload, api_key=""):
    """POST /v1/jobs → job_id (str). Local-mode only — cloud-mode
    callers should use `generate_cloud()` (single blocking call).

    `payload` is a prebuilt GenerateRequest body (see
    `payload.build_payload` — the single source of truth for all four
    task shapes)."""
    result = _post_json(f"{base_url}/v1/jobs", payload, api_key=api_key)
    return result["job_id"]


_FN_NAME = "generate_full"


def generate_cloud(space_url, hf_token, payload, on_progress=None):
    """Single blocking call against the HF Space's /generate_full endpoint
    over Gradio's Queue HTTP API.

    `payload` is a prebuilt GenerateRequest body (see `payload.build_payload`
    — the single source of truth for all four task shapes).

    Returns a dict:
      {"path": <local tmp file>, "summary": str|None,
       "rewritten": str|None, "snap_info": str|None}

    Wire format (Gradio v4/v5 internal but stable in practice):
      POST  <space>/gradio_api/call/generate_full     {data: [json]}  → {event_id}
      GET   <space>/gradio_api/call/generate_full/<event_id>          → SSE stream
                                                                       events: complete | error | generating | heartbeat

    The user's HF token is forwarded as `Authorization: Bearer <token>`.
    Hugging Face's edge mints a per-user identity token from it, and the
    Space attributes the generation's GPU time to the signed-in account's
    own daily ZeroGPU allowance (verified live 2026-07-08). Without a
    token the caller is attributed per network address. Either way the
    Space tops up from its shared pool when the caller's allowance is
    spent, within a daily courtesy cap. Mirrors the web app's path.

    `on_progress(tuple_data)` is invoked once per `generating` event with
    the parsed yielded tuple — typically used to surface the stage label
    in slot 1 to the operator panel.

    Raises `RuntimeError` with a clean message on failure.
    """
    # Check the online-access preference up front so the clean message
    # reaches the user directly (the enqueue try/except below wraps
    # generic exceptions in a "could not connect" prefix).
    ensure_online_access()

    if not hf_token:
        raise RuntimeError(
            "Cloud mode requires a Hugging Face token — paste one into "
            "Preferences > Add-ons > AnimoFlow. "
            "Get it at huggingface.co/settings/tokens."
        )

    base = space_url.rstrip("/")
    call_url = f"{base}/gradio_api/call/{_FN_NAME}"
    auth_headers = {"Authorization": f"Bearer {hf_token}"}

    # --- Step 1: enqueue ----------------------------------------------------
    enqueue_body = json.dumps({"data": [json.dumps(payload)]}).encode()
    enqueue_req = urllib.request.Request(
        call_url,
        data=enqueue_body,
        headers={**auth_headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _urlopen(enqueue_req, timeout=30) as resp:
            enqueue_data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if exc.code in (401, 403):
            raise RuntimeError(
                "Hugging Face rejected your token. Open Preferences → "
                "Add-ons → AnimoFlow and paste a fresh one from "
                "huggingface.co/settings/tokens."
            ) from exc
        raise RuntimeError(
            f"AnimoFlow Cloud enqueue failed (HTTP {exc.code}): {body[:200]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Could not connect to AnimoFlow Cloud: {exc}") from exc

    event_id = enqueue_data.get("event_id")
    if not event_id:
        raise RuntimeError("AnimoFlow Cloud did not return an event_id.")

    # --- Step 2: stream SSE for the result ---------------------------------
    stream_req = urllib.request.Request(
        f"{call_url}/{event_id}",
        headers={**auth_headers, "Accept": "text/event-stream"},
        method="GET",
    )
    final_data = None
    try:
        # Generous timeout — cold-start ZeroGPU + Kimodo can take a minute
        # or more. SSE heartbeats keep the socket alive between bytes.
        with _urlopen(stream_req, timeout=600) as stream:
            block_lines = []
            for raw in stream:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    block_lines.append(line)
                    continue
                # Blank line — end of one SSE message block.
                evt = _parse_sse_event(block_lines)
                block_lines = []
                if not evt:
                    continue
                if evt["event"] == "complete":
                    try:
                        final_data = json.loads(evt["data"])
                    except Exception as exc:
                        raise RuntimeError(
                            f"AnimoFlow Cloud returned malformed JSON: {exc}"
                        ) from exc
                    break
                if evt["event"] == "error":
                    msg = evt["data"] or "AnimoFlow Cloud returned an error."
                    try:
                        parsed = json.loads(evt["data"])
                        if isinstance(parsed, str) and parsed.strip():
                            msg = parsed
                        elif isinstance(parsed, dict):
                            msg = parsed.get("message") or json.dumps(parsed)
                        elif parsed is None:
                            # Gradio emits `data: null` when the handler
                            # raised without a user-facing message.
                            msg = ("Generation failed on the server with no "
                                   "details. If this is the first request "
                                   "in a while, the model may still be "
                                   "warming up — try again in a minute.")
                    except Exception:
                        pass
                    raise RuntimeError(msg)
                if evt["event"] == "generating" and on_progress is not None:
                    # Opportunistic — don't let a bad progress payload abort
                    # the run; the result comes via `complete`.
                    try:
                        on_progress(json.loads(evt["data"]))
                    except Exception:
                        pass
                # heartbeat / unknown event names: ignore.
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"AnimoFlow Cloud stream failed (HTTP {exc.code}): {body[:200]}"
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"AnimoFlow Cloud stream error: {exc}") from exc

    if not final_data:
        raise RuntimeError("AnimoFlow Cloud stream ended without a result.")

    # data is [fileData, summary, rewritten, snap_info_json]. fileData is
    # a Gradio FileData dict {path, url, orig_name, ...} for a Model3D
    # component, but defensively also handle a bare string URL/path.
    if not isinstance(final_data, list) or not final_data:
        raise RuntimeError("AnimoFlow Cloud returned an unexpected payload shape.")
    file_entry = final_data[0]
    file_url = None
    if isinstance(file_entry, dict):
        file_url = file_entry.get("url") or file_entry.get("path")
    elif isinstance(file_entry, str):
        file_url = file_entry
    if not file_url:
        raise RuntimeError("AnimoFlow Cloud returned no file.")

    # Resolve to an absolute HTTPS URL. Gradio file references show up as
    # absolute https://, host-relative "/file=...", or a raw server path.
    if file_url.startswith("http://") or file_url.startswith("https://"):
        download_url = file_url
    elif file_url.startswith("/"):
        download_url = base + file_url
    else:
        download_url = f"{base}/file={file_url}"

    # Preserve the source extension so the import step picks the right loader.
    ext = ".glb"
    lower = download_url.lower()
    for candidate in (".glb", ".gltf", ".fbx"):
        if candidate in lower:
            ext = candidate
            break

    tmp = tempfile.mktemp(suffix=ext, prefix="animoflow_")
    dl_req = urllib.request.Request(download_url, headers=auth_headers, method="GET")
    try:
        with _urlopen(dl_req, timeout=60) as dl_resp:
            with open(tmp, "wb") as f:
                while True:
                    chunk = dl_resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as exc:
        raise RuntimeError(f"AnimoFlow Cloud file download failed: {exc}") from exc

    def _slot(i):
        v = final_data[i] if len(final_data) > i else None
        return v if isinstance(v, str) and v.strip() else None

    return {
        "path": tmp,
        "summary": _slot(1),
        "rewritten": _slot(2),
        "snap_info": _slot(3),
    }


def _parse_sse_event(block_lines):
    """Parse one SSE message block (list of field-lines) into {event, data}.

    SSE spec: each line is `field: value` (optional single leading space after
    the colon is stripped). Multiple `data:` lines on one event are joined
    with literal newlines. Lines starting with `:` are comments. Returns
    None if the block has no `data` field.
    """
    event = "message"
    data_parts = []
    for line in block_lines:
        if not line or line.startswith(":"):
            continue
        idx = line.find(":")
        if idx == -1:
            continue
        field = line[:idx].strip()
        value = line[idx + 1:]
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data_parts.append(value)
    if not data_parts:
        return None
    return {"event": event, "data": "\n".join(data_parts)}


def poll_job(base_url, job_id, api_key=""):
    """GET /v1/jobs/{id} → {status, progress, download_url, pipeline_resolved, error}."""
    return _get_json(f"{base_url}/v1/jobs/{job_id}", api_key=api_key)

def fetch_characters(base_url, api_key=""):
    """GET /v1/characters → list[str]. Falls back to defaults on error."""
    try:
        result = _get_json(f"{base_url}/v1/characters", timeout=20, api_key=api_key)
        return result.get("characters") or ["Y_bot"]
    except Exception as e:
        raise RuntimeError(f"Failed to fetch characters from {base_url}: {e}") from e


def fetch_tasks(base_url, api_key=""):
    """GET /v1/tasks → full task catalog with per-task model lists.

    Returns a list of task dicts:
      [{id, display_name, description, status, models: [{type, display_name, available, status, ...}]}]

    Returns [] on error — no hardcoded fallback.
    """
    try:
        url = f"{base_url}/v1/tasks"
        result = _get_json(url, timeout=20, api_key=api_key)
        return result.get("tasks", [])
    except Exception as e:
        raise RuntimeError(f"Failed to fetch tasks from {base_url}: {e}") from e

def download_output(base_url, download_url, api_key=""):
    """Download the terminal pipeline artifact (.glb or .fbx) to a temp
    file. Returns local path, preserving the source extension so the
    import step can pick the right loader.

    """
    if download_url.startswith("/"):
        url = base_url + download_url
    else:
        url = download_url
    # Extension from the URL (server builds it as /v1/files/<job>.glb).
    # Fall back to .glb for unknown URLs since that's the canonical
    # output format post-glb-switch.
    _ext_match = url.rsplit(".", 1)
    ext = "." + _ext_match[1].split("?", 1)[0].lower() if len(_ext_match) == 2 else ".glb"
    if ext not in (".glb", ".gltf", ".fbx"):
        ext = ".glb"
    tmp = tempfile.mktemp(suffix=ext, prefix="animoflow_")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with _urlopen(req) as resp:
        with open(tmp, "wb") as f:
            f.write(resp.read())
    return tmp


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

_ERROR_MESSAGES = {
    "rate_limited":   "Too many requests — try again in a moment.",
    "model_failed":   "Generation failed. Try a different prompt or seed.",
    "timeout":        "Generation timed out (>10 min). Try fewer frames.",
    "unauthorized":   "Invalid API key — check Preferences \u2192 Add-ons \u2192 AnimoFlow.",
    "forbidden":      "Access denied. Check your API key.",
    "job_not_found":  "Job not found. The server may have restarted.",
    "pipeline_failed": "Pipeline failed. Try a different prompt or model.",
}


def friendly_error(result: dict) -> str:
    """Return a user-facing message from a failed job or error response."""
    err = result.get("error")
    if isinstance(err, dict):
        code = err.get("code", "")
        return _ERROR_MESSAGES.get(code, err.get("message", "Unknown error."))
    if isinstance(err, str):
        return err or "Generation failed."
    return "Generation failed."
