"""What a type checker sees of a server API block: four wrong adapters fail at the block.

An adapter missing a port method, with no port, recording another version
type, or taking other settings fails mypy at the ``ServerApi(...)`` line
(``AdapterClass[S, V, P]``); a sound one, subclassing its port or not, passes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from repo.support import REPO_ROOT

SNIPPET = """\
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self

from pydantic import BaseModel

from untaped.sdk import key
from untaped.sdk.network import (
    ApiAdapter,
    Credential,
    CredentialKey,
    HttpClient,
    Release,
    ServerApi,
    ServerVersion,
)


class Server(CredentialKey):
    url: str = ""


class Settings(BaseModel):
    server: Server | None = None
    server_version: Version | None = None


class OtherSettings(BaseModel):
    pass


@dataclass(frozen=True)
class Version(ServerVersion):
    release: Release

    def __str__(self) -> str:
        return str(self.release)

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls(Release.parse(text))


@dataclass(frozen=True)
class OtherVersion(ServerVersion):
    def __str__(self) -> str:
        return "other"

    @classmethod
    def parse(cls, text: str) -> Self:
        return cls()


class Api(Protocol):
    def me(self) -> str: ...
    def issue(self, key: str) -> int: ...


def detect(settings: Settings, client: HttpClient) -> Version:
    return Version(Release.parse("1"))


class Good(Api, ApiAdapter[Settings, Version]):
    recorded = (Version(Release.parse("1")),)

    @classmethod
    def serves(cls, version: Version) -> bool:
        return True

    def me(self) -> str:
        return ""

    def issue(self, key: str) -> int:
        return 1


class MissesAMethod(ApiAdapter[Settings, Version]):
    recorded = (Version(Release.parse("1")),)

    @classmethod
    def serves(cls, version: Version) -> bool:
        return True

    def me(self) -> str:
        return ""


class NoPort(ApiAdapter[Settings, Version]):
    recorded = (Version(Release.parse("1")),)

    @classmethod
    def serves(cls, version: Version) -> bool:
        return True


class OtherVersionsFresh(ApiAdapter[Settings, OtherVersion]):
    recorded = (OtherVersion(),)

    @classmethod
    def serves(cls, version: OtherVersion) -> bool:
        return True

    def me(self) -> str:
        return ""

    def issue(self, key: str) -> int:
        return 1


class OtherSettingsAdapter(ApiAdapter[OtherSettings, Version]):
    recorded = (Version(Release.parse("1")),)

    @classmethod
    def serves(cls, version: Version) -> bool:
        return True

    def me(self) -> str:
        return ""

    def issue(self, key: str) -> int:
        return 1


TOKEN = Credential(key=Server)


GOOD = ServerApi[Api, Settings, Version](
    server=key(Settings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(Good,),
    override=key(Settings).server_version,
)
MISSES = ServerApi[Api, Settings, Version](
    server=key(Settings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(MissesAMethod,),
    override=key(Settings).server_version,
)
NOPORT = ServerApi[Api, Settings, Version](
    server=key(Settings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(NoPort,),
    override=key(Settings).server_version,
)
OTHERV = ServerApi[Api, Settings, Version](
    server=key(Settings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(OtherVersionsFresh,),
    override=key(Settings).server_version,
)
OTHERS = ServerApi[Api, Settings, Version](
    server=key(Settings).server,
    credential=TOKEN,
    detect=detect,
    adapters=(OtherSettingsAdapter,),
    override=key(Settings).server_version,
)

"""


def test_mypy_refuses_the_four_wrong_adapters_at_the_block(tmp_path: Path) -> None:
    source = tmp_path / "plugin.py"
    source.write_text(SNIPPET, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(source),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    errors = [line for line in proc.stdout.splitlines() if ": error:" in line]
    lines = sorted(int(line.split(":")[1]) for line in errors)
    wrong = ("MissesAMethod", "NoPort", "OtherVersionsFresh", "OtherSettingsAdapter")
    expected = [
        number
        for number, line in enumerate(SNIPPET.splitlines(), 1)
        if any(line.strip() == f"adapters=({name},)," for name in wrong)
    ]
    assert len(expected) == len(wrong)
    assert lines == expected, proc.stdout
    assert all("AdapterClass[Settings, Version, Api]" in line for line in errors), proc.stdout
