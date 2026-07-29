"""
Unit tests for animoflow-blender api.py. No Blender installation required.
Uses unittest.mock to simulate HTTP responses.

Imports modules directly via importlib to avoid triggering
animoflow_blender/__init__.py (which has Blender-specific relative imports).
"""
import json
import sys
import os
import ssl
import importlib.util
import pytest
from unittest.mock import patch, MagicMock

_pkg = os.path.join(os.path.dirname(__file__), "..", "animoflow_blender")

def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_pkg, filename))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

api = _load("api", "api.py")


BASE = "http://localhost:8090"
KEY  = "test-key"


def _mock_response(data: dict, status: int = 200):
    """Builds a mock urllib response context manager."""
    body = json.dumps(data).encode()
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__  = MagicMock(return_value=False)
    mock.read      = MagicMock(return_value=body)
    mock.status    = status
    return mock


# ---------------------------------------------------------------------------
# generate()
# ---------------------------------------------------------------------------

def _text_payload(prompt="a person walks", model="mdm", character="Y_bot"):
    return {
        "input": {"type": "text", "prompt": prompt},
        "model": model,
        "character": character,
        "duration": 4.0,
        "seed": 42,
        "keyframe_builder": False,
        "keyframe_builder_error_degrees": 3.0,
        "compress_output": False,
    }


class TestGenerate:

    def test_returns_job_id(self):
        mock_resp = _mock_response({"job_id": "abc-123-def"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            job_id = api.generate(BASE, _text_payload(), api_key=KEY)
        assert job_id == "abc-123-def"

    def test_sends_auth_header(self):
        mock_resp = _mock_response({"job_id": "xyz"})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            api.generate(BASE, _text_payload(), api_key=KEY)
            req = mock_open.call_args[0][0]
            assert req.get_header("Authorization") == f"Bearer {KEY}"

    def test_posts_to_v1_jobs(self):
        mock_resp = _mock_response({"job_id": "xyz"})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            api.generate(BASE, _text_payload(), api_key=KEY)
            req = mock_open.call_args[0][0]
            assert "/v1/jobs" in req.full_url

    def test_request_body_is_payload_verbatim(self):
        mock_resp = _mock_response({"job_id": "xyz"})
        payload = _text_payload(prompt="a person jumps", model="kimodo",
                                character="Ch14_nonPBR")
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            api.generate(BASE, payload, api_key=KEY)
            req = mock_open.call_args[0][0]
            body = json.loads(req.data)
            assert body == payload


# ---------------------------------------------------------------------------
# poll_job()
# ---------------------------------------------------------------------------

class TestPollJob:

    def test_returns_status_dict(self):
        payload = {"job_id": "abc", "status": "running", "progress": 0.4,
                   "download_url": None, "error": None,
                   "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:01Z"}
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            result = api.poll_job(BASE, "abc", api_key=KEY)
        assert result["status"] == "running"
        assert result["progress"] == 0.4

    def test_sends_auth_header(self):
        payload = {"job_id": "abc", "status": "done", "progress": 1.0,
                   "download_url": "/v1/files/abc.fbx", "error": None,
                   "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:05Z"}
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)) as mock_open:
            api.poll_job(BASE, "abc", api_key=KEY)
            req = mock_open.call_args[0][0]
            assert req.get_header("Authorization") == f"Bearer {KEY}"

    def test_polls_v1_endpoint(self):
        payload = {"job_id": "abc", "status": "done", "progress": 1.0,
                   "download_url": None, "error": None,
                   "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:05Z"}
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)) as mock_open:
            api.poll_job(BASE, "abc", api_key=KEY)
            req = mock_open.call_args[0][0]
            assert "/v1/jobs/abc" in req.full_url


# ---------------------------------------------------------------------------
# fetch_characters()
# ---------------------------------------------------------------------------

