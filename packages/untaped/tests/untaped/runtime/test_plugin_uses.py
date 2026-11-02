"""Who owns a module: the longest package of a composed plugin, else untaped's."""

from __future__ import annotations

from untaped.runtime.plugin_uses import Owner, owner_of

_OTHER = Owner("untaped-other", "other")
_OWNERS = {"untaped": Owner("untaped", "untaped", core=True), "untaped_other": _OTHER}


def test_the_longest_package_owns_a_module() -> None:
    nested = Owner("untaped-acme", "acme")
    owners = {**_OWNERS, "untaped.plugins.acme": nested}
    assert owner_of("untaped.plugins.acme.api", owners) is nested
    assert owner_of("untaped.sdk", owners) is _OWNERS["untaped"]
    assert owner_of("untaped_other.api", owners) is _OTHER
    assert owner_of("requests", owners) is None
