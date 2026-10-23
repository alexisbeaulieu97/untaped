"""A plugin class's credential: named by its attribute, its plugin read from the run."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import Credential, CredentialKey, Plugin
from untaped.sdk.run import run_scope


class DemoServer(CredentialKey):
    name: str = "demo"


class DemoSettings(BaseModel):
    pass


class Demo(Plugin):
    """Demo."""

    settings = DemoSettings
    api_token = Credential(key=DemoServer, env=("DEMO_TOKEN",))


def test_a_plugin_classs_credential_takes_its_attribute_and_plugin(tmp_path: Path) -> None:
    with run_scope(run_owning(tmp_path, __name__.partition(".")[0], "demo")):
        assert Demo.api_token.qualified == "demo.api_token"
        assert Demo.api_token.label == "demo api token"
    assert repr(Demo.api_token) == "Credential(key=DemoServer)"
    assert Demo.api_token.module == __name__


def test_a_credential_takes_its_plugin_and_name_together() -> None:
    with pytest.raises(TypeError, match="its plugin and its name together"):
        Credential("demo", key=DemoServer)
    with pytest.raises(TypeError, match="lower case letters"):
        Credential("demo", "Token", key=DemoServer)
