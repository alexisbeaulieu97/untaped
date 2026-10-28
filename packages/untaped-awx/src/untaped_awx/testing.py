"""Test helpers for rules over AWX resources: a snapshot from YAML text.

``resources(text)`` loads apply documents exactly as ``awx lint FILES
--offline`` and the repository reader do, so a rule's unit test sees the
references, states and facts a real run would::

    from untaped_awx.testing import resources
    from untaped_lint.testing import run_rule

    found = run_rule(vault_credential, resources(TEMPLATES_YAML))
"""

from __future__ import annotations

from untaped.sdk import experimental
from untaped_awx.application.lint.documents import load_documents
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.infrastructure.lint.documents import documents_of
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog


@experimental
def resources(*yaml_texts: str) -> Resources:
    """The offline snapshot of ``yaml_texts``, one file each (``text1.yml``, ``text2.yml``…)."""
    texts = [(f"text{index}.yml", text) for index, text in enumerate(yaml_texts, start=1)]
    return load_documents(documents_of(texts), catalog=AwxResourceCatalog())


__all__ = ["resources"]
