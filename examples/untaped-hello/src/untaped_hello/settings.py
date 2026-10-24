"""Profile settings of the ``hello`` plugin (the ``hello`` config section), and its cache."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Cache

#: The message of the day, kept from ``hello.motd_path`` (hello's stand-in upstream).
MOTD = Cache(label="message of the day", max_age=timedelta(hours=1))


class HelloSettings(BaseModel):
    """Profile-scoped hello settings."""

    model_config = ConfigDict(frozen=True)

    greeting: str = Field(
        "hello from untaped-hello", description="What `untaped hello greet` prints."
    )
    motd_path: Path | None = Field(
        None, description="The file the message of the day is kept from."
    )
