"""The walker's named lists: what declarations alone can't tell it, each with its reason.

- :data:`IMPLEMENTERS`: SDK classes plugins implement rather than only call
  (D8: the direction an instance travels decides its shape). ``Contract``
  subclasses are implementer-shaped by rule and need no entry.
- :data:`HOOKS`: members of an SDK class that plugin classes override, which
  the SDK then reads or calls. An implementer's hooks join its ``implement=``
  list; a caller-shaped class's hooks are already its own lines. The repo's
  override sweep fails on an override that is in neither list.
- :data:`CONVENTIONS`: names authors write that core finds by convention, not
  by import; each becomes a ``convention`` line.
- :data:`WRITTEN_OUTSIDE`: hidden spellings untaped writes somewhere outside
  its own process, which are promised like visible ones (D9). Empty: the git
  credential helper untaped writes into worktree configs uses only visible
  spellings, and a repo test parses it to keep that true.
"""

from __future__ import annotations

from collections.abc import Mapping

IMPLEMENTERS: Mapping[str, str] = {
    "untaped.sdk.Command": "every command subclasses it, and core calls its run()",
    "untaped.sdk.Plugin": "every plugin subclasses it, and core reads its attributes",
    "untaped.sdk.contracts.Contract": "every provider subclasses it, and the SDK calls its ready()",
    "untaped.sdk.testing.PromptBackend": (
        "tests pass their own backend to invoke_cli(prompt_backend=) and "
        "UiContext(prompt_backend=), and the SDK calls its methods"
    ),
}

HOOKS: Mapping[str, Mapping[str, str]] = {
    "untaped.sdk.contracts.Contract": {
        "ready": "a provider overrides it for a condition its settings can't express",
    },
    "untaped.sdk.UntapedError": {
        "category": "subclasses set it; the exit code and the failure record read it",
        "hint": "subclasses set it; the error report prints it",
        "system": "subclasses set it; the failure record reads it",
        "fix_by": "subclasses set it; the error report and the failure record read it",
        "exit_code": "a subclass whose code no category names (Matched) overrides it; "
        "report_errors reads it",
    },
    "untaped.sdk.HttpError": {
        "category": "subclasses set it; the exit code and the failure record read it",
        "describes_body": "subclasses set it; the error report reads it",
    },
    "untaped.sdk.Record": {
        "table_columns": "record classes set it; the table renderer reads it",
    },
}

CONVENTIONS: Mapping[str, str] = {
    "entry-point untaped.plugins": "the entry-point group plugin distributions register under",
    "module untaped.sdk.testing.plugin": "the pytest plugin a plugin's conftest names",
    "module <owner>.testing.conformance": "where plugin check finds an owner's conformance checks",
    "file fills.json": "the file assert_fills writes and plugin check reads",
    "attribute renamed_keys": "core reads it off a settings model for renamed keys",
    "attribute retired_keys": "core reads it off a settings model for retired keys",
    "attribute token_sources": "core reads it off a settings model for where a token comes from",
}

WRITTEN_OUTSIDE: Mapping[str, str] = {}
