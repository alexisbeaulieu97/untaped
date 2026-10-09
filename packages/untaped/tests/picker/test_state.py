"""The picker state machine, driven key by key without a terminal."""

from __future__ import annotations

from dataclasses import replace

from untaped.picker import PickCatalog, PickItem, PickRequest, PickSetting
from untaped.picker.state import (
    ALL,
    CREATE,
    PickerState,
    begin_refresh,
    completions,
    handle,
    initial_state,
    is_inherited,
    refresh_failed,
    result,
    rows,
    setting_value,
    visible,
    with_catalog,
)

ITEMS = (
    PickItem(id="acme/api", label="acme/api", description="Core REST API"),
    PickItem(id="acme/api-gateway", label="acme/api-gateway"),
    PickItem(id="acme/web", label="acme/web"),
    PickItem(id="legacy/api", label="legacy/api", dimmed=True),
)
SETTINGS = (
    PickSetting(key="mode", label="mode", default="write", choices=("write", "read-only")),
    PickSetting(
        key="base", label="base", placeholder="default", complete=lambda _id: ["main", "release/2"]
    ),
)


def _state(**overrides: object) -> PickerState:
    request = PickRequest(heading="New workspace", catalog=PickCatalog(ITEMS), settings=SETTINGS)
    return initial_state(replace(request, **overrides))  # type: ignore[arg-type]


def press(state: PickerState, *keys: str) -> PickerState:
    for key in keys:
        state = handle(state, key)
    return state


def typed(state: PickerState, text: str) -> PickerState:
    return press(state, *text)


def test_starts_in_search_without_a_title_field() -> None:
    assert _state().focus == "search"


def test_starts_on_the_title_when_one_is_required_and_empty() -> None:
    state = _state(title_label="name")
    assert state.focus == "title"
    state = press(typed(state, "JIRA-1"), "enter")
    assert (state.title, state.focus) == ("JIRA-1", "search")


def test_typing_filters_and_resets_the_cursor() -> None:
    state = typed(_state(), "web")
    assert [r.item.id for r in visible(state)] == ["acme/web"]
    assert state.cursor == 0


def test_down_enters_the_list_and_space_toggles_the_highlighted_item() -> None:
    state = press(typed(_state(), "api"), "down", "down", " ")
    assert state.focus == "list"
    assert state.selected == ("acme/api-gateway",)
    state = press(state, " ")
    assert state.selected == ()


def test_selected_items_stay_in_the_list() -> None:
    state = press(typed(_state(), "web"), "down", " ")
    assert [r.item.id for r in visible(state)] == ["acme/web"]


def test_printable_key_in_the_list_jumps_to_search_and_types() -> None:
    state = press(_state(), "down", "w")
    assert (state.focus, state.query) == ("search", "w")


def test_slash_in_the_list_jumps_to_search_but_is_literal_in_search() -> None:
    state = press(_state(), "down", "/")
    assert (state.focus, state.query) == ("search", "")
    state = press(state, "/")
    assert state.query == "/"


def test_up_from_the_first_row_returns_to_search() -> None:
    state = press(_state(), "down", "up")
    assert state.focus == "search"


def test_esc_clears_the_query_and_never_quits() -> None:
    state = press(typed(_state(), "web"), "down", " ", "esc")
    assert (state.query, state.focus, state.outcome) == ("", "search", "running")
    state = press(state, "esc", "esc")
    assert state.outcome == "running"
    assert state.selected == ("acme/web",)


def test_ctrl_u_and_ctrl_w_edit_the_query() -> None:
    state = typed(_state(), "acme api")
    assert press(state, "ctrl-w").query == "acme "
    assert press(state, "ctrl-u").query == ""


def test_tab_switches_panes() -> None:
    state = press(_state(), "tab")
    assert (state.focus, state.row) == ("selected", (ALL, None))
    assert press(state, "tab").focus == "search"


def test_selected_pane_is_an_accordion_of_the_highlighted_owner() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab")
    assert rows(state) == [
        (ALL, None),
        (ALL, "mode"),
        (ALL, "base"),
        ("acme/web", None),
        (CREATE, None),
    ]
    state = press(state, "down", "down", "down")
    assert state.row == ("acme/web", None)
    assert rows(state) == [
        (ALL, None),
        ("acme/web", None),
        ("acme/web", "mode"),
        ("acme/web", "base"),
        (CREATE, None),
    ]


