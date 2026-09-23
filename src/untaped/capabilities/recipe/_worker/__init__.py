"""Isolated hook-worker files: the worker script and the stdlib-only modules it needs.

The worker is launched as a script (``python -P _worker/hook_worker.py``) in a
pack's uv environment. Keeping it in a directory that holds only worker files
means nothing from the recipe capability (``cli``, ``domain``, ``settings``,
...) can shadow a pack's own top-level modules.
"""
