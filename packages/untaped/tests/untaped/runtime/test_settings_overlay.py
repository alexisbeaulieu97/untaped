"""A settings overlay: candidate values a child run lays over the loaded config.

``setup`` checks a plugin against what the user typed before anything is
written. The overlay is the internal seam for that: a child run carries it
(``child_run(overlay=...)``), the settings loader reads it, ``get_settings``
bypasses its cache under it, and nothing outside the child run (the main
thread, a fresh thread, the config file, the keychain) ever sees the
candidate values.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from untaped.runtime.run import child_run
from untaped.runtime.settings import (
    apply_overlay,
    get_settings,
    load_settings_section,
    register_profile_settings,
    resolve_with_overlay,
)
from untaped.sdk.errors import ConfigError
from untaped.sdk.network.auth import TokenSources, clear_token_cache, resolve_token
from untaped.sdk.run import Run, SettingsOverlay, current_overlay, current_run

pytestmark = pytest.mark.usefixtures("home_run")


class OverlayProfile(BaseModel):
    """Service double with a token and a token command (section ``ovl``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("OVL_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: list[str] | None = None


@pytest.fixture(autouse=True)
def _register() -> None:
    register_profile_settings("ovl", OverlayProfile)


def _overlaid(
    profile: str, section: str, values: Mapping[str, object], *, pick: str | None = None
) -> AbstractContextManager[Run]:
    """A child run laying ``values`` over ``profile``'s ``section`` (and picking ``pick``)."""
    return child_run(profiles=pick, overlay=SettingsOverlay(profile, section, values))


def _config(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()


def _in_thread[T](fn: Callable[[], T], *, copy: bool = True) -> T:
    """Run ``fn`` on another thread, in a copy of this context (or a fresh one)."""
    box: list[T] = []
    errors: list[BaseException] = []
    context = contextvars.copy_context() if copy else contextvars.Context()

    def target() -> None:
        try:
            box.append(context.run(fn))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return box[0]


def test_overlay_values_are_visible_to_load_settings_section(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "format_version: 2\novl:\n  base_url: https://old\n  token: file-token\n",
    )

    candidate = {"base_url": "https://new", "token": SecretStr("typed")}
    with _overlaid("default", "ovl", candidate):
        section = load_settings_section("ovl")

    assert section.base_url == "https://new"
    assert section.token is not None
    assert section.token.get_secret_value() == "typed"
    assert load_settings_section("ovl").base_url == "https://old"


def test_overlay_for_another_profile_is_ignored(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "format_version: 2\novl:\n  base_url: https://old\nprofiles:\n  prod: {}\n",
    )

    with _overlaid("prod", "ovl", {"base_url": "https://new"}):
        assert load_settings_section("ovl").base_url == "https://old"
        with child_run(profiles="prod"):  # a child of the child keeps its overlay
            assert load_settings_section("ovl").base_url == "https://new"


def test_overlay_none_unsets_a_key(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "format_version: 2\novl:\n  base_url: https://old\n  token: file-token\n",
    )

    with _overlaid("default", "ovl", {"token": None}):
        section = load_settings_section("ovl")

    assert section.token is None
    assert section.base_url == "https://old"


def test_overlay_reaches_a_profile_that_is_not_written_yet(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "format_version: 2\novl:\n  base_url: https://old\n",
    )

    typed = {"token": SecretStr("t")}
    with _overlaid("brand-new", "ovl", typed, pick="brand-new"):
        section = load_settings_section("ovl")

    # It starts from default's values, as the profile will once it is created.
    assert section.base_url == "https://old"
    assert section.token is not None
    with pytest.raises(ConfigError, match="brand-new"), child_run(profiles="brand-new"):
        load_settings_section("ovl")


@pytest.mark.parametrize("profiles", ["", "format_version: 2\nprofiles: {}\n"])
def test_overlay_reaches_a_new_profile_in_a_config_without_profiles(
    _isolated_config: Path, profiles: str
) -> None:
    _config(_isolated_config, profiles or "{}\n")

    typed = {"token": SecretStr("t")}
    with _overlaid("brand-new", "ovl", typed, pick="brand-new"):
        section = load_settings_section("ovl")

    assert section.token is not None


def test_overlay_never_changes_get_settings_on_the_main_thread(_isolated_config: Path) -> None:
    _config(_isolated_config, "format_version: 2\novl:\n  base_url: https://old\n")
    before = get_settings()
    assert before.ovl.base_url == "https://old"  # type: ignore[attr-defined]

    def candidate() -> str | None:
        with _overlaid("default", "ovl", {"base_url": "https://candidate"}):
            return get_settings().ovl.base_url  # type: ignore[attr-defined, no-any-return]

    assert _in_thread(candidate) == "https://candidate"

    after = get_settings()
    assert after.ovl.base_url == "https://old"  # type: ignore[attr-defined]
    # The cache held the file's values throughout: the same object came back.
    assert after is before


def test_the_cache_is_never_filled_with_candidate_values(_isolated_config: Path) -> None:
    _config(_isolated_config, "format_version: 2\novl:\n  base_url: https://old\n")
    get_settings.cache_clear()

    with _overlaid("default", "ovl", {"base_url": "https://candidate"}):
        assert get_settings().ovl.base_url == "https://candidate"  # type: ignore[attr-defined]
        assert get_settings.cache_info().currsize == 0

    assert get_settings().ovl.base_url == "https://old"  # type: ignore[attr-defined]


def test_get_settings_cache_still_works_without_an_overlay(_isolated_config: Path) -> None:
    _config(_isolated_config, "format_version: 2\novl:\n  base_url: https://old\n")

    assert get_settings() is get_settings()
    assert get_settings.cache_info().hits >= 1
    get_settings.cache_clear()
    assert get_settings.cache_info().currsize == 0


def test_overlay_does_not_write_config_or_keychain(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core_test_support.commands.stores import install_fake_stores

    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    _config(_isolated_config, "format_version: 2\novl:\n  base_url: https://old\n")
    config_bytes = _isolated_config.read_bytes()

    with _overlaid(
        "default",
        "ovl",
        {"base_url": "https://new", "token": SecretStr("typed"), "token_command": ["pass", "x"]},
    ):
        load_settings_section("ovl")
        get_settings()

    assert _isolated_config.read_bytes() == config_bytes
    assert stores.entries() == {}
    assert stores.calls() == []


def test_overlay_is_visible_in_a_copied_context_thread_and_not_a_fresh_one(
    _isolated_config: Path,
) -> None:
    _config(_isolated_config, "format_version: 2\novl:\n  base_url: https://old\n")

    with _overlaid("default", "ovl", {"base_url": "https://new"}):
        copied = _in_thread(lambda: load_settings_section("ovl").base_url)
        fresh = _in_thread(lambda: load_settings_section("ovl").base_url, copy=False)

    assert (copied, fresh) == ("https://new", "https://old")


def test_the_overlay_ends_with_its_block(_isolated_config: Path) -> None:
    assert current_overlay() is None

    with _overlaid("default", "ovl", {"base_url": "x"}) as child:
        assert current_run() is child
        assert current_overlay() == SettingsOverlay("default", "ovl", {"base_url": "x"})

    assert current_overlay() is None


def test_apply_overlay_copies_and_unwraps_secrets() -> None:
    effective: dict[str, Any] = {"ovl": {"base_url": "a", "token": "t"}, "other": {"k": 1}}

    with _overlaid("p", "ovl", {"token": SecretStr("typed"), "token_command": None}):
        same = apply_overlay(effective, profile="elsewhere")
        applied = apply_overlay(effective, profile="p")

    assert same is effective
    assert applied == {"ovl": {"base_url": "a", "token": "typed"}, "other": {"k": 1}}
    assert effective["ovl"]["token"] == "t"  # the input was not mutated


def test_resolve_with_overlay_is_plain_resolution_without_an_overlay(
    _isolated_config: Path,
) -> None:
    _config(_isolated_config, "format_version: 2\novl:\n  base_url: https://old\n")

    resolved = resolve_with_overlay({"format_version": 2, "ovl": {"base_url": "u"}})

    assert resolved.effective == {"ovl": {"base_url": "u"}}


def test_plaintext_warning_is_suppressed_under_an_overlay(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _config(
        _isolated_config,
        "format_version: 2\novl:\n  token: file-token\n",
    )
    clear_token_cache()
    section = load_settings_section("ovl")

    with _overlaid("default", "ovl", {"token": SecretStr("typed")}):
        resolve_token(section, section="ovl")

    assert capsys.readouterr().err == ""

    resolve_token(section, section="ovl")  # the warning still fires without the overlay

    assert "support for a plain-text ovl.token" in capsys.readouterr().err
