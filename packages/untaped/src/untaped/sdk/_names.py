"""How a class name is spelled where untaped derives a name from it.

A record's kind is ``<plugin>.<snake(class)>`` and a command's word is
``kebab(class)``; composition and the SDK's kind lookup read the same rule.
"""

from __future__ import annotations

import re

_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")


def words(name: str) -> list[str]:
    """The words of a class name, a run of capitals one word: ``SSHKeys`` is ``ssh``, ``keys``."""
    return [word.lower() for word in _WORDS.findall(name)]


def snake(name: str) -> str:
    """A class name as a kind's last part: ``TestResult`` is ``test_result``."""
    return "_".join(words(name))


def kebab(name: str) -> str:
    """A class name as a command word: ``JobTemplates`` is ``job-templates``."""
    return "-".join(words(name))
