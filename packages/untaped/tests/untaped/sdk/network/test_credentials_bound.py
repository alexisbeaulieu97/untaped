"""A plugin class's credential: named by its attribute, its plugin read from the run."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from core_test_support.runtime.run import run_owning
from untaped.sdk import Credential, CredentialKey, Plugin
from untaped.sdk._run import run_scope
from untaped.sdk.network.credentials import NoCredential


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
        assert Demo.api_token.login == "auth login demo api_token"
        assert Demo.api_token.login_args == "demo api_token"
    assert repr(Demo.api_token) == "Credential(key=DemoServer)"
    assert Demo.api_token.module == __name__


def test_a_missing_credential_s_hint_runs_its_login(tmp_path: Path) -> None:
    run = run_owning(tmp_path, __name__.partition(".")[0], "demo")
    with run_scope(run), pytest.raises(NoCredential) as missing:
        Demo.api_token.get(DemoServer())
    assert missing.value.hint == "run `untaped auth login demo api_token`, or set DEMO_TOKEN"
