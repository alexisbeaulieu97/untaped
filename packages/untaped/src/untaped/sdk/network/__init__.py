"""Network and secrets (``untaped.sdk.network``): the HTTP client, its retries, credentials.

``capped`` and ``succeeded`` are the upstream budget every call to an upstream
reads and reports to, so ``doctor --online`` and a cache's automatic refresh
fail fast offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped.sdk._lazy import lazy_names

if TYPE_CHECKING:
    from untaped.sdk.network.auth import find_program
    from untaped.sdk.network.credentials import Credential, CredentialKey, TlsVerify
    from untaped.sdk.network.http import (
        HttpClient,
        HttpFactory,
        RetryPolicy,
        paginate_link,
        paginate_offset,
        paginate_pages,
        rejected_token_error,
        same_origin,
    )
    from untaped.sdk.network.upstream import capped, succeeded

__all__ = [
    "Credential",
    "CredentialKey",
    "HttpClient",
    "HttpFactory",
    "RetryPolicy",
    "TlsVerify",
    "capped",
    "find_program",
    "paginate_link",
    "paginate_offset",
    "paginate_pages",
    "rejected_token_error",
    "same_origin",
    "succeeded",
]

if not TYPE_CHECKING:  # a type checker reads the imports above; a run reads the table
    __getattr__, __dir__ = lazy_names(
        globals(),
        {
            "auth": ("find_program",),
            "credentials": ("Credential", "CredentialKey", "TlsVerify"),
            "http": (
                "HttpClient",
                "HttpFactory",
                "RetryPolicy",
                "paginate_link",
                "paginate_offset",
                "paginate_pages",
                "rejected_token_error",
                "same_origin",
            ),
            "upstream": ("capped", "succeeded"),
        },
    )
