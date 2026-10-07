"""The one colour decision a screen's terminal makes, from the environment.

Rich renders a view into ANSI and prompt_toolkit paints that ANSI, so both
must agree on how many colours the terminal has: prompt_toolkit otherwise
re-quantizes Rich's 24-bit codes to 256 colours. :func:`detect_color` decides
once (``NO_COLOR``, ``COLORTERM``, ``TERM``, with Rich's own rules) and
:func:`rich_system` and :func:`ptk_depth_name` translate the decision for each
library. The depth is a *name* (``"DEPTH_24_BIT"``), so this module stays free
of prompt_toolkit; the adapter looks the member up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

__all__ = ["ColorMode", "detect_color", "ptk_depth_name", "rich_system"]

type ColorSystem = Literal["truecolor", "256", "standard"]

#: ``TERM`` suffixes (after the last hyphen) that mean 256 colours, as Rich reads them.
_TERM_256 = frozenset({"256color", "kitty"})


@dataclass(frozen=True)
class ColorMode:
    """How many colours the terminal shows and whether the user asked for none.

    ``no_color`` keeps bold, underline and reverse (the ``NO_COLOR`` convention
    removes colour only); ``system`` is then ``"standard"``.
    """

    system: ColorSystem
    no_color: bool = False


def detect_color(environ: Mapping[str, str]) -> ColorMode:
    """The colour mode ``environ`` asks for.

    A non-empty ``NO_COLOR`` wins; else ``COLORTERM`` of ``truecolor`` or
    ``24bit`` is true colour, a ``TERM`` ending in ``256color`` is 256 colours,
    and anything else is the 16 standard colours (Rich's own rule).
    """
    if environ.get("NO_COLOR", ""):
        return ColorMode("standard", no_color=True)
    if environ.get("COLORTERM", "").strip().lower() in ("truecolor", "24bit"):
        return ColorMode("truecolor")
    suffix = environ.get("TERM", "").strip().lower().rpartition("-")[2]
    if suffix in _TERM_256:
        return ColorMode("256")
    return ColorMode("standard")


def rich_system(mode: ColorMode) -> ColorSystem:
    """The ``color_system`` for Rich's console."""
    return mode.system


def ptk_depth_name(mode: ColorMode) -> str:
    """The name of the prompt_toolkit ``ColorDepth`` member that matches ``mode``."""
    if mode.no_color:
        return "DEPTH_1_BIT"
    return {"truecolor": "DEPTH_24_BIT", "256": "DEPTH_8_BIT", "standard": "DEPTH_4_BIT"}[
        mode.system
    ]
