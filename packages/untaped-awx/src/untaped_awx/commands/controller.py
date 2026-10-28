"""``untaped awx ping`` and ``untaped awx schema``: the controller's health, document schemas."""

from __future__ import annotations

import json
from typing import Final, Literal

import yaml
from pydantic import Field

from untaped.sdk import Command, Output, Positional
from untaped_awx.application import Ping as PingUseCase
from untaped_awx.commands._awx import AwxCommand
from untaped_awx.domain.ping import PingStatus
from untaped_awx.domain.suites.suite import Suite
from untaped_awx.settings import AwxSettings


class Ping(AwxCommand):
    """Check control-plane health.

    The row names the authenticated `user`, so it also checks the token.
    """

    output: Output[PingStatus] = Output()  # a default, so the doctor probe builds one bare

    def run(self) -> PingStatus:
        with self.awx() as awx:
            return PingUseCase(awx.client)()


def probe() -> str:
    """``doctor --online``: the same round trip as ``ping``, saying who the token is."""
    return f"authenticated as {Ping().run().user}"


#: The document kinds ``awx schema`` prints, by name.
DOCUMENT_MODELS: Final = {"AwxTestSuite": Suite}


class Schema(Command[AwxSettings]):
    """Print the JSON Schema of a document kind you write, for editors and agents."""

    kind: Positional[Literal["AwxTestSuite"]] = Field(description="The document kind.")
    yaml: bool = Field(False, description="Print the schema as YAML instead of JSON.")

    def run(self) -> str:
        schema = DOCUMENT_MODELS[self.kind].model_json_schema(by_alias=True)
        document = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
        if self.yaml:
            return yaml.safe_dump(document, sort_keys=False, default_flow_style=False).rstrip()
        return json.dumps(document)


__all__ = ["DOCUMENT_MODELS", "Ping", "Schema", "probe"]
