"""Step kinds other plugins provide (design 11): ``kind: macos.defaults`` runs through them."""

from __future__ import annotations

import json
from typing import ClassVar, Literal

import pytest
from pydantic import BaseModel, SecretStr

from dotfiles_test_support import MakeRemote, as_json, installed, json_rows, run, subscribe
from untaped.sdk import Record, UntapedError
from untaped.sdk.contracts import Provider
from untaped.sdk.errors import ExitCode
from untaped.sdk.testing import ScriptedTerminal
from untaped_dotfiles.api import StepKind, StepResult

pytestmark = pytest.mark.usefixtures("dotfiles_env")


class Defaults(Record):
    """A `defaults write` key."""

    domain: str
    key: str
    type: Literal["bool", "int", "string"]
    value: bool | int | str


class Machine:
    """What the fake provider reads and writes, and every call it got."""

    keys: ClassVar[dict[str, object]] = {}
    calls: ClassVar[list[str]] = []
    raises: ClassVar[UntapedError | None] = None
    fails: ClassVar[bool] = False


class DefaultsStep(StepKind[Defaults], Provider):
    """Writes macOS `defaults` keys."""

    def check(self, step: Defaults) -> StepResult:
        Machine.calls.append(f"check {step.key}={step.value}")
        now = Machine.keys.get(f"{step.domain}/{step.key}")
        return StepResult(passes=now == step.value, detail=f"{step.key} is {now!r}")

    def run(self, step: Defaults) -> StepResult:  # takes no secret: called without one
        Machine.calls.append(f"run {step.key}={step.value}")
        if Machine.raises is not None:
            raise Machine.raises
        if not Machine.fails:
            Machine.keys[f"{step.domain}/{step.key}"] = step.value
        return StepResult(passes=not Machine.fails, detail="the key is locked")


class Token(Record):
    """A token written where a tool reads it."""

    name: str


class TokenStep(StepKind[Token], Provider):
    """Keeps a token."""

    got: ClassVar[list[str | None]] = []

    def check(self, step: Token) -> StepResult:
        return StepResult(passes=bool(TokenStep.got))

    def run(self, step: Token, secret: SecretStr | None = None) -> StepResult:
        TokenStep.got.append(None if secret is None else secret.get_secret_value())
        return StepResult(passes=True)


class Unnamed(StepKind, Provider):
    """Leaves the record at StepFields: adds no kind."""

    def check(self, step: object) -> StepResult:
        raise AssertionError("never asked")

    def run(self, step: object, secret: SecretStr | None = None) -> StepResult:
        raise AssertionError("never asked")


@pytest.fixture(autouse=True)
def _machine() -> None:
    Machine.keys, Machine.calls, Machine.raises, Machine.fails = {}, [], None, False
    TokenStep.got = []


def _package(*steps: str, inputs: str = "") -> str:
    body = "".join(f"      - {step}\n" for step in steps)
    lead = f"inputs:\n{inputs}" if inputs else ""
    return f"version: 1\n{lead}items:\n  mac:\n    steps:\n{body}"


DOCK = (
    "{name: dock, kind: macos.defaults, "
    "with: {domain: com.apple.dock, key: autohide, type: bool, value: true}}"
)


def _subscribe(make_remote: MakeRemote, package: str) -> None:
    source = make_remote(packages={"mac": package})
    subscribe(source.url, "--add", "mac")


def _apply(*args: str) -> tuple[int, dict[str, dict[str, object]]]:
    result = run(["apply", "--yes", *args, "--format", "json"])
    rows = {str(r["entry"]): r for r in json_rows(result) if r["kind"] == "step"}
    return result.exit_code, rows


def _status() -> dict[str, dict[str, object]]:
    return {str(r["entry"]): r for r in as_json(["status"])}


