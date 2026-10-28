"""The settings digest: one short name for what a plugin's own section says.

Data kept per settings (a ``Cache(split="settings")`` folder, a contract
answer) is named by it, so two runs whose section reads the same share that
data whichever profile they picked, and a run whose section reads otherwise
never sees it.

The digest is the first 16 hex digits of the sha256 of canonical JSON
(sorted keys, no spaces) of two terms:

- ``settings``: the values some layer set (the config file, a variable), by
  their current names after renames. Model defaults never count, so a release
  that adds a defaulted field moves nobody's data; a release whose changed
  default alters what a cache holds bumps that cache's ``version=``. The keys
  the SDK injects into every section (:data:`~untaped.runtime.settings.RESERVED_SECTION_KEYS`:
  cache max ages, extension ranks) and every secret are left out.
- ``credentials``: for each credential the plugin declares, the step its
  ``get`` would use for this run's key: the variable's name, or ``stored``
  (read from the environment and the index, never a value or a backend).
  A run on another credential source never shares a folder with your stored
  login, while the same variable with a new value keeps its folder. Data that
  depends on who asks passes the credential as the cache's ``partition=``.

Core's own sections (``http``) are no plugin's: they are never part of it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, SecretStr

from untaped.runtime.settings import (
    RESERVED_SECTION_KEYS,
    load_settings_section,
    registered_profile_model,
)
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.network.credentials import Credential, credential_key, declared_credentials

#: Hex digits of the sha256 kept: a folder name, still far from any collision.
DIGEST_LENGTH = 16


def settings_digest(section: str) -> str:
    """The digest of plugin ``section`` as the active settings read it.

    An unregistered section (a plugin used uncomposed) digests as empty. An
    invalid section raises its :class:`~untaped.sdk.errors.ConfigError`.
    """
    return digest_of(settings_terms(section))


def settings_terms(section: str) -> dict[str, Any]:
    """What :func:`settings_digest` hashes for ``section``: its set values and secret sources."""
    if registered_profile_model(section) is None:
        return {"settings": {}, "credentials": {}}
    loaded: BaseModel = load_settings_section(section)
    declared = declared_credentials(section)
    secrets = {
        name
        for name, field in type(loaded).model_fields.items()
        if unwrap_optional(field.annotation) is SecretStr
    }
    values = loaded.model_dump(
        mode="json", exclude_unset=True, exclude=set(RESERVED_SECTION_KEYS) | secrets
    )
    sources = {each.name: _step(each, loaded) for each in declared}
    return {
        "settings": values,
        "credentials": {name: source for name, source in sources.items() if source is not None},
    }


def _step(credential: Credential[Any], settings: BaseModel) -> str | None:
    """The step ``credential.get`` would use for this run's key, named; ``None`` for none."""
    key = credential_key(credential, settings)
    answer = None if key is None else credential.answer(key)
    if answer is None:
        return None
    return "stored" if answer.step == "stored" else answer.source.removeprefix("$")


def digest_of(terms: Mapping[str, Any]) -> str:
    """The digest of ``terms``: sha256 of their canonical JSON, cut to :data:`DIGEST_LENGTH`."""
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:DIGEST_LENGTH]
