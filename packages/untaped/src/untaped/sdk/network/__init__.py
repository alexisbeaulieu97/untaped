"""Network and secrets (``untaped.sdk.network``): the HTTP client, its retries, credentials."""

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
        connected_client,
        paginate_link,
        paginate_offset,
        paginate_pages,
        rejected_token_error,
    )

__all__ = [
    "Credential",
    "CredentialKey",
    "HttpClient",
    "HttpFactory",
    "RetryPolicy",
    "TlsVerify",
    "connected_client",
    "find_program",
    "paginate_link",
    "paginate_offset",
    "paginate_pages",
    "rejected_token_error",
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
                "connected_client",
                "paginate_link",
                "paginate_offset",
                "paginate_pages",
                "rejected_token_error",
            ),
        },
    )
