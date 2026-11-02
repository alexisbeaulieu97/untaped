"""Storage: caches, state rows, directories and data files (``untaped.sdk.storage``).

A plugin keeps what it fetched in a :class:`Cache`, the rows only it has in
:class:`StateRows`, a folder of its own in a :class:`Dir`, and one file in a
:class:`DataFile`; each locks and stamps itself, so a plugin never handles a
lock or a version number of its own (:class:`Lock` is the one lock a plugin
holds over an operation). A cache's fill reports through a
:class:`ProgressHandle`. A :class:`Dir` with ``items=`` lets go of an item
through a :class:`ReleaseAsk` answered by a :class:`LetGo` or a
:class:`Refused`, and :func:`atomic_write`, :func:`file_lock`,
:func:`read_structured_file` and :func:`dir_bytes` are the file steps the
blocks are built from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk._lazy import lazy_names

if TYPE_CHECKING:
    from untaped.sdk.progress import NO_PROGRESS, ProgressHandle
    from untaped.sdk.storage._fs import atomic_write, dir_bytes, file_lock, read_structured_file
    from untaped.sdk.storage.cache import (
        Cache,
        Filled,
        Freshness,
        FreshnessRecord,
        NeverRefreshed,
        Served,
        code_version,
    )
    from untaped.sdk.storage.data_file import (
        Absent,
        DataFile,
        DataRead,
        Found,
        OtherVersion,
        Unreadable,
        read_data_file,
        write_data_file,
    )
    from untaped.sdk.storage.dir import Dir
    from untaped.sdk.storage.holds import (
        HolderView,
        LetGo,
        MoveAsk,
        Refused,
        ReleaseAsk,
        UnknownHolder,
    )
    from untaped.sdk.storage.lock import Lock
    from untaped.sdk.storage.state import RowExists, StateRows

__all__ = [
    "NO_PROGRESS",
    "Absent",
    "Cache",
    "DataFile",
    "DataRead",
    "Dir",
    "Filled",
    "Found",
    "Freshness",
    "FreshnessRecord",
    "HolderView",
    "LetGo",
    "Lock",
    "MoveAsk",
    "NeverRefreshed",
    "OtherVersion",
    "ProgressHandle",
    "Refused",
    "ReleaseAsk",
    "RowExists",
    "Served",
    "StateRows",
    "UnknownHolder",
    "Unreadable",
    "atomic_write",
    "code_version",
    "dir_bytes",
    "file_lock",
    "read_data_file",
    "read_structured_file",
    "write_data_file",
]

if not TYPE_CHECKING:  # a type checker reads the imports above; a run reads the table
    __getattr__, __dir__ = lazy_names(
        globals(),
        {
            "_fs": ("atomic_write", "dir_bytes", "file_lock", "read_structured_file"),
            "cache": (
                "Cache",
                "Filled",
                "Freshness",
                "FreshnessRecord",
                "NeverRefreshed",
                "Served",
                "code_version",
            ),
            "data_file": (
                "Absent",
                "DataFile",
                "DataRead",
                "Found",
                "OtherVersion",
                "Unreadable",
                "read_data_file",
                "write_data_file",
            ),
            "dir": ("Dir",),
            "holds": (
                "HolderView",
                "LetGo",
                "MoveAsk",
                "Refused",
                "ReleaseAsk",
                "UnknownHolder",
            ),
            "lock": ("Lock",),
            "state": ("RowExists", "StateRows"),
            "untaped.sdk.progress": ("NO_PROGRESS", "ProgressHandle"),
        },
    )