def test_a_kind_runs_through_the_plugin_that_provides_it(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    _subscribe(make_remote, _package(DOCK))
    assert _status()["dock"]["state"] == "pending"
    assert Machine.calls == []  # nothing runs before it is approved
    code, rows = _apply()
    assert code == 0, rows
    assert rows["dock"]["action"] == "created"
    assert Machine.calls == ["check autohide=True", "run autohide=True", "check autohide=True"]
    assert Machine.keys == {"com.apple.dock/autohide": True}
    assert _status()["dock"]["state"] == "applied"
    Machine.keys.clear()
    assert _status()["dock"]["state"] == "drifted"


def test_with_is_validated_as_the_kind_s_record(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    bad = "{name: dock, kind: macos.defaults, with: {domain: com.apple.dock, key: autohide}}"
    _subscribe(make_remote, _package(bad))
    status = run(["status", "--format", "json"])
    [row] = json_rows(status)
    assert (status.exit_code, row["state"]) == (1, "error")
    assert row["detail"] == "step 'dock': with: type: Field required"
    code, _ = _apply()
    assert code == 1 and Machine.calls == []


def test_with_renders_inputs_before_it_is_validated(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    step = (
        "{name: dock, kind: macos.defaults, "
        "with: {domain: com.apple.dock, key: '{{ key }}', type: string, value: x}}"
    )
    _subscribe(make_remote, _package(step, inputs="  key: {description: Which key}\n"))
    waiting = _status()["dock"]
    assert (waiting["state"], waiting["detail"]) == (
        "pending",
        "input 'key' has no value: run `untaped dotfiles add dotfiles/mac --var key=VALUE`",
    )
    code, rows = _apply("--var", "key=tilesize")
    assert code == 0, rows
    assert Machine.keys == {"com.apple.dock/tilesize": "x"}


def test_a_kind_nobody_provides_is_skipped_and_the_rest_applies(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    fonts = "{name: fonts, kind: fonts.family, with: {name: fira}}"
    _subscribe(make_remote, _package(fonts, DOCK))
    code, rows = _apply()
    assert code == 3
    assert rows["fonts"]["action"] == "skipped"
    assert rows["fonts"]["detail"] == (
        "kind fonts.family: no installed plugin provides it; install the plugin fonts"
    )
    assert rows["dock"]["action"] == "created"


def test_a_plugin_without_that_kind_says_which_it_has(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    other = "{name: other, kind: macos.wallpaper, with: {}}"
    _subscribe(make_remote, _package(other))
    detail = str(_status()["other"]["detail"])
    assert detail == (
        "kind macos.wallpaper: the plugin macos provides no such step kind; "
        "it provides macos.defaults"
    )


def test_a_provider_that_raises_fails_only_its_row(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    second = DOCK.replace("dock,", "dock2,").replace("autohide", "magnify")
    _subscribe(make_remote, _package(DOCK, second))
    Machine.raises = UntapedError("the defaults database is locked", category="config")
    code, rows = _apply()
    assert rows["dock"]["action"] == "failed"
    assert "the defaults database is locked" in str(rows["dock"]["detail"])
    assert rows["dock2"]["action"] == "failed"  # each row on its own
    assert code == ExitCode.ENVIRONMENT


def test_a_run_that_does_not_pass_fails_its_row(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,)})
    _subscribe(make_remote, _package(DOCK))
    Machine.fails = True
    code, rows = _apply()
    assert code == 1
    assert rows["dock"]["action"] == "failed"
    assert "the key is locked" in str(rows["dock"]["detail"])
    assert _status()["dock"]["state"] == "pending"  # never counted as passing


def test_a_secret_input_reaches_run_as_a_secret(
    make_remote: MakeRemote, terminal: ScriptedTerminal
) -> None:
    installed({"tools": (TokenStep,)})
    step = "{name: token, kind: tools.token, with: {name: gh}, stdin: token}"
    token = "  token: {description: A token, secret: true}\n"
    _subscribe(make_remote, _package(step, inputs=token))
    terminal.types("sentinel-secret").answers(True)
    try:
        done = run(["apply", "--format", "json"])
    finally:
        terminal.absent()
    assert done.exit_code == 0, done.output
    assert TokenStep.got == ["sentinel-secret"]
    assert "sentinel-secret" not in done.output


def test_a_provider_keeping_step_fields_adds_no_kind_and_doctor_names_it() -> None:
    installed({"macos": (DefaultsStep,), "tools": (Unnamed,)})
    ran = installed().cli(["doctor", "--format", "json"])
    rows = {str(row["check"]): row for row in json.loads(ran.stdout)}
    row = rows["dotfiles.step-kinds"]
    assert row["status"] == "warn"
    assert "tools's Unnamed keeps StepFields" in str(row["detail"])


def test_the_extensions_page_shows_each_kind_s_fields() -> None:
    installed({"macos": (DefaultsStep,)})
    page = installed().cli(["docs", "dotfiles", "extensions", "step-kind"]).stdout
    assert "### `macos.defaults` (macos)" in page
    assert "A `defaults write` key." in page
    assert "| `domain` |" in page and "| `value` |" in page


def test_one_plugin_provides_several_kinds(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep, TokenStep)})
    token = "{name: token, kind: macos.token, with: {name: gh}}"
    _subscribe(make_remote, _package(DOCK, token))
    code, rows = _apply()
    assert code == 0, rows
    assert (rows["dock"]["action"], rows["token"]["action"]) == ("created", "created")
    assert Machine.keys == {"com.apple.dock/autohide": True}
    assert TokenStep.got == [None]


class Again(StepKind[Defaults], Provider):
    """A second provider of one kind in one plugin: core refuses both."""

    def check(self, step: Defaults) -> StepResult:
        raise AssertionError("never asked")

    def run(self, step: Defaults) -> StepResult:
        raise AssertionError("never asked")


def test_a_refused_provider_is_reported_with_core_s_reason(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep, Again, TokenStep)})
    token = "{name: token, kind: macos.token, with: {name: gh}}"
    _subscribe(make_remote, _package(DOCK, token))
    row = _status()["dock"]
    assert row["state"] == "unmet"
    assert str(row["detail"]).startswith(
        "kind macos.defaults: untaped refused macos's provider of it (bad-provider): "
        "macos provides StepKind twice with the record Defaults"
    )
    code, rows = _apply()
    assert code == 3
    assert rows["token"]["action"] == "created"  # the plugin's other kind still runs


