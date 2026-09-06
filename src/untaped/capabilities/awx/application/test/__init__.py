"""Application use cases for ``untaped awx test`` (loader, resolver, runner).

Domain-pure orchestration. Concrete adapters arrive via the ``Protocol``s
in :mod:`untaped.capabilities.awx.application.test.ports`; tests inject stubs so the
use cases never touch the filesystem, Jinja2, httpx, or the CLI framework directly.
"""

from untaped.capabilities.awx.application.test.ports import (
    Filesystem,
    Launcher,
    Parser,
    Prompt,
    VarsResolver,
    Watcher,
)

__all__ = ["Filesystem", "Launcher", "Parser", "Prompt", "VarsResolver", "Watcher"]
