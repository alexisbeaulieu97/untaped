"""The stderr kinds: ``Failure``'s fill and agreement, plain lines, and ``StaleData``'s wording."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped.runtime.run import entered, start_run
from untaped.runtime.sink import (
    Diagnostics,
    current_sink,
    exit_on_counted_failure,
)
from untaped.sdk.diagnostic import Failure, StaleData
from untaped.sdk.errors import ConfigError, ErrorCategory, UntapedError
from untaped.sdk.records import OutcomeRecord

_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class _Row(OutcomeRecord, kind="acme-tools.row_outcome"):
    name: str


def test_a_failure_nested_in_a_row_round_trips() -> None:
    error = Failure.from_exception(ConfigError("x", details={"status": 401}), message="redacted")
    row = _Row(name="a", action="failed", error=error)

    assert _Row.model_validate_json(row.model_dump_json()) == row


def test_details_hold_json_values_only() -> None:
    with pytest.raises(ValidationError):
        Failure(message="m", category="failed", system="x", details={"at": _NOW})  # type: ignore[dict-item]


def test_from_exception_coerces_details_it_cannot_carry() -> None:
    error = UntapedError("m", details={"at": _NOW, "where": Path("/tmp/x"), "n": [1, "2"]})

    failure = Failure.from_exception(error)

    assert failure.details == {"at": "2026-10-10T12:00:00Z", "where": "/tmp/x", "n": [1, "2"]}


def test_retryable_and_exit_code_follow_the_category() -> None:
    failure = Failure(message="m", category="unavailable", system="awx")

    assert (failure.retryable, failure.exit_code) == (True, 5)


@pytest.mark.parametrize(
    "fields", [{"retryable": True}, {"exit_code": 4}], ids=["retryable", "exit_code"]
)
def test_a_retryable_or_exit_code_disagreeing_with_the_category_is_rejected(
    fields: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="disagrees with category"):
        Failure.model_validate({"message": "m", "category": "failed", "system": "x", **fields})


def test_an_older_error_without_exit_code_or_details_still_validates() -> None:
    older = {"category": "auth", "system": "awx", "retryable": False, "message": "m", "hint": None}

    failure = Failure.model_validate(older)

    assert (failure.exit_code, failure.details) == (4, {})


def test_a_plain_failure_is_failed_and_untaped() -> None:
    failure = Failure.plain("it broke", hint="try again")

    assert (failure.category, failure.system, failure.exit_code, failure.hint) == (
        ErrorCategory.FAILED,
        "untaped",
        1,
        "try again",
    )


# ---- StaleData -----------------------------------------------------------------


def _stale(**overrides: object) -> StaleData:
    fields: dict[str, object] = {
        "plugin": "hello",
        "subject": "shelf",
        "cache": "hello.shelf_books",
        "key": "hello:ab12",
        "refreshed_at": _NOW - timedelta(hours=26),
        "now": _NOW,
        "max_age": timedelta(hours=1),
    }
    return StaleData.describe(**(fields | overrides))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "text"),
    [
        (
            {"error": Failure.from_exception(ConfigError("token rejected", category="auth"))},
            "shelf: refresh failed (token rejected); "
            "using data from 2026-10-09 10:00 UTC, 26 h old",
        ),
        (
            {"error": Failure.from_exception(KeyboardInterrupt())},
            "shelf: refresh interrupted; using data from 2026-10-09 10:00 UTC, 26 h old",
        ),
        (
            {
                "error": Failure.from_exception(ConfigError("2 of 5 pages failed")),
                "partial": True,
            },
            "shelf: 2 of 5 pages failed; using data from 2026-10-09 10:00 UTC",
        ),
        ({}, "shelf: cached data from 2026-10-09 10:00 UTC, 26 h old (--cached)"),
        (
            {"outdated": True, "cached": True},
            "shelf: made by another version of the hello plugin; "
            "using data from 2026-10-09 10:00 UTC, 26 h old (--cached)",
        ),
        (
            {"outdated": True},
            "shelf: made by another version of the hello plugin; "
            "using data from 2026-10-09 10:00 UTC, 26 h old",
        ),
        (
            {
                "error": Failure.from_exception(ConfigError("token rejected", category="auth")),
                "outdated": True,
            },
            "shelf: refresh failed (token rejected); "
            "using data from 2026-10-09 10:00 UTC, 26 h old",
        ),
    ],
    ids=[
        "failed",
        "interrupted",
        "partial",
        "cached",
        "outdated",
        "outdated-auto",
        "outdated-failed",
    ],
)
def test_stale_data_wording(overrides: dict[str, object], text: str) -> None:
    stream = io.StringIO()
    with redirect_stderr(stream), entered(start_run()):
        current_sink().emit(_stale(**overrides))

    assert stream.getvalue() == f"warning: {text}\n"


def test_stale_data_is_a_json_warning_and_counts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    stream = io.StringIO()
    error = Failure.from_exception(ConfigError("token rejected", category="auth"))
    with redirect_stderr(stream), entered(start_run()):
        Diagnostics().emit(_stale(error=error, attempted_at=_NOW))
        exit_on_counted_failure()  # a stale answer is not a failure: no SystemExit

    line = json.loads(stream.getvalue())
    assert (line["level"], line["kind"], line["cache"], line["key"]) == (
        "warning",
        "untaped.stale",
        "hello.shelf_books",
        "hello:ab12",
    )
    assert (line["age_seconds"], line["max_age_seconds"]) == (93_600.0, 3_600)
    assert (line["refreshed_at"], line["attempted_at"]) == (
        "2026-10-09T10:00:00Z",
        "2026-10-10T12:00:00Z",
    )
    assert line["error"]["category"] == "auth"
    assert line["outdated"] is False


def test_an_outdated_stale_line_says_so_in_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", "json")
    stream = io.StringIO()
    with redirect_stderr(stream), entered(start_run()):
        Diagnostics().emit(_stale(outdated=True))

    line = json.loads(stream.getvalue())
    assert (line["outdated"], line["error"], line["plugin"]) == (True, None, "hello")
