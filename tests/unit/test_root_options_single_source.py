"""Wave 1.3 fix: the root-option machinery has exactly one source.

:mod:`untaped._root_options` owns the position-independent ``--profile`` /
``--verbose`` / ``--quiet`` table plus the consume/dispatch helpers.
:mod:`untaped.bootstrap` binds the subset it calls; there must be one source.
"""

from __future__ import annotations

import untaped._root_options as shared
import untaped.bootstrap as bootstrap

# Names bootstrap.py binds (what its install/dispatch path calls).
_BOOTSTRAP_NAMES = (
    "_RootOption",
    "_root_options",
    "_root_callback_signature",
    "_consume_leading_root_options",
    "_dispatch_with_root_options",
)


def test_bootstrap_shares_machinery() -> None:
    for name in _BOOTSTRAP_NAMES:
        assert getattr(bootstrap, name) is getattr(shared, name), name


def test_both_roots_build_identical_option_tables() -> None:
    assert set(bootstrap._root_options()) == {
        "--profile",
        "--verbose",
        "--quiet",
    }
