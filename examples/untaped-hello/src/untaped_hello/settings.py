"""Profile settings of the ``hello`` capability (the ``hello`` config section)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HelloSettings(BaseModel):
    """Profile-scoped hello settings."""

    model_config = ConfigDict(frozen=True)

    greeting: str = "hello from untaped-hello"
