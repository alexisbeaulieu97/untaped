"""The ``terminal`` and ``credentials`` fixtures: what ``install`` asks and stores against."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import BaseModel, Field

from untaped.sdk import Command, Confirm, Credential, CredentialKey, Plugin, Positional
from untaped.sdk.testing import Install, MemoryCredentials, ScriptedTerminal

Installer = Callable[..., Install]


class VaultSettings(BaseModel):
    """Nothing to configure."""


class VaultServer(CredentialKey):
    name: str = Field("vault", description="Which vault.")


class Wipe(Command):
    """Wipe the vault, asking first."""

    confirm: Confirm

    def run(self) -> str:
        return "wiped" if self.confirm("Wipe the vault?") else "planned"


class Label(Command):
    """Ask for a label and print it."""

    def run(self) -> str:
        return self.ctx.terminal.text("Label?")


class Peek(Command):
    """Print the vault's token."""

    name: Positional[str] = Field("vault", description="Which vault.")

    def run(self) -> str:
        return Vault.token.get(VaultServer(name=self.name)).get_secret_value()


class Vault(Plugin):
    """Keep secrets (a test's plugin)."""

    settings = VaultSettings
    commands = (Wipe, Label, Peek)
    token = Credential(key=VaultServer, env=("VAULT_TOKEN",))


def test_the_terminal_is_absent_until_scripted(
    install: Installer, terminal: ScriptedTerminal
) -> None:
    vault = install(Vault)
    assert vault.cli("vault wipe").exit == 2
    terminal.answers(yes=True)
    assert vault.cli("vault wipe").text == "wiped"
    assert terminal.asked == [("confirm", "Wipe the vault?")]
    terminal.absent()
    assert vault.cli("vault wipe").exit == 2


def test_the_terminal_answers_in_order(install: Installer, terminal: ScriptedTerminal) -> None:
    vault = install(Vault)
    terminal.answers(True, False)
    assert vault.cli("vault wipe").text == "wiped"
    assert vault.cli("vault wipe").exit == 1  # declined


def test_the_terminal_types_what_a_prompt_reads(
    install: Installer, terminal: ScriptedTerminal
) -> None:
    vault = install(Vault)
    assert vault.run(Label()).exit != 0  # nobody to ask
    terminal.types("fragile")
    assert vault.run(Label()).text == "fragile"
    assert terminal.asked == [("text", "Label?")]


def test_a_prompt_with_nothing_scripted_fails_instead_of_waiting(
    install: Installer, terminal: ScriptedTerminal
) -> None:
    terminal.answers(yes=True)
    ran = install(Vault).run(Label())
    assert ran.exit != 0
    assert any("no scripted text answer" in line for line in ran.stderr)


def test_credentials_hold_what_a_credential_reads(
    install: Installer, credentials: MemoryCredentials
) -> None:
    vault = install(Vault)
    credentials.set(Vault.token, VaultServer(), "s3cret")
    assert vault.run(Peek()).text == "s3cret"
    assert len(credentials.asked) == 1
    missing = vault.run(Peek(name="other"))
    assert missing.exit == 4
    assert len(credentials.asked) == 1


def test_auth_login_lands_in_the_test_s_store(
    install: Installer, credentials: MemoryCredentials
) -> None:
    vault = install(Vault)
    stored = vault.cli("auth login vault", input="typed\n")
    assert stored.exit == 0, stored.stderr
    assert list(credentials.entries.values()) == ["typed"]
    assert vault.run(Peek()).text == "typed"


@pytest.mark.parametrize("given", [(), (True,)])
def test_answers_make_the_terminal_present(given: tuple[bool, ...]) -> None:
    terminal = ScriptedTerminal()
    assert terminal.present is False
    assert terminal.answers(*given).present is True


def test_setting_a_credential_needs_its_plugin_installed(credentials: MemoryCredentials) -> None:
    with pytest.raises(RuntimeError, match="install the credential's plugin"):
        credentials.set(Vault.token, VaultServer(), "s3cret")
