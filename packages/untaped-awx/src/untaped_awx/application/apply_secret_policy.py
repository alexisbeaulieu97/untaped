"""Decide which top-level fields are safe to omit from a PATCH.

Second pass over secrets in the apply pipeline. After
:func:`untaped_awx.application._secret_paths.strip_encrypted_in_place` has
removed ``$encrypted$`` placeholders from the user's write payload,
:class:`SecretPreservationPolicy` compares the stripped subtree against
the existing record's subtree (with the same paths removed) to decide
which top-level keys can safely be omitted from the PATCH body — AWX
retains them, including the secret values — versus which carry a
sibling change that would silently clobber the existing secret.

Pure value-shaped class: no I/O, no Protocols, no network. The class
wrapper exists so the orchestrator can constructor-inject the policy
(simplifies focused testing and future configurability) and so the API
shape lines up with the other apply collaborators.
"""

from __future__ import annotations

import copy
from typing import Any


class SecretPreservationPolicy:
    """Two-pass-secrets handler — partition + strip helpers."""

    def partition(
        self,
        *,
        write_payload: dict[str, Any],
        existing: dict[str, Any] | None,
        preserved: list[str],
    ) -> tuple[set[str], list[str]]:
        """Decide which preserved-secret top-level fields are safe to omit.

        For each top-level key that contains at least one preserved
        secret path, compare the user's stripped subtree against the
        existing record's subtree with the same paths removed:

        - Equal → field is added to ``preserved_fields`` (omit from
          the PATCH; AWX keeps the value).
        - Different → field is added to ``conflict_fields`` (a sibling
          change alongside the placeholder; PATCHing would clobber the
          secret).

        ``existing is None`` (create path) returns empty sets — there's
        nothing to preserve, and ``_do_create`` enforces the
        no-placeholders rule separately.
        """
        if existing is None:
            return set(), []
        top_keys = {path.split(".", 1)[0] for path in preserved}
        existing_stripped = self.strip_paths(existing, preserved)
        preserved_fields: set[str] = set()
        conflict_fields: list[str] = []
        for top in top_keys:
            # ``dict.get(top)`` returns ``None`` for an absent key and
            # the actual value (often ``{}`` after stripping) when
            # present. The comparison treats those as different on
            # purpose: an unset subtree is a real sibling change vs. a
            # stripped-empty subtree, and the user should see the
            # conflict so they don't silently overwrite the secret.
            if write_payload.get(top) == existing_stripped.get(top):
                preserved_fields.add(top)
            else:
                conflict_fields.append(top)
        return preserved_fields, conflict_fields

    @staticmethod
    def strip_paths(obj: Any, paths: list[str]) -> Any:
        """Return a deep copy of ``obj`` with the given dotted paths removed.

        Path syntax matches ``ResourceSpec.secret_paths``: ``*`` matches
        any list element or dict key.
        """
        result = copy.deepcopy(obj)
        for path in paths:
            _remove_at_path(result, path.split("."))
        return result


def _remove_at_path(obj: Any, parts: list[str]) -> None:
    if not parts or obj is None:
        return
    head = parts[0]
    rest = parts[1:]
    if not rest:
        if isinstance(obj, dict):
            if head == "*":
                obj.clear()
            else:
                obj.pop(head, None)
        elif isinstance(obj, list) and head == "*":
            obj.clear()
        return
    if isinstance(obj, dict):
        if head == "*":
            for key in list(obj.keys()):
                _remove_at_path(obj[key], rest)
        elif head in obj:
            _remove_at_path(obj[head], rest)
    elif isinstance(obj, list) and head == "*":
        for item in obj:
            _remove_at_path(item, rest)
