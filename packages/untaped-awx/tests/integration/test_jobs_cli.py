"""End-to-end CLI tests for the upgraded ``untaped awx jobs`` UX."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner
from untaped_awx import app

pytestmark = pytest.mark.integration


def _seed_running_job(fake: Any, *, job_id: int = 42) -> None:
    fake.seed(
        "jobs",
        id=job_id,
        name="deploy",
        status="successful",
        started="2026-01-01T00:00:00Z",
        finished="2026-01-01T00:01:00Z",
        stdout="line-0\nline-1\nline-2\n",
    )


def _seed_events(fake: Any, *, job_id: int = 42) -> None:
    fake.seed(
        "job_events",
        id=1,
        job=job_id,
        counter=1,
        event="playbook_on_play_start",
        play="Deploy",
    )
    fake.seed(
        "job_events",
        id=2,
        job=job_id,
        counter=2,
        event="playbook_on_task_start",
        task="install",
    )
    fake.seed(
        "job_events",
        id=3,
        job=job_id,
        counter=3,
        event="runner_on_ok",
        host=5,
        host_name="web-01",
        task="install",
    )
    fake.seed(
        "job_events",
        id=4,
        job=job_id,
        counter=4,
        event="runner_on_failed",
        host=6,
        host_name="api-01",
        task="install",
        failed=True,
    )


def test_jobs_list_returns_seeded_records(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliRunner().invoke(app, ["jobs", "list", "--format", "raw", "--columns", "id"])
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines())
    assert ids == ["42", "43"]


def test_jobs_list_status_filter_passes_to_awx(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=1, name="ok", status="successful")
    fake_aap.seed("jobs", id=2, name="bad", status="failed")
    result = CliRunner().invoke(
        app, ["jobs", "list", "--status", "failed", "--format", "raw", "--columns", "id"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "2"


def test_jobs_events_drains_existing(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app, ["jobs", "events", "42", "--format", "raw", "--columns", "counter"]
    )
    assert result.exit_code == 0, result.output
    counters = sorted(result.stdout.strip().splitlines())
    assert counters == ["1", "2", "3", "4"]


def test_jobs_events_follow_streams_events_to_stdout(fake_aap: Any) -> None:
    """Regression: ``--follow`` used to build the full row list before
    printing, so nothing appeared until the job hit terminal. The CLI
    now emits each event as it's yielded.

    Also pins the NDJSON contract: ``--follow --format json`` emits one
    bare JSON object per line so ``jq`` can ingest directly without
    ``jq -s '.[]'`` (matches ``kubectl get -w -o json``).
    """
    import json as _json

    _seed_running_job(fake_aap)  # already terminal — drain loop returns
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app,
        ["jobs", "events", "42", "--follow", "--format", "json", "--columns", "counter"],
    )
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.strip().splitlines() if line]
    parsed = [_json.loads(line) for line in lines]
    # NDJSON: each line is a bare JSON object, NOT a single-element array.
    assert all(isinstance(p, dict) for p in parsed), parsed
    assert [p["counter"] for p in parsed] == [1, 2, 3, 4]


def test_jobs_events_follow_with_table_format_renders_human_lines(fake_aap: Any) -> None:
    """Table mode under ``--follow`` streams one colored human-readable
    line per event (PLAY/TASK/ok/changed/failed), via Rich Console — ANSI
    on a TTY, plain text under ``CliRunner`` (which doesn't simulate one).
    """
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "events", "42", "--follow"])
    assert result.exit_code == 0, result.output
    # ``CliRunner`` has no TTY, so colour is stripped — but the rendered
    # shape (PLAY/TASK/ok/failed) must still appear.
    out = result.stdout
    assert "PLAY [Deploy]" in out
    assert "TASK [install]" in out
    assert "ok: web-01" in out
    assert "failed: api-01" in out


def test_jobs_events_server_side_filter(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "jobs",
            "events",
            "42",
            "--filter",
            "event=runner_on_failed",
            "--format",
            "raw",
            "--columns",
            "host_name",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "api-01"


def test_jobs_events_from_counter_skips_already_seen(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliRunner().invoke(
        app,
        [
            "jobs",
            "events",
            "42",
            "--from-counter",
            "2",
            "--format",
            "raw",
            "--columns",
            "counter",
        ],
    )
    assert result.exit_code == 0, result.output
    counters = sorted(result.stdout.strip().splitlines())
    assert counters == ["3", "4"]


def test_jobs_logs_prints_full_stdout_by_default(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "logs", "42"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["line-0", "line-1", "line-2"]


def test_jobs_logs_supports_standard_raw_columns_options(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--format", "raw", "--columns", "line"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["line-0", "line-1", "line-2"]


def test_jobs_logs_supports_structured_formatter_output(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(
        app, ["jobs", "logs", "42", "--format", "json", "--columns", "line"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ('[{"line": "line-0"}, {"line": "line-1"}, {"line": "line-2"}]')


def test_jobs_logs_tail_returns_only_last_n(fake_aap: Any) -> None:
    _seed_running_job(fake_aap)
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--tail", "2"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["line-1", "line-2"]


def test_jobs_logs_grep_filters_lines(fake_aap: Any) -> None:
    fake_aap.seed(
        "jobs",
        id=42,
        status="successful",
        stdout="info: ok\nERROR: boom\ninfo: done\n",
    )
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--grep", "ERROR"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "ERROR: boom"


def test_jobs_logs_grep_ignore_case(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, status="successful", stdout="error: lower\nfine\n")
    result = CliRunner().invoke(
        app,
        ["jobs", "logs", "42", "--grep", "ERROR", "--ignore-case"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "error: lower"


def test_jobs_logs_invalid_grep_pattern_rejected_at_boundary(fake_aap: Any) -> None:
    """An unterminated character class is user input, not a bug — it must
    surface as a clean ``BadParameter`` (typer exit 2), not a Python
    traceback through ``report_errors`` (which only translates
    ``UntapedError``)."""
    fake_aap.seed("jobs", id=42, status="successful", stdout="anything\n")
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--grep", "[unclosed"])
    assert result.exit_code != 0
    assert "is not a valid regex" in result.output
    # Make sure the underlying Python re.error didn't escape.
    assert "Traceback" not in result.output


def test_jobs_get_with_kind_workflow_job_hits_workflow_jobs_endpoint(fake_aap: Any) -> None:
    """``jobs get --kind workflow_job <id>`` routes to ``workflow_jobs/<id>/``,
    not the default ``jobs/<id>/`` (which would 404 for workflow_job ids).
    PollingJobMonitor and WatchJob already understand this kind via
    ``KIND_TO_API_PATH`` — wiring it through the CLI completes the path."""
    fake_aap.seed(
        "workflow_jobs",
        id=999,
        name="nightly-pipeline",
        status="successful",
    )
    result = CliRunner().invoke(
        app,
        ["jobs", "get", "999", "--kind", "workflow_job", "--format", "raw", "--columns", "name"],
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "nightly-pipeline"


def _seed_fk_prereqs(fake: Any) -> None:
    """Seed the org / inventory / project records every JT-launch test
    needs. FakeAap's ``_action`` handler materialises the launched job
    record at a fresh id, so callers only seed JT records on top.
    """
    fake.seed("organizations", id=1, name="Default")
    fake.seed(
        "inventories", id=20, name="prod", organization=1, organization_name="Default", kind=""
    )
    fake.seed(
        "projects",
        id=10,
        name="playbooks",
        organization=1,
        organization_name="Default",
        scm_type="git",
    )


def _seed_jt(fake: Any, *, name: str, id: int, playbook: str) -> None:
    fake.seed(
        "job_templates",
        id=id,
        name=name,
        organization=1,
        organization_name="Default",
        project=10,
        project_name="playbooks",
        inventory=20,
        inventory_name="prod",
        playbook=playbook,
    )


def _seed_basic_jt(fake: Any, *, job_status: str) -> None:
    _seed_fk_prereqs(fake)
    _seed_jt(fake, name="deploy", id=30, playbook="deploy.yml")
    fake.next_action_status = job_status


def _seed_two_jts(fake: Any) -> None:
    _seed_fk_prereqs(fake)
    _seed_jt(fake, name="deploy-a", id=30, playbook="a.yml")
    _seed_jt(fake, name="deploy-b", id=31, playbook="b.yml")


class _PrefixingStubStream:
    """Stand-in for ``StreamJobEvents`` that yields one identifiable
    event per worker. The play name carries the materialised job id so
    a failing assertion's stderr dump tells us which worker emitted
    what. Used by every test that asserts on prefixed output.
    """

    def __init__(self, monitor: Any) -> None:
        pass

    def __call__(self, job: Any, *, follow: bool = True, **_kwargs: Any) -> Any:
        from untaped_awx.domain import JobEvent

        return iter([JobEvent(counter=1, event="playbook_on_play_start", play=f"job-{job.id}")])


def test_launch_track_exits_zero_on_successful_job(fake_aap: Any) -> None:
    _seed_basic_jt(fake_aap, job_status="successful")
    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy", "--track"])
    assert result.exit_code == 0, result.output


def test_launch_track_exits_one_on_job_failure(fake_aap: Any) -> None:
    _seed_basic_jt(fake_aap, job_status="failed")
    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy", "--track"])
    assert result.exit_code == 1


def test_launch_track_parallel_drains_concurrently(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ``--track`` jobs must drain concurrently. We prove it by
    blocking each worker on a 2-party :class:`threading.Barrier`: a
    sequential implementation can never reach the second worker, the
    barrier times out, and the test fails.
    """
    import threading

    from untaped_awx.cli import resource_commands

    _seed_two_jts(fake_aap)
    barrier = threading.Barrier(2, timeout=15)

    class _BarrierStream:
        def __init__(self, monitor: Any) -> None:
            pass

        def __call__(self, job: Any, *, follow: bool = True, **_kwargs: Any) -> Any:
            barrier.wait()
            return iter(())

    monkeypatch.setattr(resource_commands, "StreamJobEvents", _BarrierStream)

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 0, result.output


def test_launch_track_output_lines_carry_template_prefix(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent multi-template event output must be prefixed with the
    originating template name so a shared stderr stays disambiguable.
    """
    from untaped_awx.cli import resource_commands

    _seed_two_jts(fake_aap)
    monkeypatch.setattr(resource_commands, "StreamJobEvents", _PrefixingStubStream)

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 0, result.output
    assert "[deploy-a]" in result.stderr
    assert "[deploy-b]" in result.stderr


def test_launch_track_one_failed_exits_one_and_logs_both(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed terminal statuses across templates: one ``failed`` → exit 1
    AND both templates' events still reach stderr with their prefixes.

    The ``next_action_status`` override is one-shot, so ``deploy-a``
    (first launch) ends ``failed`` and ``deploy-b`` defaults back to
    ``successful``. ``failed`` is a terminal status — no exception is
    raised — so the failure flows through ``_drain_parallel`` into
    ``jobs`` and the post-loop ``any(j.status != "successful")`` block
    triggers ``exit 1``.
    """
    from untaped_awx.cli import resource_commands

    _seed_two_jts(fake_aap)
    fake_aap.next_action_status = "failed"
    monkeypatch.setattr(resource_commands, "StreamJobEvents", _PrefixingStubStream)

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 1, result.output
    assert "[deploy-a]" in result.stderr
    assert "[deploy-b]" in result.stderr


def test_launch_wait_parallel_returns_results_in_launch_order(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--wait`` (no ``--track``) for two templates exercises
    ``_wait_parallel``. The collected ``jobs`` list must be in launch
    order so the table-output rows mirror the user-supplied ``ids``.

    ``WatchJob`` is patched with a stub that returns the input ``Job``
    after a small delay; the slow path (``deploy-a``) finishes after
    the fast path (``deploy-b``) but the result list still puts
    ``deploy-a`` first because ``_wait_parallel`` walks ``futures`` in
    launch order before calling ``result()``.
    """
    import time

    from untaped_awx.cli import resource_commands
    from untaped_awx.domain import Job

    _seed_two_jts(fake_aap)

    class _StubWatch:
        def __init__(self, client: Any) -> None:
            pass

        def __call__(self, job: Job, **_kwargs: Any) -> Job:
            # First-launched (deploy-a) sleeps longer than second
            # (deploy-b) so future-completion order != launch order.
            # The id parity isolates which template each callback got.
            if job.id % 2 == 0:
                time.sleep(0.05)
            return job.model_copy(update={"status": "successful"})

    monkeypatch.setattr(resource_commands, "WatchJob", _StubWatch)

    result = CliRunner().invoke(
        app,
        [
            "job-templates",
            "launch",
            "deploy-a",
            "deploy-b",
            "--wait",
            "--format",
            "raw",
            "--columns",
            "name",
        ],
    )
    assert result.exit_code == 0, result.output
    # `name` column for the two materialised jobs: ``deploy-a-launch``
    # first, ``deploy-b-launch`` second — preserves launch order even
    # though the deploy-a worker completed second.
    rows = [line for line in result.stdout.strip().splitlines() if line]
    assert rows == ["deploy-a-launch", "deploy-b-launch"], result.output


def test_launch_track_worker_exception_wraps_to_untaped_error(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-``UntapedError`` raised inside one ``--track`` worker must
    not abort the whole batch as a raw traceback. The worker wraps it
    as ``UntapedError(f"{type(exc).__name__}: {exc}")``, the caller
    echoes ``error: <name>: <wrapped>``, ``any_failed`` flips, and the
    other worker's events still reach stderr.

    Pins the wrap-message format so the ``error: deploy-a: deploy-a:
    ...`` double-prefix bug (round-2 review) cannot regress.
    """
    from untaped_awx.cli import resource_commands
    from untaped_awx.domain import JobEvent

    _seed_two_jts(fake_aap)

    class _StubStreamWithDeployAFailure:
        def __init__(self, monitor: Any) -> None:
            pass

        def __call__(self, job: Any, *, follow: bool = True, **_kwargs: Any) -> Any:
            # ``deploy-a`` materialises at the first new job id (32);
            # ``deploy-b`` at the second (33). Discriminate by parity
            # so the test isn't coupled to FakeAap's id sequencing.
            if job.id % 2 == 0:
                raise RuntimeError("boom")
            return iter([JobEvent(counter=1, event="playbook_on_play_start", play=f"job-{job.id}")])

    monkeypatch.setattr(resource_commands, "StreamJobEvents", _StubStreamWithDeployAFailure)

    result = CliRunner().invoke(app, ["job-templates", "launch", "deploy-a", "deploy-b", "--track"])
    assert result.exit_code == 1, result.output
    # Single-prefix error row, with the original exception class name
    # preserved for debuggability.
    assert "error: deploy-a: RuntimeError: boom" in result.stderr
    # The other worker isn't aborted by deploy-a's failure: deploy-b's
    # event still streams with its prefix.
    assert "[deploy-b]" in result.stderr


# ── jobs --stdin pipeline shape (issue #154) ────────────────────────────────


def test_jobs_get_accepts_multiple_positional_ids(fake_aap: Any) -> None:
    """``jobs get 42 43`` resolves each id in turn — same contract as
    ``awx <kind> get a b c``."""
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliRunner().invoke(
        app, ["jobs", "get", "42", "43", "--format", "raw", "--columns", "id"]
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines())
    assert ids == ["42", "43"]


def test_jobs_get_reads_ids_from_stdin(fake_aap: Any) -> None:
    """``jobs list -f raw | jobs get --stdin`` is the documented pipeline shape."""
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliRunner().invoke(
        app,
        ["jobs", "get", "--stdin", "--format", "raw", "--columns", "id"],
        input="42\n43\n",
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines())
    assert ids == ["42", "43"]


def test_jobs_get_continues_when_one_id_missing(fake_aap: Any) -> None:
    """A missing id in a multi-id batch must not suppress the resolved
    ids — same rule as ``awx <kind> get --stdin``."""
    _seed_running_job(fake_aap, job_id=42)
    result = CliRunner().invoke(
        app,
        ["jobs", "get", "9999", "42", "--format", "raw", "--columns", "id"],
        input="",
    )
    assert result.exit_code != 0
    # The resolved id reaches stdout — pipeline still gets the row.
    assert result.stdout.strip().splitlines() == ["42"]
    # The per-id error row lands on stderr, never stdout (pipeline contract).
    assert "error: 9999" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_jobs_get_rejects_mixed_positional_and_stdin(fake_aap: Any) -> None:
    """Per ``read_identifiers``: mixing positional and ``--stdin`` is
    refused, since a misplaced flag would silently act on the wrong set."""
    _seed_running_job(fake_aap, job_id=42)
    result = CliRunner().invoke(app, ["jobs", "get", "42", "--stdin"], input="43\n")
    assert result.exit_code != 0
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_jobs_get_rejects_non_numeric_stdin_entry(fake_aap: Any) -> None:
    """Non-numeric job ids surface as a per-id error (not a crash)."""
    _seed_running_job(fake_aap, job_id=42)
    result = CliRunner().invoke(
        app,
        ["jobs", "get", "--stdin", "--format", "raw", "--columns", "id"],
        input="not-a-number\n42\n",
    )
    assert result.exit_code != 0
    # Good id reaches stdout; the bad-line error stays on stderr so a
    # downstream pipe doesn't ingest ``error: …`` as data.
    assert result.stdout.strip().splitlines() == ["42"]
    assert "error: not-a-number" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_jobs_wait_accepts_multiple_positional_ids(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliRunner().invoke(
        app, ["jobs", "wait", "42", "43", "--format", "raw", "--columns", "id"]
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines())
    assert ids == ["42", "43"]


def test_jobs_wait_reads_ids_from_stdin(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliRunner().invoke(
        app,
        ["jobs", "wait", "--stdin", "--format", "raw", "--columns", "id"],
        input="42\n43\n",
    )
    assert result.exit_code == 0, result.output
    ids = sorted(result.stdout.strip().splitlines())
    assert ids == ["42", "43"]


def test_jobs_wait_rejects_mixed_positional_and_stdin(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    result = CliRunner().invoke(app, ["jobs", "wait", "42", "--stdin"], input="43\n")
    assert result.exit_code != 0
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_jobs_wait_continues_when_one_id_missing(fake_aap: Any) -> None:
    """A missing id in a multi-id ``wait`` batch must not suppress the
    resolved ids — same pipeline-resilience contract as ``jobs get``."""
    _seed_running_job(fake_aap, job_id=42)
    result = CliRunner().invoke(
        app, ["jobs", "wait", "9999", "42", "--format", "raw", "--columns", "id"]
    )
    assert result.exit_code != 0
    assert result.stdout.strip().splitlines() == ["42"]
    assert "error: 9999" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_jobs_wait_multi_id_timeout(fake_aap: Any) -> None:
    """Multi-id ``wait --timeout`` aggregates timeout breadcrumbs to
    stderr and exits 1 if any id never reaches terminal.

    FakeAap returns the seeded job ``status="running"`` so ``WatchJob``
    never sees a terminal state within ``--timeout 0``.
    """
    fake_aap.seed("jobs", id=42, status="running")
    fake_aap.seed("jobs", id=43, status="running")
    result = CliRunner().invoke(app, ["jobs", "wait", "42", "43", "--timeout", "0"])
    assert result.exit_code == 1
    stderr = result.stderr or ""
    # One breadcrumb per id — neither was silently dropped.
    assert "timeout: job 42" in stderr
    assert "timeout: job 43" in stderr


def test_jobs_logs_concatenates_streams_across_ids(fake_aap: Any) -> None:
    """Multiple ids drain serially; each job's stdout is emitted in
    turn. A ``[<id>] `` stderr breadcrumb identifies which job is up."""
    fake_aap.seed("jobs", id=42, status="successful", stdout="alpha-1\nalpha-2\n")
    fake_aap.seed("jobs", id=43, status="successful", stdout="beta-1\nbeta-2\n")
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "43"])
    assert result.exit_code == 0, result.output
    out = result.stdout.strip().splitlines()
    assert out == ["alpha-1", "alpha-2", "beta-1", "beta-2"]
    # Breadcrumb to stderr so stdout stays clean for piping.
    assert "[42]" in result.stderr
    assert "[43]" in result.stderr


def test_jobs_logs_reads_ids_from_stdin(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, status="successful", stdout="alpha\n")
    fake_aap.seed("jobs", id=43, status="successful", stdout="beta\n")
    result = CliRunner().invoke(
        app,
        ["jobs", "logs", "--stdin"],
        input="42\n43\n",
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == ["alpha", "beta"]


def test_jobs_logs_rejects_mixed_positional_and_stdin(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=42, status="successful", stdout="x\n")
    result = CliRunner().invoke(app, ["jobs", "logs", "42", "--stdin"], input="43\n")
    assert result.exit_code != 0
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_jobs_logs_continues_when_one_id_missing(fake_aap: Any) -> None:
    """A missing id in a multi-id ``logs`` batch streams what landed
    and emits a per-id error on stderr without aborting."""
    fake_aap.seed("jobs", id=42, status="successful", stdout="alpha\n")
    result = CliRunner().invoke(app, ["jobs", "logs", "9999", "42"])
    assert result.exit_code != 0
    # The reachable job's stdout still made it to stdout.
    assert "alpha" in result.stdout
    assert "error: 9999" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_jobs_events_concatenates_streams_across_ids(fake_aap: Any) -> None:
    """Two seeded jobs each get a one-event log; events ``--format raw
    --columns counter`` emits both rows in id order with a breadcrumb."""
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    fake_aap.seed("job_events", id=1, job=42, counter=1, event="playbook_on_play_start")
    fake_aap.seed("job_events", id=2, job=43, counter=2, event="playbook_on_play_start")
    result = CliRunner().invoke(
        app,
        ["jobs", "events", "42", "43", "--format", "raw", "--columns", "counter"],
    )
    assert result.exit_code == 0, result.output
    counters = result.stdout.strip().splitlines()
    assert counters == ["1", "2"]
    assert "[42]" in result.stderr
    assert "[43]" in result.stderr


def test_jobs_events_reads_ids_from_stdin(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    fake_aap.seed("job_events", id=1, job=42, counter=1, event="playbook_on_play_start")
    fake_aap.seed("job_events", id=2, job=43, counter=2, event="playbook_on_play_start")
    result = CliRunner().invoke(
        app,
        ["jobs", "events", "--stdin", "--format", "raw", "--columns", "counter"],
        input="42\n43\n",
    )
    assert result.exit_code == 0, result.output
    counters = result.stdout.strip().splitlines()
    assert counters == ["1", "2"]


def test_jobs_events_rejects_mixed_positional_and_stdin(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    result = CliRunner().invoke(app, ["jobs", "events", "42", "--stdin"], input="43\n")
    assert result.exit_code != 0
    assert "stdin" in (result.output + (result.stderr or "")).lower()


def test_jobs_events_continues_when_one_id_missing(fake_aap: Any) -> None:
    """A missing id in a multi-id ``events`` batch streams what landed
    and emits a per-id error on stderr without aborting."""
    _seed_running_job(fake_aap, job_id=42)
    fake_aap.seed("job_events", id=1, job=42, counter=1, event="playbook_on_play_start")
    result = CliRunner().invoke(
        app, ["jobs", "events", "9999", "42", "--format", "raw", "--columns", "counter"]
    )
    assert result.exit_code != 0
    assert result.stdout.strip().splitlines() == ["1"]
    assert "error: 9999" in (result.stderr or "")
    assert "error:" not in result.stdout


def test_jobs_events_multi_id_non_follow_emits_per_job_blocks(fake_aap: Any) -> None:
    """Pin the v0 non-follow multi-id contract: each job emits its own
    format block (not a single merged document). For ``--format json``
    that means N separately framed arrays, so callers wanting one
    document should use ``--format raw`` or ``--follow --format json``
    (NDJSON). Documented in ``jobs events`` docstring."""
    import json as _json

    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    fake_aap.seed("job_events", id=1, job=42, counter=1, event="playbook_on_play_start")
    fake_aap.seed("job_events", id=2, job=43, counter=2, event="playbook_on_play_start")
    result = CliRunner().invoke(
        app,
        ["jobs", "events", "42", "43", "--format", "json", "--columns", "counter"],
    )
    assert result.exit_code == 0, result.output
    # Two separately framed arrays — verify they're parseable on their
    # own, not as one document. ``json.loads`` would reject the
    # concatenation; we split-then-parse.
    chunks = result.stdout.replace("][", "]\n[").strip().splitlines()
    assert len(chunks) == 2, result.stdout
    parsed = [_json.loads(c) for c in chunks]
    assert [row[0]["counter"] for row in parsed] == [1, 2]
