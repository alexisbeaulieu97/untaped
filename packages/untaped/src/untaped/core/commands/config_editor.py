"""$EDITOR flow for the root ``untaped config edit`` command."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from untaped.runtime.config_file import read_config_text, replace_config_text
from untaped.runtime.settings import (
    FORMAT_VERSION,
    NewerFormatError,
    OlderFormatError,
    check_config_text,
    resolve_config_path,
    validate_config_file,
)
from untaped.runtime.sink import Diagnostics
from untaped.sdk._cli import report_errors
from untaped.sdk.errors import ConfigError, attribution
from untaped.sdk.prompt.editor import run_editor
from untaped.sdk.storage._fs import atomic_write
from untaped.sdk.words import run_hint

#: A fresh home's draft: a non-empty config file is read only when stamped.
_FRESH = f"format_version: {FORMAT_VERSION}\n"


def run_config_editor() -> None:
    """Edit a private copy of the config file in $VISUAL/$EDITOR; save it if valid.

    The copy is validated before anything is written; a valid change is saved
    like any other config write (under the config lock, atomic, owner-only,
    through a symlink), line endings untouched. Once the editor has saved
    changes to the copy (even if it then exits with an error), any failure
    leaves the config file as it was and keeps the edited copy so the work is
    not lost; an unchanged copy is removed.
    """
    with report_errors():
        path = resolve_config_path()
        original = read_config_text(path)
        if original is not None:
            _refuse_a_newer_format(lambda: check_config_text(original, path))
        workdir = Path(tempfile.mkdtemp(prefix="untaped-config-edit-"))
        draft = workdir / path.name
        edited_by_user = False
        try:
            atomic_write(draft, original or _FRESH, mode=0o600)
            run_editor(draft)
            edited_by_user = True
            with draft.open(encoding="utf-8", newline="") as handle:
                edited = handle.read()
            if edited == (original or _FRESH):
                shutil.rmtree(workdir, ignore_errors=True)
                Diagnostics().info(f"no changes; config unchanged ({path})")
                return
            try:
                validate_config_file(draft)
            except NewerFormatError as exc:
                raise ConfigError(
                    f"format_version {exc.version} is newer than this release supports "
                    f"(format {FORMAT_VERSION})",
                    category="invalid",
                ) from exc
            except OlderFormatError as exc:
                raise ConfigError(
                    f"the edit is not format {FORMAT_VERSION}",
                    category="invalid",
                    hint=(
                        f"start it with `format_version: {FORMAT_VERSION}`, the base's sections "
                        "at the top and named profiles under `profiles:`"
                    ),
                ) from exc
            except ConfigError as exc:
                # The edit is the invalid input here, not the setup.
                raise ConfigError(str(exc), category="invalid") from exc
            replace_config_text(edited, expected=original, path=path)
        except (ConfigError, OSError, UnicodeDecodeError) as exc:
            if not edited_by_user and not _saved_changes(draft, original or _FRESH):
                shutil.rmtree(workdir, ignore_errors=True)
                if isinstance(exc, ConfigError):
                    raise
                raise ConfigError(f"could not edit {path}: {exc}") from exc
            detail = str(exc) if isinstance(exc, ConfigError) else f"could not save {path}: {exc}"
            fields = attribution(exc)
            if isinstance(exc, UnicodeDecodeError):
                fields = {"category": "invalid"}
            if not edited_by_user:
                # The editor failed after saving: its edits were never checked.
                from untaped.core.commands.doctor import Check  # noqa: PLC0415 - a cycle

                fields["hint"] = (
                    f"to apply them, copy {draft} over {path}, then {run_hint(Check.spelled())}"
                )
            raise ConfigError(
                f"{detail}\nconfig left unchanged; your edits are in {draft}", **fields
            ) from exc
        shutil.rmtree(workdir, ignore_errors=True)
        Diagnostics().success(f"config saved and validated (config: {path})")


def _refuse_a_newer_format(check: Callable[[], object]) -> None:
    """Never edit beside a file a newer untaped wrote; leave any other error to repair.

    An 11.x ``config.yml`` opens too, so it can be rewritten by hand as format 2.
    """
    try:
        check()
    except NewerFormatError:
        raise
    except ConfigError:
        pass


def _saved_changes(draft: Path, original: str) -> bool:
    """Whether ``draft`` no longer holds ``original`` (a missing draft holds no changes)."""
    try:
        with draft.open(encoding="utf-8", newline="") as handle:
            return handle.read() != original
    except UnicodeDecodeError:
        return True
    except OSError:
        return False
