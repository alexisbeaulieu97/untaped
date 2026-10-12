"""Profile settings of the ``hello`` plugin (the ``hello`` config section), and its cache."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from untaped.sdk import Cache

#: The message of the day, kept from ``hello.motd_path`` (hello's stand-in upstream).
MOTD = Cache("hello", "motd", label="message of the day", max_age=timedelta(hours=1))


class HelloSettings(BaseModel):
    """Profile-scoped hello settings."""

    model_config = ConfigDict(frozen=True)

    greeting: str = "hello from untaped-hello"
    motd_path: Path | None = None
