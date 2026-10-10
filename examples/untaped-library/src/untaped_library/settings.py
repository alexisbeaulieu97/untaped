"""The ``library`` config section: the volumes the library keeps."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class VolumeEntry(BaseModel):
    """One volume as the config file lists it."""

    model_config = ConfigDict(frozen=True)

    shelf_mark: str
    name: str
    pages: int = 0


class LibrarySettings(BaseModel):
    """``library.volumes``: the volumes this library lends."""

    model_config = ConfigDict(frozen=True)

    volumes: list[VolumeEntry] = []