def test_left_right_cycle_a_choice_and_inheriting_values_drop_the_override() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "down", "down", "down")
    assert state.row == ("acme/web", "mode")
    assert is_inherited(state, "acme/web", "mode")
    state = press(state, "right")
    assert setting_value(state, "acme/web", "mode") == "read-only"
    assert not is_inherited(state, "acme/web", "mode")
    state = press(state, "left")
    assert is_inherited(state, "acme/web", "mode")


def test_changing_a_default_on_the_all_row_flows_to_inheriting_items() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "right")
    assert state.defaults["mode"] == "read-only"
    assert setting_value(state, "acme/web", "mode") == "read-only"


def test_enter_edits_a_text_field_and_enter_commits() -> None:
    state = press(
        typed(_state(), "web"), "down", " ", "tab", "down", "down", "down", "down", "down"
    )
    assert state.row == ("acme/web", "base")
    state = press(state, "enter")
    assert state.editing == ""
    state = press(typed(state, "release/2"), "enter")
    assert state.editing is None
    assert setting_value(state, "acme/web", "base") == "release/2"


def test_esc_abandons_an_edit() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "down", "enter")
    state = press(typed(state, "zzz"), "esc")
    assert state.editing is None
    assert state.defaults["base"] == ""


def test_tab_while_editing_completes_the_first_candidate() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "down", "enter", "r")
    assert completions(state) == ["release/2"]
    state = press(state, "tab")
    assert state.editing == "release/2"


def test_space_on_a_selected_item_header_deselects_it() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "down", "down")
    state = press(state, " ")
    assert state.selected == ()
    assert state.row == (ALL, None)


def test_ctrl_s_requires_a_selection() -> None:
    state = press(_state(), "ctrl-s")
    assert (state.outcome, state.error) == ("running", "select at least one item")
    assert press(state, "down").error == ""


def test_ctrl_s_confirms_no_selection_when_the_request_allows_it() -> None:
    state = press(_state(allow_empty=True), "ctrl-s")
    assert (state.outcome, state.error) == ("confirmed", "")
    assert result(state).picks == ()


def test_create_button_confirms_no_selection_when_the_request_allows_it() -> None:
    state = press(_state(allow_empty=True), "tab", "down", "down", "down")
    assert state.row == (CREATE, None)
    assert press(state, "enter").outcome == "confirmed"


def test_allow_empty_still_requires_the_title() -> None:
    state = press(_state(allow_empty=True, title_label="name"), "ctrl-s")
    assert (state.outcome, state.error) == ("running", "name is required")


def test_ctrl_s_requires_the_title_when_there_is_a_title_field() -> None:
    state = press(_state(title_label="name"), "down", "down", " ", "ctrl-s")
    assert (state.outcome, state.focus) == ("running", "title")
    assert state.error == "name is required"


def test_enter_on_create_confirms() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "up")
    assert state.row == (ALL, None)
    # ALL.mode, ALL.base, web header (now expanded), web.mode, web.base, Create
    state = press(state, "down", "down", "down", "down", "down", "down")
    assert state.row == (CREATE, None)
    state = press(state, "enter")
    assert state.outcome == "confirmed"


def test_ctrl_c_with_nothing_selected_cancels_at_once() -> None:
    assert press(_state(), "ctrl-c").outcome == "cancelled"


def test_ctrl_c_with_a_selection_asks_first() -> None:
    state = press(_state(), "down", " ", "ctrl-c")
    assert (state.quitting, state.outcome) == (True, "running")
    assert press(state, "n").quitting is False
    assert press(state, "y").outcome == "cancelled"
    assert press(state, "ctrl-c").outcome == "cancelled"


def test_keys_after_the_outcome_is_decided_are_ignored() -> None:
    cancelled = press(_state(), "ctrl-c")
    assert press(cancelled, "a", "down", "ctrl-s") is cancelled


def test_result_resolves_every_setting() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "right", "ctrl-s")
    picked = result(state)
    assert picked.defaults == {"mode": "read-only", "base": ""}
    assert [(p.item.id, dict(p.settings)) for p in picked.picks] == [
        ("acme/web", {"mode": "read-only", "base": ""})
    ]


