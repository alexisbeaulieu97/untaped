"""Wave 1.3 fix: the root-option machinery has exactly one source.

:mod:`untaped._root_options` owns the position-independent ``--profile`` /
``--verbose`` / ``--quiet`` table plus the consume/dispatch helpers.
:mod:`untaped.run` (legacy surface) re-exports every name it used to define
so existing imports keep working; :mod:`untaped.bootstrap` binds the subset
it calls. Both must resolve to the identical objects — never a second copy.
"""

from __future__ import annotations

import untaped._root_options as shared
import untaped.bootstrap as bootstrap
import untaped.run as run

_SHARED_NAMES = (
    "_FLAG_PRESENT",
    "_PROFILE_HELP",
    "_VERBOSE_HELP",
    "_QUIET_HELP",
    "_RootOption",
    "_apply_profile",
    "_reset_profile",
    "_reset_verbose_option",
    "_reset_quiet_option",
    "_root_options",
    "_option_names",
    "_match_option",
    "_consume_option_at",
    "_root_callback_signature",
    "_consume_leading_root_options",
    "_dispatch_with_root_options",
    "_unknown_root_option",
    "_strip_trailing_root_option",
    "_extract_root_option_value",
    "_apply_root_option",
)

# Names bootstrap.py binds (what its install/dispatch path calls).
_BOOTSTRAP_NAMES = (
    "_RootOption",
    "_root_options",
    "_root_callback_signature",
    "_consume_leading_root_options",
    "_dispatch_with_root_options",
)


def test_run_reexports_shared_machinery() -> None:
    for name in _SHARED_NAMES:
        assert getattr(run, name) is getattr(shared, name), name


def test_bootstrap_shares_machinery() -> None:
    for name in _BOOTSTRAP_NAMES:
        assert getattr(bootstrap, name) is getattr(shared, name), name


def test_both_roots_build_identical_option_tables() -> None:
    assert (
        set(run._root_options())
        == set(bootstrap._root_options())
        == {
            "--profile",
            "--verbose",
            "--quiet",
        }
    )