class HostSettings(BaseModel):
    host: str


class Host(Record):
    """A host the tool talks to."""

    name: str


class NeedsHost(StepKind[Host], Provider[HostSettings]):
    """Ready once the plugin's host is set."""

    def check(self, step: Host) -> StepResult:
        return StepResult(passes=False)

    def run(self, step: Host) -> StepResult:
        return StepResult(passes=True)


def test_a_provider_that_is_not_ready_fails_its_row_as_setup(make_remote: MakeRemote) -> None:
    installed({"remote": (NeedsHost,)})
    _subscribe(make_remote, _package("{name: host, kind: remote.host, with: {name: a}}"))
    code, rows = _apply()
    assert rows["host"]["action"] == "failed"
    assert str(rows["host"]["detail"]).startswith(
        "no provider of dotfiles.step_kind.check is ready"
    )
    assert "remote.host" in str(rows["host"]["detail"])
    assert code == ExitCode.ENVIRONMENT


class OtherDefaults(StepKind[Defaults], Provider):
    """Another plugin issuing the record macos issues: neither is asked for it."""

    def check(self, step: Defaults) -> StepResult:
        raise AssertionError("never asked")

    def run(self, step: Defaults) -> StepResult:
        raise AssertionError("never asked")


def test_a_kind_two_plugins_provide_is_asked_of_neither(make_remote: MakeRemote) -> None:
    installed({"macos": (DefaultsStep,), "tweaks": (OtherDefaults,)})
    _subscribe(make_remote, _package(DOCK))
    row = _status()["dock"]
    assert row["state"] == "unmet"
    assert row["detail"] == (
        "kind macos.defaults: macos and tweaks each provide it, so neither is asked; uninstall one"
    )
    assert Machine.calls == []
    ran = installed().cli(["doctor", "--format", "json"])
    rows = {str(each["check"]): each for each in json.loads(ran.stdout)}
    assert "macos.defaults: macos and tweaks each provide it" in str(
        rows["dotfiles.step-kinds"]["detail"]
    )


class Echoes(StepKind[Token], Provider):
    """Says the secret it got, in its detail or its error."""

    raises: ClassVar[bool] = False

    def check(self, step: Token) -> StepResult:
        return StepResult(passes=False)

    def run(self, step: Token, secret: SecretStr | None = None) -> StepResult:
        said = f"login rejected for {secret.get_secret_value() if secret else '?'}"
        if Echoes.raises:
            raise UntapedError(said, category="config")
        return StepResult(passes=False, detail=said)


@pytest.mark.parametrize("raises", [False, True])
def test_a_secret_a_provider_says_back_is_masked(
    make_remote: MakeRemote, terminal: ScriptedTerminal, raises: bool
) -> None:
    Echoes.raises = raises
    installed({"tools": (Echoes,)})
    step = "{name: token, kind: tools.token, with: {name: gh}, stdin: token}"
    token = "  token: {description: A token, secret: true}\n"
    _subscribe(make_remote, _package(step, inputs=token))
    terminal.types("sentinel-secret").answers(True)
    try:
        done = run(["apply", "--format", "json"])
    finally:
        terminal.absent()
    assert done.exit_code != 0
    assert "login rejected for ***" in done.output
    assert "sentinel-secret" not in done.output