def test_adhoc_item_appears_first_and_can_be_selected() -> None:
    def adhoc(query: str) -> PickItem | None:
        return PickItem(id=query, label=query) if query.startswith("git@") else None

    state = typed(_state(adhoc=adhoc), "git@h:o/r.git")
    assert visible(state)[0].item.id == "git@h:o/r.git"
    state = press(state, "down", " ")
    assert result(press(state, "ctrl-s")).picks[0].item.id == "git@h:o/r.git"


def test_adhoc_item_is_skipped_when_its_id_is_already_listed() -> None:
    state = typed(_state(adhoc=lambda q: PickItem(id="acme/web", label="dup")), "acme/web")
    assert [r.item.label for r in visible(state)] == ["acme/web"]


def test_refresh_keeps_selected_items_missing_from_the_new_catalog() -> None:
    state = press(typed(_state(), "web"), "down", " ")
    state = with_catalog(begin_refresh(state), PickCatalog((ITEMS[0],), note="just now"))
    assert (state.refreshing, state.note) == (False, "just now")
    assert [p.item.id for p in result(press(state, "ctrl-s")).picks] == ["acme/web"]


def test_refresh_failure_keeps_the_catalog_and_reports() -> None:
    state = refresh_failed(begin_refresh(_state()), "HTTP 503")
    assert state.refreshing is False
    assert state.error == "refresh failed: HTTP 503"
    assert len(state.items) == len(ITEMS)


def test_ctrl_c_while_editing_abandons_the_edit_and_asks_to_discard() -> None:
    state = press(typed(_state(), "web"), "down", " ", "tab", "down", "down", "enter")
    assert state.editing is not None
    state = press(state, "ctrl-c")
    assert (state.quitting, state.editing) == (True, None)


def test_ctrl_c_while_editing_with_nothing_selected_cancels() -> None:
    state = press(_state(), "tab", "down", "down", "enter")
    assert state.row == (ALL, "base")
    assert state.editing is not None
    assert press(state, "ctrl-c").outcome == "cancelled"


def test_ctrl_u_in_the_list_clears_the_query_and_returns_to_search() -> None:
    state = press(typed(_state(), "web"), "down")
    assert state.focus == "list"
    state = press(state, "ctrl-u")
    assert (state.query, state.focus) == ("", "search")


def test_refresh_clamps_the_cursor_to_the_new_list() -> None:
    state = press(_state(), "down", "down", "down", "down")
    assert state.cursor == 3
    state = with_catalog(state, PickCatalog(ITEMS[:2]))
    assert state.cursor == 1
    assert with_catalog(state, PickCatalog(())).cursor == 0


def test_a_failed_refresh_stays_flagged_until_a_refresh_succeeds() -> None:
    state = press(refresh_failed(begin_refresh(_state()), "HTTP 503"), "down")
    assert state.error == ""
    assert state.stale is True
    assert with_catalog(begin_refresh(state), PickCatalog(ITEMS)).stale is False


def test_validate_title_blocks_confirm_with_its_message() -> None:
    seen: list[str] = []

    def validate(title: str) -> str | None:
        seen.append(title)
        return None if title.startswith("JIRA-") else "name must start with JIRA-"

    state = press(typed(_state(title_label="name", validate_title=validate), " bad "), "down")
    state = press(state, "down", " ", "ctrl-s")
    assert seen == ["bad"]
    assert (state.outcome, state.focus) == ("running", "title")
    assert state.error == "name must start with JIRA-"
    state = press(state, "ctrl-u", *"JIRA-1", "ctrl-s")
    assert state.outcome == "confirmed"


def test_validate_title_is_not_called_without_a_title_field() -> None:
    def validate(_title: str) -> str | None:
        raise AssertionError("no title field")

    state = press(_state(validate_title=validate), "down", " ", "ctrl-s")
    assert state.outcome == "confirmed"


def test_validate_title_runs_on_the_create_button() -> None:
    state = typed(_state(title_label="name", validate_title=lambda _t: "nope"), "x")
    state = press(state, "down", "down", " ", "tab", *["down"] * 10)
    assert state.row == (CREATE, None)
    state = press(state, "enter")
    assert (state.outcome, state.error) == ("running", "nope")
