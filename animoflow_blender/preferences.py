"""
AnimoFlow addon preferences (Edit > Preferences > Add-ons > AnimoFlow).
"""
import bpy
from bpy.props import EnumProperty, StringProperty


# Hardcoded HF Space URL for cloud mode. Single source of truth — change
# here if the Space ever moves. Cloud mode talks to this Space directly
# over the Gradio Queue HTTP API so ZeroGPU attributes generation quota
# to the user's own HF account (free ~5 min/day, PRO ~25 min/day) instead
# of burning a shared bucket.
ANIMOFLOW_HF_SPACE_URL = "https://animoflow-animoflow-demo.hf.space"


# Connection-status banner shown in the Preferences pane. Set by the
# Connect operator; read by AnimoFlowPreferences.draw(). Module-level so
# we don't have to round-trip through a saved StringProperty (the user
# never edits this directly).
#
# Format: "" (initial) | "ok: <message>" | "err: <message>"
_connection_status = ""


def get_connection_status() -> str:
    return _connection_status


def set_connection_status(s: str) -> None:
    global _connection_status
    _connection_status = s


def _auto_refresh_on_server_change(self, context):
    """Called when server_mode or api_url changes (NOT api_key — see below).

    The api_key field fires its update callback on every keystroke, which
    spuriously hits the server with partial tokens and 401s. The Connect
    button is the explicit "test now" entry point for cloud mode; we keep
    auto-refresh only for the truly safe triggers (mode toggle, local URL
    edit) where every state is plausibly valid.

    Refresh runs asynchronously (refresh.refresh_async) — never block the
    UI thread on network I/O from a property-update callback.
    """
    from . import refresh
    try:
        refresh.refresh_from_prefs(context)
    except Exception as e:
        print(f"[animoflow] auto-refresh on server change failed: {e}")
        set_connection_status(f"err: {e}")


class AnimoFlowPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    server_mode: EnumProperty(
        name="Server",
        description="Which AnimoFlow server to connect to",
        items=[
            ("none",  "Not configured",      "Choose a server to get started"),
            ("cloud", "AnimoFlow Cloud",     "Use the hosted AnimoFlow service with an API key"),
            ("local", "Own Server",          "Run the AnimoFlow server locally"),
        ],
        default="none",
        update=_auto_refresh_on_server_change,
    )

    api_key: StringProperty(
        name="Hugging Face Token",
        description=(
            "Your personal HF access token. Get one at "
            "huggingface.co/settings/tokens (any scope works for "
            "generation; 'inference-api' gives ZeroGPU PRO attribution "
            "if you have a PRO account). Your quota is used, not "
            "AnimoFlow's."
        ),
        default="",
        subtype="PASSWORD",
        # No `update=` here on purpose — see _auto_refresh_on_server_change
        # docstring. The Connect button is the explicit entry point.
    )

    api_url: StringProperty(
        name="Server URL",
        description="URL of your local AnimoFlow server",
        default="http://localhost:8090",
        update=_auto_refresh_on_server_change,
    )

    def get_base_url(self):
        """Return the effective base URL, or None if not configured.

        Cloud mode returns the HF Space URL; the api module dispatches
        on the .hf.space suffix to pick the Space submission path.
        """
        if self.server_mode == "cloud":
            return ANIMOFLOW_HF_SPACE_URL if self.api_key.strip() else None
        if self.server_mode == "local":
            return self.api_url.rstrip("/") or None
        return None

    def is_configured(self):
        """True if the user has completed setup."""
        if self.server_mode == "cloud":
            return bool(self.api_key.strip())
        if self.server_mode == "local":
            return bool(self.api_url.strip())
        return False

    def draw(self, context):
        layout = self.layout

        layout.prop(self, "server_mode", expand=True)
        layout.separator()

        if self.server_mode == "none":
            box = layout.box()
            box.label(text="Pick a server mode above to get started.", icon="INFO")

        elif self.server_mode == "cloud":
            # Token field + Connect button side-by-side so the user has a
            # clear "I'm done pasting, hit this now" action. Auto-refresh
            # on keystroke was confusing (silent 401s on partial paste).
            row = layout.row(align=True)
            row.prop(self, "api_key")
            connect = row.operator("animoflow.connect", text="Connect", icon="LINKED")
            row.enabled = True  # keep both interactive even pre-paste

            status = get_connection_status()
            if not self.api_key.strip():
                box = layout.box()
                box.label(text="1. Paste your Hugging Face access token above.", icon="INFO")
                box.label(text="2. Click Connect.")
                box.label(text="Get a token: huggingface.co/settings/tokens")
                box.label(text="Your free or PRO HF quota gets used — you control it.")
            elif status.startswith("busy:"):
                box = layout.box()
                box.label(text=status[5:].strip().capitalize(), icon="SORTTIME")
            elif status.startswith("ok:"):
                box = layout.box()
                box.label(text=status[3:].strip(), icon="CHECKMARK")
                box.label(text=f"Cloud: {ANIMOFLOW_HF_SPACE_URL}", icon="WORLD")
            elif status.startswith("err:"):
                box = layout.box()
                # Trim long error messages so the prefs pane stays usable
                msg = status[4:].strip()
                box.label(text=f"Connection failed: {msg[:80]}", icon="ERROR")
                if len(msg) > 80:
                    box.label(text=msg[80:160])
                box.label(text="Check the token, then click Connect again.")
            else:
                box = layout.box()
                box.label(text="Token saved — click Connect to load characters + models.", icon="INFO")

        elif self.server_mode == "local":
            row = layout.row(align=True)
            row.prop(self, "api_url")
            row.operator("animoflow.connect", text="Connect", icon="LINKED")
            status = get_connection_status()
            if status.startswith("busy:"):
                layout.label(text=status[5:].strip().capitalize(), icon="SORTTIME")
            elif status.startswith("ok:"):
                layout.label(text=status[3:].strip(), icon="CHECKMARK")
            elif status.startswith("err:"):
                layout.label(text=f"Connection failed: {status[4:].strip()[:90]}", icon="ERROR")
            layout.label(
                text="Start the AnimoFlow server:  uvicorn main:app --port 8090",
                icon="INFO",
            )


def register():
    bpy.utils.register_class(AnimoFlowPreferences)


def unregister():
    bpy.utils.unregister_class(AnimoFlowPreferences)
