"""The output package owns stderr.

``policy`` decides what one invocation asked of stderr (text or JSON,
``--quiet``, ``--verbose``) and where the current invocation is; ``diagnostic``
holds the wire models (the kind machinery records share, and the stderr
kinds); ``sink`` is the one writer, its scope and the derived exit code.
Nothing outside this package writes to stderr or asks the mode.
"""

from __future__ import annotations

from untaped.output.diagnostic import (
    Debug,
    Diagnostic,
    Failure,
    Message,
    Progress,
    StaleData,
)
from untaped.output.policy import OutputPolicy
from untaped.output.progress import NO_PROGRESS, ProgressHandle
from untaped.output.sink import (
    Diagnostics,
    current_sink,
    diagnostics_scope,
    failure_exit_code,
)

__all__ = [
    "NO_PROGRESS",
    "Debug",
    "Diagnostic",
    "Diagnostics",
    "Failure",
    "Message",
    "OutputPolicy",
    "Progress",
    "ProgressHandle",
    "StaleData",
    "current_sink",
    "diagnostics_scope",
    "failure_exit_code",
]
