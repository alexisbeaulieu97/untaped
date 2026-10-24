"""Storage: caches, state rows, directories and data files (``untaped.sdk.storage``).

A plugin keeps what it fetched in a :class:`Cache`, the rows only it has in
:class:`StateRows`, a folder of its own in a :class:`Dir`, and one file in a
:class:`DataFile`; each locks and stamps itself, so a plugin never handles a
lock or a version number of its own (:class:`Lock` is the one lock a plugin
holds over an operation). A cache's fill reports through a
:class:`ProgressHandle`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk._lazy import lazy_names

if TYPE_CHECKING:
    from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
    from untaped.sdk.storage.cache import Cache, Filled, NeverRefreshed, Served
    from untaped.sdk.storage.data_file import Absent, DataFile, Found, OtherVersion, Unreadable
    from untaped.sdk.storage.dir import Dir
    from untaped.sdk.storage.lock import Lock
    from untaped.sdk.storage.state import RowExists, StateRows

__all__ = [
    "NO_PROGRESS",
    "Absent",
    "Cache",
    "DataFile",
    "Dir",
    "Filled",
    "Found",
    "Lock",
    "NeverRefreshed",
    "OtherVersion",
    "ProgressHandle",
    "RowExists",
    "Served",
    "StateRows",
    "Unreadable",
]

if not TYPE_CHECKING:  # a type checker reads the imports above; a run reads the table
    __getattr__, __dir__ = lazy_names(
        globals(),
        {
            "cache": ("Cache", "Filled", "NeverRefreshed", "Served"),
            "data_file": ("Absent", "DataFile", "Found", "OtherVersion", "Unreadable"),
            "dir": ("Dir",),
            "lock": ("Lock",),
            "state": ("RowExists", "StateRows"),
            "untaped.sdk.progress": ("NO_PROGRESS", "ProgressHandle"),
        },
    )
