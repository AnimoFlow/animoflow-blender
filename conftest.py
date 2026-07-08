"""
Root conftest — stubs bpy + tells pytest to skip the Blender addon __init__.py.
The addon root __init__.py uses relative imports that fail outside Blender.
"""
import sys
from unittest.mock import MagicMock

# Skip the Blender addon __init__.py during test collection
collect_ignore = ["__init__.py"]

if "bpy" not in sys.modules:
    bpy = MagicMock()
    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = bpy.props
    sys.modules["bpy.types"] = bpy.types
    sys.modules["bpy.utils"] = bpy.utils
    sys.modules["bpy.app"] = bpy.app
