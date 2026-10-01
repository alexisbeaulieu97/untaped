"""Drive the real prompt_toolkit picker with scripted keystrokes."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from untaped.picker import PickCatalog, PickItem, PickRequest, PickResult, PickSetting
from untaped.picker.app import run_picker

DOWN = "\x1b[B"
TAB = "\t"
ENTER = "\r"
CTRL_S = "\x13"
CTRL_C = "\x03"
CTRL_R = "\x12"

ITEMS = (
    PickItem(id="acme/api", label="acme/api"),
    PickItem(id="acme/web", label="acme/web"),
)
SETTINGS = (PickSetting(key="mode", label="mode", default="write", choices=("write", "read-only")),)


def _run(
    keys: str, request: PickRequest, *, spawn: Callable[[Callable[[], None]], None] | None = None
) -> PickResult | None:
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        pipe.close()  # a missing confirm reads EOF instead of hanging
        return run_picker(request, input=pipe, output=DummyOutput(), spawn=spawn)


def _request(**extra: object) -> PickRequest:
    return PickRequest(
        heading="New workspace", catalog=PickCatalog(ITEMS), settings=SETTINGS, **extra
    )  # type: ignore[arg-type]


def test_type_select_and_confirm() -> None:
    picked = _run("web" + DOWN + " " + CTRL_S, _request())
    assert picked is not None
    assert [p.item.id for p in picked.picks] == ["acme/web"]


def test_settings_change_in_the_selected_pane() -> None:
    keys = "web" + DOWN + " " + TAB + DOWN + "\x1b[C" + CTRL_S  # → on the all-items mode row
    picked = _run(keys, _request())
    assert picked is not None
    assert picked.defaults == {"mode": "read-only"}


def test_ctrl_c_with_nothing_selected_returns_none() -> None:
    assert _run(CTRL_C, _request()) is None


def test_ctrl_c_then_y_discards_a_selection() -> None:
    assert _run(DOWN + " " + CTRL_C + "y", _request()) is None


def test_closed_input_without_a_confirm_raises_eof() -> None:
    with pytest.raises(EOFError):
        _run("web", _request())


def test_title_field_is_typed_first() -> None:
    picked = _run("JIRA-1" + ENTER + DOWN + " " + CTRL_S, _request(title_label="name"))
    assert picked is not None
    assert picked.title == "JIRA-1"


def test_refresh_runs_at_start_and_on_ctrl_r() -> None:
    calls: list[bool] = []

    def refresh(force: bool) -> PickCatalog:
        calls.append(force)
        return PickCatalog((*ITEMS, PickItem(id="acme/new", label="acme/new")), note="just now")

    picked = _run(
        CTRL_R + "new" + DOWN + " " + CTRL_S, _request(refresh=refresh), spawn=lambda work: work()
    )
    assert calls == [False, True]
    assert picked is not None
    assert [p.item.id for p in picked.picks] == ["acme/new"]


def test_refresh_errors_keep_the_picker_usable() -> None:
    def refresh(force: bool) -> PickCatalog:
        raise RuntimeError("HTTP 503")

    picked = _run("api" + DOWN + " " + CTRL_S, _request(refresh=refresh), spawn=lambda work: work())
    assert picked is not None
    assert [p.item.id for p in picked.picks] == ["acme/api"]


def test_unbound_escape_sequences_do_not_type() -> None:
    picked = _run("\x1b[H" + "api" + DOWN + " " + CTRL_S, _request())
    assert picked is not None
    assert [p.item.id for p in picked.picks] == ["acme/api"]


def test_pasted_text_lands_in_the_search() -> None:
    picked = _run("\x1b[200~web\x1b[201~" + DOWN + " " + CTRL_S, _request())
    assert picked is not None
    assert [p.item.id for p in picked.picks] == ["acme/web"]


def test_paste_after_the_outcome_is_set_exits_once() -> None:
    assert _run(DOWN + " " + CTRL_C + "\x1b[200~yy\x1b[201~", _request()) is None