class TestFetchCharacters:

    def test_returns_character_list(self):
        with patch("urllib.request.urlopen",
                   return_value=_mock_response({"characters": ["Y_bot", "Ch14_nonPBR"]})):
            chars = api.fetch_characters(BASE, api_key=KEY)
        assert chars == ["Y_bot", "Ch14_nonPBR"]

    def test_raises_loudly_on_error(self):
        # No-silent-fallback rule: a network failure must surface, never
        # degrade to a default character list.
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            with pytest.raises(RuntimeError, match="Failed to fetch characters"):
                api.fetch_characters(BASE, api_key=KEY)

    def test_falls_back_on_empty_response(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"characters": []})):
            chars = api.fetch_characters(BASE, api_key=KEY)
        assert chars == ["Y_bot"]

    def test_full_returns_path_scales(self):
        payload = {"characters": ["Y_bot", "Unitree G1"],
                   "categories": {"Y_bot": "humanoid", "Unitree G1": "robot"},
                   "path_scales": {"Y_bot": 1.0, "Unitree G1": 1.198}}
        with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
            names, categories, scales = api.fetch_characters_full(BASE, api_key=KEY)
        assert names == ["Y_bot", "Unitree G1"]
        assert categories["Unitree G1"] == "robot"
        assert scales["Unitree G1"] == 1.198

    def test_full_defaults_path_scales_when_absent(self):
        with patch("urllib.request.urlopen",
                   return_value=_mock_response({"characters": ["Y_bot"]})):
            _names, _categories, scales = api.fetch_characters_full(BASE, api_key=KEY)
        assert scales == {}


# ---------------------------------------------------------------------------
# _friendly_error() via operators
# ---------------------------------------------------------------------------

class TestFriendlyErrors:
    """Tests for api.friendly_error — pure logic, no bpy needed."""

    def test_error_code_rate_limited(self):
        result = api.friendly_error({"error": {"code": "rate_limited", "message": "slow down"}})
        assert "try again" in result.lower()

    def test_error_code_unauthorized(self):
        result = api.friendly_error({"error": {"code": "unauthorized", "message": "bad key"}})
        assert "api key" in result.lower()

    def test_error_code_timeout(self):
        result = api.friendly_error({"error": {"code": "timeout", "message": "too slow"}})
        assert "timed out" in result.lower()

    def test_unknown_code_falls_back_to_message(self):
        result = api.friendly_error({"error": {"code": "some_new_code", "message": "something broke"}})
        assert "something broke" in result

    def test_plain_string_error(self):
        result = api.friendly_error({"error": "MDM container crashed"})
        assert "MDM container crashed" in result


# ---------------------------------------------------------------------------
# fetch_tasks() — task catalog discovery
# ---------------------------------------------------------------------------

_TASKS_PAYLOAD = {"tasks": [
    {
        "id": "text",
        "display_name": "Text to Motion",
        "status": "active",
        "models": [
            {"type": "mdm",    "display_name": "MDM",    "available": True, "status": "active"},
            {"type": "momask", "display_name": "MoMask", "available": True, "status": "active"},
        ],
    },
    {
        "id": "trajectory",
        "display_name": "Trajectory",
        "status": "wip",
        "models": [
            {"type": "priormdm", "display_name": "priorMDM", "available": True, "status": "wip"},
            {"type": "kimodo",   "display_name": "Kimodo",   "available": False, "status": "future"},
        ],
    },
]}


class TestFetchTasks:

    def test_returns_task_list(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(_TASKS_PAYLOAD)):
            tasks = api.fetch_tasks(BASE, api_key=KEY)
        assert len(tasks) == 2
        assert tasks[0]["id"] == "text"
        assert tasks[1]["id"] == "trajectory"

    def test_task_models_included(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(_TASKS_PAYLOAD)):
            tasks = api.fetch_tasks(BASE, api_key=KEY)
        text_task = tasks[0]
        assert len(text_task["models"]) == 2
        assert text_task["models"][0]["type"] == "mdm"

    def test_raises_on_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            with pytest.raises(RuntimeError, match="Failed to fetch tasks"):
                api.fetch_tasks(BASE, api_key=KEY)

    def test_sends_auth_header(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(_TASKS_PAYLOAD)) as mock_open:
            api.fetch_tasks(BASE, api_key=KEY)
            req = mock_open.call_args[0][0]
            assert req.get_header("Authorization") == f"Bearer {KEY}"


# ---------------------------------------------------------------------------
# ensure_online_access() — Blender 4.2+ 'Allow Online Access' preference
# ---------------------------------------------------------------------------

