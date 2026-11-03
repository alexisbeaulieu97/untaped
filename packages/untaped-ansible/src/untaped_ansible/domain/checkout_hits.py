"""Which dependent each checkout checks out: ``impact checkouts``' rows from the providers' answers.

A checkout hangs under the dependent node of the repo its URL names, at the
ref it checks out: its own ref, or the repo's default branch when it names
none. A checkout of a dependent's repo at a ref the source holds no
dependent at comes with no dependent ("not in the source"); one of any other
repo is not about the role, so it is left out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from untaped_ansible.checkouts import Checkout
from untaped_ansible.domain.identity import repo_key
from untaped_ansible.records import CheckoutHit, FoundIn, ReachedRow


def checkout_hits(
    reached: Sequence[ReachedRow],
    checkouts: Iterable[Checkout],
    *,
    repo_of: Callable[[str], str | None],
    default_branches: Mapping[str, str | None],
) -> list[CheckoutHit]:
    """One row per checkout and dependent it checks out, those not in the source last.

    ``repo_of`` names the repo a URL is (``None``: not one the source can
    hold); ``default_branches`` holds each dependent repo's default branch by
    :func:`repo_key`, where the source knows it.
    """
    nodes: dict[str, dict[str | None, FoundIn]] = {}
    for row in reached:
        if row.repo is None:
            continue
        refs = nodes.setdefault(repo_key(row.repo), {})
        refs.setdefault(row.ref, FoundIn(repo=row.repo, ref=row.ref, path=row.path))
    hits: list[CheckoutHit] = []
    for checkout in checkouts:
        repo = repo_of(str(checkout.url))
        key = repo_key(repo) if repo is not None else None
        if key is None or key not in nodes:
            continue
        ref = checkout.ref or default_branches.get(key)
        hits.append(CheckoutHit(**dict(checkout), dependent=nodes[key].get(ref)))
    return sorted(hits, key=_order)


def _order(hit: CheckoutHit) -> tuple[bool, str, str, str]:
    found = hit.dependent
    if found is None:
        return (True, str(hit.url).lower(), hit.ref or "", hit.name)
    return (False, repo_key(found.repo), found.ref or "", hit.name)


__all__ = ["checkout_hits"]
