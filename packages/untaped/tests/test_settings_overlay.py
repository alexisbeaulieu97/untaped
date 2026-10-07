"""``settings_overlay``: candidate values layered over the loaded config, in one context.

``setup`` checks a capability against what the user typed before anything is
written. The overlay is the internal seam for that: the settings loader reads
it, ``get_settings`` bypasses its cache while it is set, and nothing outside
the context that set it (the main thread, a fresh thread, the config file, the
keychain) ever sees the candidate values.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from untaped import auth
from untaped.auth import TokenSources, clear_token_cache, resolve_token
from untaped.errors import ConfigError
from untaped.profile_resolver import profile_scope
from untaped.settings import (
    SettingsOverlay,
    active_overlay,
    apply_overlay,
    get_settings,
    load_settings_section,
    register_profile_settings,
    resolve_with_overlay,
    settings_overlay,
)


class OverlayProfile(BaseModel):
    """Service double with a token and a token command (section ``ovl``)."""

    token_sources: ClassVar[TokenSources] = TokenSources(env=("OVL_TOKEN",))

    base_url: str | None = None
    token: SecretStr | None = None
    token_command: list[str] | None = None


@pytest.fixture(autouse=True)
def _register() -> None:
    register_profile_settings("ovl", OverlayProfile)


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
        "profiles:\n  default:\n    ovl:\n      base_url: https://old\n      token: file-token\n",
    )

    candidate = {"base_url": "https://new", "token": SecretStr("typed")}
    with settings_overlay("default", "ovl", candidate):
        section = load_settings_section("ovl")

    assert section.base_url == "https://new"
    assert section.token is not None
    assert section.token.get_secret_value() == "typed"
    assert load_settings_section("ovl").base_url == "https://old"


def test_overlay_for_another_profile_is_ignored(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "profiles:\n  default:\n    ovl:\n      base_url: https://old\n  prod: {}\n",
    )

    with settings_overlay("prod", "ovl", {"base_url": "https://new"}):
        assert load_settings_section("ovl").base_url == "https://old"
        with profile_scope("prod"):
            assert load_settings_section("ovl").base_url == "https://new"


def test_overlay_none_unsets_a_key(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "profiles:\n  default:\n    ovl:\n      base_url: https://old\n      token: file-token\n",
    )

    with settings_overlay("default", "ovl", {"token": None}):
        section = load_settings_section("ovl")

    assert section.token is None
    assert section.base_url == "https://old"


def test_overlay_reaches_a_profile_that_is_not_written_yet(_isolated_config: Path) -> None:
    _config(
        _isolated_config,
        "profiles:\n  default:\n    ovl:\n      base_url: https://old\n",
    )

    typed = {"token": SecretStr("t")}
    with profile_scope("brand-new"), settings_overlay("brand-new", "ovl", typed):
        section = load_settings_section("ovl")

    # It starts from default's values, as the profile will once it is created.
    assert section.base_url == "https://old"
    assert section.token is not None
    with pytest.raises(ConfigError, match="brand-new"), profile_scope("brand-new"):
        load_settings_section("ovl")


def test_overlay_never_changes_get_settings_on_the_main_thread(_isolated_config: Path) -> None:
    _config(_isolated_config, "profiles:\n  default:\n    ovl:\n      base_url: https://old\n")
    before = get_settings()
    assert before.ovl.base_url == "https://old"  # type: ignore[attr-defined]

    def candidate() -> str | None:
        with settings_overlay("default", "ovl", {"base_url": "https://candidate"}):
            return get_settings().ovl.base_url  # type: ignore[attr-defined, no-any-return]

    assert _in_thread(candidate) == "https://candidate"

    after = get_settings()
    assert after.ovl.base_url == "https://old"  # type: ignore[attr-defined]
    # The cache held the file's values throughout: the same object came back.
    assert after is before


def test_the_cache_is_never_filled_with_candidate_values(_isolated_config: Path) -> None:
    _config(_isolated_config, "profiles:\n  default:\n    ovl:\n      base_url: https://old\n")
    get_settings.cache_clear()

    with settings_overlay("default", "ovl", {"base_url": "https://candidate"}):
        assert get_settings().ovl.base_url == "https://candidate"  # type: ignore[attr-defined]
        assert get_settings.cache_info().currsize == 0

    assert get_settings().ovl.base_url == "https://old"  # type: ignore[attr-defined]


def test_get_settings_cache_still_works_without_an_overlay(_isolated_config: Path) -> None:
    _config(_isolated_config, "profiles:\n  default:\n    ovl:\n      base_url: https://old\n")

    assert get_settings() is get_settings()
    assert get_settings.cache_info().hits >= 1
    get_settings.cache_clear()
    assert get_settings.cache_info().currsize == 0


def test_overlay_does_not_write_config_or_keychain(
    _isolated_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_management.stores import install_fake_stores

    stores = install_fake_stores(tmp_path, monkeypatch, "pass")
    _config(_isolated_config, "profiles:\n  default:\n    ovl:\n      base_url: https://old\n")
    config_bytes = _isolated_config.read_bytes()

    with settings_overlay(
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
    _config(_isolated_config, "profiles:\n  default:\n    ovl:\n      base_url: https://old\n")

    with settings_overlay("default", "ovl", {"base_url": "https://new"}):
        copied = _in_thread(lambda: load_settings_section("ovl").base_url)
        fresh = _in_thread(lambda: load_settings_section("ovl").base_url, copy=False)

    assert (copied, fresh) == ("https://new", "https://old")


def test_the_overlay_ends_with_its_block(_isolated_config: Path) -> None:
    assert active_overlay() is None

    with settings_overlay("default", "ovl", {"base_url": "x"}) as overlay:
        assert active_overlay() is overlay
        assert overlay == SettingsOverlay("default", "ovl", {"base_url": "x"})

    assert active_overlay() is None


def test_apply_overlay_copies_and_unwraps_secrets() -> None:
    effective: dict[str, Any] = {"ovl": {"base_url": "a", "token": "t"}, "other": {"k": 1}}

    with settings_overlay("p", "ovl", {"token": SecretStr("typed"), "token_command": None}):
        same = apply_overlay(effective, profile="elsewhere")
        applied = apply_overlay(effective, profile="p")

    assert same is effective
    assert applied == {"ovl": {"base_url": "a", "token": "typed"}, "other": {"k": 1}}
    assert effective["ovl"]["token"] == "t"  # the input was not mutated


def test_resolve_with_overlay_is_plain_resolution_without_an_overlay(
    _isolated_config: Path,
) -> None:
    _config(_isolated_config, "profiles:\n  default:\n    ovl:\n      base_url: https://old\n")

    resolved = resolve_with_overlay({"profiles": {"default": {"ovl": {"base_url": "u"}}}})

    assert resolved.effective == {"ovl": {"base_url": "u"}}


def test_plaintext_warning_is_suppressed_under_an_overlay(
    _isolated_config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _config(
        _isolated_config,
        "profiles:\n  default:\n    ovl:\n      token: file-token\n",
    )
    clear_token_cache()
    section = load_settings_section("ovl")

    with settings_overlay("default", "ovl", {"token": SecretStr("typed")}):
        resolve_token(section, section="ovl")

    assert capsys.readouterr().err == ""
    assert auth._warned == set()

    resolve_token(section, section="ovl")  # the warning still fires without the overlay

    assert "ovl.token is stored in plain text" in capsys.readouterr().err
