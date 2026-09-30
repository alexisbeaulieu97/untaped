"""Behavioural tests for the shared record bases in ``untaped.records``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from untaped.diagnostics import diagnostics_scope, failure_exit_code, note_failure
from untaped.errors import ConfigError, ErrorCategory, HttpStatusError, HttpTransportError
from untaped.records import CheckRecord, ErrorInfo, OutcomeRecord, TargetRecord, UtcTimestamp


class _Stamped(BaseModel):
    scanned_at: UtcTimestamp


class _CloneOutcome(OutcomeRecord, TargetRecord):
    repo: str


def test_utc_timestamp_renders_rfc3339_z_to_the_second() -> None:
    eastern = timezone(timedelta(hours=-5))
    record = _Stamped(scanned_at=datetime(2026, 1, 2, 3, 4, 5, 678, tzinfo=eastern))

    assert record.scanned_at == datetime(2026, 1, 2, 8, 4, 5, 678, tzinfo=UTC)
    assert record.model_dump(mode="json") == {"scanned_at": "2026-01-02T08:04:05Z"}


def test_utc_timestamp_takes_naive_values_and_strings_as_utc() -> None:
    naive = _Stamped(scanned_at=datetime(2026, 1, 2, 3, 4, 5))
    parsed = _Stamped.model_validate({"scanned_at": "2026-01-02T03:04:05+0000"})

    assert naive.model_dump(mode="json") == {"scanned_at": "2026-01-02T03:04:05Z"}
    assert parsed.scanned_at == naive.scanned_at


def test_outcome_and_target_bases_compose(tmp_path: Path) -> None:
    row = _CloneOutcome(action="failed", target_path=tmp_path, repo="a/b")

    assert row.failed is True
    assert row.model_dump(mode="json") == {
        "action": "failed",
        "target_path": str(tmp_path),
        "repo": "a/b",
    }
    assert _CloneOutcome(action="skipped", target_path=tmp_path, repo="a/b").failed is False


def test_target_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError, match="target_path must be absolute"):
        TargetRecord(target_path=Path("relative/dir"))


def test_records_are_frozen_and_reject_unknown_fields() -> None:
    check = CheckRecord(status="pass")
    with pytest.raises(ValidationError):
        check.status = "fail"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CheckRecord.model_validate({"status": "ok"})
    with pytest.raises(ValidationError):
        CheckRecord.model_validate({"status": "pass", "extra": 1})


class _BranchOutcome(OutcomeRecord, TargetRecord):
    repo: str
    action: str
    branch: str


def test_own_fields_come_before_inherited_base_fields(tmp_path: Path) -> None:
    row = _CloneOutcome(action="cloned", target_path=tmp_path, repo="a/b")

    assert list(row.model_dump()) == ["repo", "action", "target_path"]
    assert list(row.model_dump(mode="json")) == ["repo", "action", "target_path"]
    assert row.model_dump_json().startswith('{"repo":')


class _SyncOutcome(OutcomeRecord, TargetRecord):
    repo: str
    fetched: int
    detail: str | None = None


def test_an_inherited_action_follows_the_identifying_field(tmp_path: Path) -> None:
    row = _SyncOutcome(action="synced", target_path=tmp_path, repo="a/b", fetched=3)

    assert list(row.model_dump(mode="json")) == [
        "repo",
        "action",
        "fetched",
        "detail",
        "target_path",
    ]


def test_redeclared_base_fields_keep_the_subclass_position(tmp_path: Path) -> None:
    row = _BranchOutcome(repo="a/b", action="updated", branch="main", target_path=tmp_path)

    assert list(row.model_dump(mode="json")) == ["repo", "action", "branch", "target_path"]


def test_a_failed_row_carries_the_error_of_the_exception_that_failed_it(tmp_path: Path) -> None:
    cause = HttpStatusError("HTTP 503", status_code=503, url="https://h/x", system="awx")

    row = _CloneOutcome(
        action="failed", target_path=tmp_path, repo="a/b", error=ErrorInfo.from_exception(cause)
    )

    assert row.model_dump(mode="json")["error"] == {
        "category": "unavailable",
        "system": "awx",
        "retryable": True,
        "message": "HTTP 503 for https://h/x",
        "hint": None,
    }


def test_rows_without_an_error_omit_the_field(tmp_path: Path) -> None:
    row = _CloneOutcome(action="cloned", target_path=tmp_path, repo="a/b")

    assert "error" not in row.model_dump(mode="json")
    assert "error" not in row.model_dump()


def test_error_info_splits_the_hint_off_an_overridden_message() -> None:
    cause = ConfigError("rejected\nhint: run `untaped config set awx.token --prompt`")

    info = ErrorInfo.from_exception(cause, message="token <redacted> rejected")

    assert (info.category, info.system, info.retryable) == ("config", "local", False)
    assert info.message == "token <redacted> rejected"
    info = ErrorInfo.from_exception(cause)
    assert info.message == "rejected"
    assert info.hint == "run `untaped config set awx.token --prompt`"


def test_error_info_of_an_unexpected_exception_is_a_failed_untaped_error() -> None:
    info = ErrorInfo.from_exception(KeyError("id"))

    assert (info.category, info.system, info.message) == ("failed", "untaped", "'id'")


def test_error_info_is_pure_and_note_failure_counts_it() -> None:
    with diagnostics_scope():
        ErrorInfo.from_exception(HttpTransportError("down", system="awx"))
        assert failure_exit_code() == 1

        info = note_failure(HttpTransportError("down", system="awx"), message="redacted")

        assert (info.category, info.system, info.message) == ("unavailable", "awx", "redacted")
        assert failure_exit_code() == 5


def test_note_failure_counts_an_error_info_or_a_category() -> None:
    info = ErrorInfo.from_exception(ConfigError("rejected", category="auth"))
    with diagnostics_scope():
        assert note_failure(info) is info
        assert failure_exit_code() == 4
    with diagnostics_scope():
        assert note_failure(ErrorCategory.UNAVAILABLE) is None
        assert failure_exit_code() == 5


def test_an_interrupt_is_interrupted_everywhere() -> None:
    info = ErrorInfo.from_exception(KeyboardInterrupt())
    with diagnostics_scope():
        note_failure(KeyboardInterrupt())
        assert (info.category, failure_exit_code()) == ("interrupted", 130)
