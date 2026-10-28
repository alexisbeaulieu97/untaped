"""awx fills the lint plugin's contracts: its rules, and apply documents as subjects.

``untaped lint`` runs :class:`AwxRules` over every ``awx.resources``
subject. :class:`AwxDocuments` is the reader a repository's
``.untaped/lint.yml`` names under ``readers:``: the files its globs match
are loaded as ``awx lint FILES --offline`` loads them, so a repository lints
its apply documents with no controller and no token.
"""

from __future__ import annotations

from typing import ClassVar

from untaped.sdk import Record
from untaped.sdk.contracts import Provider
from untaped_awx.application.lint.documents import load_documents
from untaped_awx.domain.lint.rules import RULES
from untaped_awx.infrastructure.lint.documents import documents_of
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_lint.api import RuleFunction, Rules, Tree, TreeReader


class AwxRules(Rules):
    """The awx rules, over the ``awx.resources`` snapshot; ready offline."""

    RULES: ClassVar[tuple[RuleFunction, ...]] = RULES


class AwxDocuments(TreeReader, Provider):
    """The apply documents of a repository, as one ``awx.resources`` subject.

    References resolve within the files read, as under ``--offline``. A file
    that is not UTF-8 is lint's to report (``lint.unreadable-file``).
    """

    def read(self, tree: Tree) -> list[Record]:
        texts = [(file.path, file.text) for file in tree.files()]
        if not texts:
            return []
        resources = load_documents(documents_of(texts), catalog=AwxResourceCatalog())
        return [resources]


__all__ = ["AwxDocuments", "AwxRules"]