class TestEnsureOnlineAccess:

    def _bpy_stub(self, online_access):
        """A bpy stand-in whose app.online_access has a concrete value."""
        bpy = MagicMock()
        bpy.app.online_access = online_access
        return bpy

    def test_blocked_when_online_access_false(self):
        with patch.dict(sys.modules, {"bpy": self._bpy_stub(False)}):
            with pytest.raises(RuntimeError, match="Allow Online Access"):
                api.ensure_online_access()

    def test_allowed_when_online_access_true(self):
        with patch.dict(sys.modules, {"bpy": self._bpy_stub(True)}):
            api.ensure_online_access()  # must not raise

    def test_allowed_when_attribute_missing(self):
        """Blender <= 4.1 has no bpy.app.online_access — treat as allowed."""
        bpy = MagicMock()
        del bpy.app.online_access
        with patch.dict(sys.modules, {"bpy": bpy}):
            api.ensure_online_access()  # must not raise

    def test_guard_covers_the_urlopen_chokepoint(self):
        """All requests flow through _urlopen — blocked pref must stop
        even a plain local GET before any socket is opened."""
        with patch.dict(sys.modules, {"bpy": self._bpy_stub(False)}):
            with patch("urllib.request.urlopen") as mock_open:
                with pytest.raises(RuntimeError, match="internet access"):
                    api.fetch_characters(BASE)
                mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# _urlopen() — HTTPS gets a working CA bundle (Blender's Python can't read
# the OS trust store, so a default context raises CERTIFICATE_VERIFY_FAILED)
# ---------------------------------------------------------------------------

class TestHttpsCertContext:

    def _allow_online(self):
        # ensure_online_access() runs inside _urlopen; a bpy whose
        # online_access is True lets it through without a real Blender.
        bpy = MagicMock()
        bpy.app.online_access = True
        return patch.dict(sys.modules, {"bpy": bpy})

    def test_https_request_gets_ssl_context(self):
        import urllib.request
        req = urllib.request.Request("https://example.hf.space/v1/characters")
        with self._allow_online():
            with patch("urllib.request.urlopen") as mock_open:
                api._urlopen(req, timeout=5)
        ctx = mock_open.call_args.kwargs.get("context")
        assert ctx is not None  # HTTPS must carry our verifying context

    def test_http_request_has_no_ssl_context(self):
        import urllib.request
        req = urllib.request.Request("http://localhost:8090/v1/characters")
        with self._allow_online():
            with patch("urllib.request.urlopen") as mock_open:
                api._urlopen(req, timeout=5)
        # Plain HTTP (local server) must not be handed an SSL context.
        assert "context" not in mock_open.call_args.kwargs

    def test_context_never_disables_verification(self):
        ctx = api._https_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True


# ---------------------------------------------------------------------------
# ensure_online_access() — respect the flag only where it matters
# ---------------------------------------------------------------------------

class TestOnlineAccess:
    class _FakeApp:
        def __init__(self, online, overridden):
            self.online_access = online
            self.online_access_overridden = overridden

    def _run(self, *, online, overridden, extension):
        import types as _types
        fake_bpy = _types.ModuleType("bpy")
        fake_bpy.app = self._FakeApp(online, overridden)
        old_bpy = sys.modules.get("bpy")
        old_pkg = api.__package__
        sys.modules["bpy"] = fake_bpy
        api.__package__ = "bl_ext.user_default.animoflow_blender" if extension else "animoflow_blender"
        try:
            api.ensure_online_access()  # raises RuntimeError if blocked
            return "ok"
        except RuntimeError as e:
            assert "internet access" in str(e)
            return "blocked"
        finally:
            api.__package__ = old_pkg
            if old_bpy is not None:
                sys.modules["bpy"] = old_bpy
            else:
                sys.modules.pop("bpy", None)

    def test_legacy_soft_preference_proceeds(self):
        # Typical case: legacy add-on, user simply hasn't ticked the box.
        assert self._run(online=False, overridden=False, extension=False) == "ok"

    def test_access_on_proceeds(self):
        assert self._run(online=True, overridden=False, extension=False) == "ok"

    def test_extension_blocks_when_off(self):
        assert self._run(online=False, overridden=False, extension=True) == "blocked"

    def test_overridden_blocks_even_for_legacy(self):
        # Command-line / org policy forced it off — can't just tick a box.
        assert self._run(online=False, overridden=True, extension=False) == "blocked"

    def test_no_bpy_proceeds(self):
        old_bpy = sys.modules.pop("bpy", None)
        try:
            api.ensure_online_access()  # no bpy → nothing to enforce
        finally:
            if old_bpy is not None:
                sys.modules["bpy"] = old_bpy
