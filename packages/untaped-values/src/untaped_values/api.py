"""The values plugin's declared public module: the only untaped_values code other plugins import.

One inputs language for every file that declares values a person or script
answers. :class:`Input` is a declaration (each consumer subclasses it for its
own fields), :func:`coerce` converts a given value, :func:`resolve` finds each
value (flags and files, the default, then an answer :func:`terminal_ask`
gives), :func:`redact` hides secrets for display, :func:`render` puts values
into a text field strictly, and :func:`renderer_file` is the renderer for a
process where untaped isn't installed. :class:`Vars` is the option block a
command declares for ``--var``, ``--vars-file`` and ``--non-interactive``.
Every name is experimental until a second plugin uses it.
The closed :data:`__all__` keeps the boundary explicit.
"""

from __future__ import annotations

from untaped_values.errors import InputError
from untaped_values.inputs import Input, coerce
from untaped_values.options import Vars
from untaped_values.ports import Ask
from untaped_values.rendering import redact, render, renderer_file
from untaped_values.resolution import Resolved, resolve, terminal_ask

__all__ = [
    "Ask",
    "Input",
    "InputError",
    "Resolved",
    "Vars",
    "coerce",
    "redact",
    "render",
    "renderer_file",
    "resolve",
    "terminal_ask",
]
