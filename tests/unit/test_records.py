"""Behavioural tests for the shared record bases in ``untaped.records``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from untaped.records import CheckRecord, OutcomeRecord, TargetRecord, UtcTimestamp


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
