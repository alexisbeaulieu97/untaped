"""The values plugin's settings section: empty, since every value comes from the command."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ValuesSettings(BaseModel):
    """The values plugin has no settings: values come from flags, files and answers."""

    model_config = ConfigDict(frozen=True)
