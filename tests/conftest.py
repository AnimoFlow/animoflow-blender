# Prevent pytest from walking up into animoflow-blender/__init__.py (Blender addon).
# This conftest marks the tests/ directory as its own root.
collect_ignore_glob = ["../__init__.py", "../**/__init__.py"]
