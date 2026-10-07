"""Single-line editing shared by the text components: a buffer and the keys that edit it.

:class:`EditBuffer` is plain data (text and caret position) with the editing
keys as methods that return the next buffer, so ``TextInput``, ``SecretInput``
and ``NumberInput`` edit the same way and nothing about editing lives in their
views. A key that is not an editing key gives ``None`` (the component does not
consume it); an editing key that changes nothing gives an equal buffer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EditBuffer"]


@dataclass(frozen=True)
class EditBuffer:
    """Text and the caret's position in it (``0`` to ``len(text)``)."""

    text: str = ""
    cursor: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", max(0, min(self.cursor, len(self.text))))

    def key(self, name: str, *, allowed: str | None = None) -> EditBuffer | None:
        """The buffer after key ``name``, or ``None`` when ``name`` does not edit.

        A printable character is inserted unless ``allowed`` is given and does
        not contain it.
        """
        before, after = self.text[: self.cursor], self.text[self.cursor :]
        match name:
            case "left":
                return EditBuffer(self.text, self.cursor - 1)
            case "right":
                return EditBuffer(self.text, self.cursor + 1)
            case "home":
                return EditBuffer(self.text, 0)
            case "end":
                return EditBuffer(self.text, len(self.text))
            case "backspace":
                return EditBuffer(before[:-1] + after, self.cursor - 1)
            case "delete":
                return EditBuffer(before + after[1:], self.cursor)
            case "ctrl-u":
                return EditBuffer()
            case "ctrl-w":
                kept = re.sub(r"\S+\s*$", "", before)
                return EditBuffer(kept + after, len(kept))
        if len(name) == 1 and name.isprintable():
            return self.paste(name, allowed=allowed)
        return None

    def paste(self, text: str, *, allowed: str | None = None) -> EditBuffer:
        """The buffer with ``text`` inserted at the caret; line breaks and controls are dropped.

        With ``allowed`` the paste is all or nothing: surrounding whitespace
        (a trailing line break) is trimmed, and when any other character is not
        in ``allowed`` the buffer is returned unchanged rather than keeping
        the characters that were.
        """
        if allowed is None:
            clean = "".join(char for char in text if char.isprintable())
        else:
            clean = text.strip()
            if any(char not in allowed for char in clean):
                return self
        before, after = self.text[: self.cursor], self.text[self.cursor :]
        return EditBuffer(before + clean + after, self.cursor + len(clean))
