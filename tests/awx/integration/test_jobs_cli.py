"""End-to-end CLI tests for the upgraded ``untaped awx jobs`` UX."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from untaped.capabilities.awx.cli import app, parallel
from untaped.capabilities.awx.infrastructure.job_monitor import PollingJobMonitor
from untaped.settings import get_settings
from untaped.testing import CliInvoker

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


def test_jobs_list_table_honours_global_ui_collection_view(
    fake_aap: Any,
    aap_config: Path,
) -> None:
    aap_config.write_text(
        """
        profiles:
          default:
            ui:
              collection_view: list
            awx:
              base_url: https://aap.example.com
              token: secret
              api_prefix: /api/v2/
        """
    )
    get_settings.cache_clear()
    _seed_running_job(fake_aap, job_id=42)

    result = CliInvoker().invoke(app, ["jobs", "list", "--format", "table"])

    assert result.exit_code == 0, result.output
    assert "id: 42" in result.stdout
    assert "status: successful" in result.stdout
    assert not any(ch in result.stdout for ch in "╭╮╰╯┌┐└┘│─")


def test_jobs_list_status_filter_passes_to_awx(fake_aap: Any) -> None:
    fake_aap.seed("jobs", id=1, name="ok", status="successful")
    fake_aap.seed("jobs", id=2, name="bad", status="failed")
    result = CliInvoker().invoke(
        app, ["jobs", "list", "--status", "failed", "--format", "raw", "--columns", "id"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "2"


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
    result = CliInvoker().invoke(
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
    on a TTY, plain text under ``CliInvoker`` (which doesn't simulate one).
    """
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliInvoker().invoke(app, ["jobs", "events", "42", "--follow"])
    assert result.exit_code == 0, result.output
    # ``CliInvoker`` has no TTY, so colour is stripped — but the rendered
    # shape (PLAY/TASK/ok/failed) must still appear.
    out = result.stdout
    assert "PLAY [Deploy]" in out
    assert "TASK [install]" in out
    assert "ok: web-01" in out
    assert "failed: api-01" in out


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], ["1", "2", "3", "4"]),
        (["--filter", "event=runner_on_failed"], ["4"]),
        (["--from-counter", "2"], ["3", "4"]),
    ],
)
def test_jobs_events_selection(fake_aap: Any, args: list[str], expected: list[str]) -> None:
    _seed_running_job(fake_aap)
    _seed_events(fake_aap)
    result = CliInvoker().invoke(
        app, ["jobs", "events", "42", *args, "--format", "raw", "--columns", "counter"]
    )
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.split()) == expected


_LINES = ["line-0", "line-1", "line-2"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        # log rows are single-field: raw and ``--columns line`` print the bare lines
        ([], _LINES),
        (["--format", "raw", "--columns", "line"], _LINES),
        (["--follow"], _LINES),
        (["--follow", "--format", "raw", "--columns", "line"], _LINES),
        (["--tail", "2"], _LINES[1:]),
        (["--format", "json", "--columns", "line"], [json.dumps([{"line": x} for x in _LINES])]),
        # --follow json is NDJSON: one bare object per line, straight into ``jq``
        (["--follow", "--format", "json"], [json.dumps({"job": 42, "line": x}) for x in _LINES]),
        (["--follow", "--format", "json", "--columns", "line"],
         [json.dumps({"line": x}) for x in _LINES]),
    ],
)  # fmt: skip
def test_jobs_logs_output_shapes(fake_aap: Any, args: list[str], expected: list[str]) -> None:
    _seed_running_job(fake_aap)
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", *args])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--grep", "ERROR"], ["ERROR: boom"]),
        (["--grep", "ERROR", "--ignore-case"], ["ERROR: boom", "error: low"]),
    ],
)
def test_jobs_logs_grep_filters_lines(fake_aap: Any, args: list[str], expected: list[str]) -> None:
    fake_aap.seed("jobs", id=42, status="successful", stdout="info\nERROR: boom\nerror: low\n")
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", *args])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == expected


def test_jobs_logs_follow_yaml_keeps_per_line_emission(fake_aap: Any) -> None:
    """``--follow --format yaml`` keeps the existing per-line single-doc
    emission. YAML has no NDJSON-equivalent canonical streaming form, so
    this is an explicit regression pin: don't accidentally fold yaml into
    the NDJSON path while reshaping the json branch."""
    _seed_running_job(fake_aap)
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", "--follow", "--format", "yaml"])
    assert result.exit_code == 0, result.output
    # Three single-doc yaml blocks, one per log line.
    out = result.stdout
    assert out.count("line: line-0") == 1
    assert out.count("line: line-1") == 1
    assert out.count("line: line-2") == 1


def test_jobs_logs_follow_json_empty_stream_emits_nothing(fake_aap: Any) -> None:
    """An empty log under ``--follow --format json`` emits an empty stdout —
    NOT ``[]`` or a blank document. Pins the NDJSON-of-zero-rows contract
    so a downstream ``jq`` consumer doesn't trip on a single empty doc.
    """
    fake_aap.seed("jobs", id=42, name="empty", status="successful", stdout="")
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", "--follow", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""


def test_jobs_logs_follow_json_multi_id_keeps_stdout_pipe_clean(fake_aap: Any) -> None:
    """Multi-id ``logs --follow --format json`` keeps the breadcrumbs on
    stderr (``[<id>]``) and stdout pure NDJSON — a refactor that leaks
    the breadcrumb to stdout would silently break ``jq`` consumers.
    """
    import json as _json

    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", "43", "--follow", "--format", "json"])
    assert result.exit_code == 0, result.output
    # Breadcrumbs on stderr only.
    assert "[42]" in result.stderr
    assert "[43]" in result.stderr
    assert "[42]" not in result.stdout
    assert "[43]" not in result.stdout
    # stdout is pure NDJSON: every non-empty line parses as a bare dict.
    parsed = [_json.loads(line) for line in result.stdout.strip().splitlines() if line]
    assert all(isinstance(p, dict) and "line" in p for p in parsed), parsed
    # Both jobs' lines made it through (3 each = 6 total).
    assert len(parsed) == 6


def test_jobs_logs_invalid_grep_pattern_rejected_at_boundary(fake_aap: Any) -> None:
    """An unterminated character class is user input, not a bug — it must
    surface as a clean usage error, not a Python
    traceback through ``report_errors`` (which only translates
    ``UntapedError``)."""
    fake_aap.seed("jobs", id=42, status="successful", stdout="anything\n")
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", "--grep", "[unclosed"])
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
    result = CliInvoker().invoke(
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


def _prefixing_stub_stream(_monitor: Any, job: Any, **_kwargs: Any) -> Any:
    """Stand-in for ``JobMonitor.stream_events`` that yields one identifiable
    event per worker. The play name carries the materialised job id so
    a failing assertion's stderr dump tells us which worker emitted
    what. Used by every test that asserts on prefixed output.
    """
    from untaped.capabilities.awx.domain import JobEvent

    return iter([JobEvent(counter=1, event="playbook_on_play_start", play=f"job-{job.id}")])


def test_launch_track_exits_zero_on_successful_job(fake_aap: Any) -> None:
    _seed_basic_jt(fake_aap, job_status="successful")
    result = CliInvoker().invoke(app, ["job-templates", "launch", "deploy", "--track"])
    assert result.exit_code == 0, result.output


def test_launch_track_exits_one_on_job_failure(fake_aap: Any) -> None:
    _seed_basic_jt(fake_aap, job_status="failed")
    result = CliInvoker().invoke(app, ["job-templates", "launch", "deploy", "--track"])
    assert result.exit_code == 1


def test_launch_track_shows_the_failure_reason(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from untaped.capabilities.awx.domain import JobEvent

    _seed_basic_jt(fake_aap, job_status="failed")
    failed = JobEvent(
        counter=1,
        event="runner_on_failed",
        host_name="web-01",
        stdout='fatal: [web-01]: FAILED! => {"msg": "disk full"}',
    )
    monkeypatch.setattr(PollingJobMonitor, "stream_events", lambda *_a, **_k: iter([failed]))

    result = CliInvoker().invoke(app, ["job-templates", "launch", "deploy", "--track"])

    assert result.exit_code == 1
    assert "[deploy]   failed: web-01" in result.stderr
    assert '[deploy]     fatal: [web-01]: FAILED! => {"msg": "disk full"}' in result.stderr


def test_launch_track_parallel_drains_concurrently(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ``--track`` jobs must drain concurrently. We prove it by
    blocking each worker on a 2-party :class:`threading.Barrier`: a
    sequential implementation can never reach the second worker, the
    barrier times out, and the test fails.
    """
    import threading

    _seed_two_jts(fake_aap)
    barrier = threading.Barrier(2, timeout=15)

    def _barrier_stream(_monitor: Any, job: Any, **_kwargs: Any) -> Any:
        barrier.wait()
        return iter(())

    monkeypatch.setattr(PollingJobMonitor, "stream_events", _barrier_stream)

    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "--yes", "deploy-a", "deploy-b", "--track"]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(("status", "exit_code"), [("successful", 0), ("failed", 1)])
def test_launch_track_prefixes_every_template_and_fails_on_any_failure(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch, status: str, exit_code: int
) -> None:
    """Concurrent ``--track`` output carries each template's name on the shared
    stderr; one failed execution (the first launch only) exits 1 while the
    other template's events still stream."""
    _seed_two_jts(fake_aap)
    fake_aap.next_action_status = status
    monkeypatch.setattr(PollingJobMonitor, "stream_events", _prefixing_stub_stream)

    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "--yes", "deploy-a", "deploy-b", "--track"]
    )
    assert result.exit_code == exit_code, result.output
    assert "[deploy-a]" in result.stderr
    assert "[deploy-b]" in result.stderr


def test_launch_wait_parallel_returns_results_in_launch_order(
    fake_aap: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--wait`` (no ``--track``) for two templates exercises
    ``wait_parallel``. The collected ``jobs`` list must be in launch
    order so the table-output rows mirror the user-supplied ``ids``.

    ``WatchJob`` is patched with a stub that returns the input ``Job``
    after a small delay; the slow path (``deploy-a``) finishes after
    the fast path (``deploy-b``) but the result list still puts
    ``deploy-a`` first because ``wait_parallel`` walks ``futures`` in
    launch order before calling ``result()``.
    """
    import time

    from untaped.capabilities.awx.domain import Job

    _seed_two_jts(fake_aap)

    class _StubWatch:
        def __init__(self, client: Any, **_kwargs: Any) -> None:
            pass

        def __call__(self, job: Job, **_kwargs: Any) -> Job:
            # First-launched (deploy-a) sleeps longer than second
            # (deploy-b) so future-completion order != launch order.
            # The id parity isolates which template each callback got.
            if job.id % 2 == 0:
                time.sleep(0.05)
            return job.model_copy(update={"status": "successful"})

    monkeypatch.setattr(parallel, "WatchJob", _StubWatch)

    result = CliInvoker().invoke(
        app,
        [
            "job-templates",
            "launch",
            "--yes",
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
    from untaped.capabilities.awx.domain import JobEvent

    _seed_two_jts(fake_aap)

    def _stub_stream_with_deploy_a_failure(_monitor: Any, job: Any, **_kwargs: Any) -> Any:
        # ``deploy-a`` materialises at the first new job id (32);
        # ``deploy-b`` at the second (33). Discriminate by parity
        # so the test isn't coupled to FakeAap's id sequencing.
        if job.id % 2 == 0:
            raise RuntimeError("boom")
        return iter([JobEvent(counter=1, event="playbook_on_play_start", play=f"job-{job.id}")])

    monkeypatch.setattr(PollingJobMonitor, "stream_events", _stub_stream_with_deploy_a_failure)

    result = CliInvoker().invoke(
        app, ["job-templates", "launch", "--yes", "deploy-a", "deploy-b", "--track"]
    )
    assert result.exit_code == 1, result.output
    # Single-prefix error row, with the original exception class name
    # preserved for debuggability.
    assert "failed: deploy-a: RuntimeError: boom" in result.stderr
    # The other worker isn't aborted by deploy-a's failure: deploy-b's
    # event still streams with its prefix.
    assert "[deploy-b]" in result.stderr


# ── jobs --stdin pipeline shape (issue #154) ────────────────────────────────


# Each multi-id jobs command, with the flags and the stdout it prints for jobs 42 and 43.
_MULTI_ID = {
    "get": (["--format", "raw", "--columns", "id"], ["42", "43"]),
    "wait": (["--format", "raw", "--columns", "id"], ["42", "43"]),
    "logs": ([], ["alpha", "beta"]),
    "events": (["--format", "raw", "--columns", "counter"], ["1", "2"]),
}


@pytest.fixture
def two_jobs(fake_aap: Any) -> Any:
    for job_id, line, counter in ((42, "alpha", 1), (43, "beta", 2)):
        fake_aap.seed("jobs", id=job_id, name="deploy", status="successful", stdout=f"{line}\n")
        fake_aap.seed(
            "job_events", id=counter, job=job_id, counter=counter, event="playbook_on_play_start"
        )
    return fake_aap


@pytest.mark.parametrize("stdin", [False, True], ids=["positional", "stdin"])
@pytest.mark.parametrize("command", sorted(_MULTI_ID))
def test_jobs_commands_take_several_ids(two_jobs: Any, command: str, stdin: bool) -> None:
    """``jobs list -f raw | jobs <command> --stdin`` is the documented pipeline shape."""
    flags, expected = _MULTI_ID[command]
    ids = ["--stdin"] if stdin else ["42", "43"]
    result = CliInvoker().invoke(app, ["jobs", command, *ids, *flags], input="42\n43\n")
    assert result.exit_code == 0, result.output
    assert sorted(result.stdout.strip().splitlines()) == expected
    if command in {"logs", "events"}:  # streams name the job they come from, on stderr
        assert "[42]" in result.stderr and "[43]" in result.stderr


@pytest.mark.parametrize("command", sorted(_MULTI_ID))
def test_jobs_commands_reject_mixed_positional_and_stdin(two_jobs: Any, command: str) -> None:
    result = CliInvoker().invoke(app, ["jobs", command, "42", "--stdin"], input="43\n")
    assert result.exit_code != 0
    assert "stdin" in result.stderr.lower()


@pytest.mark.parametrize(
    ("command", "ids", "input", "bad"),
    [
        *((command, ["9999", "42"], None, "9999") for command in sorted(_MULTI_ID)),
        ("get", ["--stdin"], "not-a-number\n42\n", "not-a-number"),
    ],
)
def test_jobs_commands_continue_past_a_bad_id(
    two_jobs: Any, command: str, ids: list[str], input: str | None, bad: str
) -> None:
    """A bad id fails the exit status but never suppresses the rows that resolved,
    and its error stays on stderr so pipelines do not ingest it as data."""
    flags, expected = _MULTI_ID[command]
    result = CliInvoker().invoke(app, ["jobs", command, *ids, *flags], input=input)
    assert result.exit_code == 1
    assert result.stdout.strip().splitlines() == expected[:1]
    assert f"error: {bad}" in result.stderr
    assert "error:" not in result.stdout


def test_jobs_get_reads_ids_from_pipe_envelope_stdin(fake_aap: Any) -> None:
    """`jobs list --format pipe | jobs get --stdin` extracts the numeric `id`
    from each envelope (id_field="id"), coercing the int record value to a
    string for the lookup."""
    _seed_running_job(fake_aap, job_id=42)
    envelope = json.dumps({"untaped": "1", "kind": "awx.job", "record": {"id": 42, "name": "run"}})
    result = CliInvoker().invoke(
        app,
        ["jobs", "get", "--stdin", "--format", "raw", "--columns", "id"],
        input=envelope + "\n",
    )
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "42"


def test_jobs_wait_stdin_honours_execution_kind_from_pipe(fake_aap: Any) -> None:
    """A launched workflow's pipe row must be waited on at workflow_jobs/, not jobs/."""
    fake_aap.seed("workflow_jobs", id=77, name="wf", status="successful")
    fake_aap.seed("jobs", id=42, name="run", status="successful")
    lines = [
        {"untaped": "1", "kind": "awx.job", "record": {"id": 77, "kind": "workflow_job"}},
        {"untaped": "1", "kind": "awx.job", "record": {"id": 42, "type": "job"}},
    ]
    result = CliInvoker().invoke(
        app,
        ["jobs", "wait", "--stdin", "--format", "json"],
        input="".join(json.dumps(line) + "\n" for line in lines),
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [(row["id"], row["kind"]) for row in rows] == [(77, "workflow_job"), (42, "job")]


@pytest.mark.parametrize("command", ["get", "events", "logs", "wait"])
def test_jobs_stdin_rejects_records_of_another_kind(fake_aap: Any, command: str) -> None:
    """Host ids piped into a jobs command never pass as job ids."""
    envelope = json.dumps({"untaped": "1", "kind": "awx.host", "record": {"id": 42}})
    result = CliInvoker().invoke(app, ["jobs", command, "--stdin"], input=envelope + "\n")
    assert result.exit_code == 2, result.output
    assert "record kind 'awx.host' is not accepted here" in result.stderr
    assert not fake_aap.router.calls


@pytest.mark.parametrize(("args", "expected"), [([], 20), (["--limit", "0"], 25)])
def test_jobs_list_defaults_to_twenty_and_zero_means_all(
    fake_aap: Any, args: list[str], expected: int
) -> None:
    for index in range(25):
        fake_aap.seed("jobs", id=100 + index, name=f"run{index}", status="successful")
    result = CliInvoker().invoke(app, ["jobs", "list", *args, "--format", "raw", "--columns", "id"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.split()) == expected


def test_jobs_kind_rejects_unknown_values(fake_aap: Any) -> None:
    result = CliInvoker().invoke(app, ["jobs", "get", "42", "--kind", "workflow"])
    assert result.exit_code == 2, result.output


def test_jobs_get_rejects_non_numeric_stdin_entry(fake_aap: Any) -> None:
    """Non-numeric job ids surface as a per-id error (not a crash)."""
    _seed_running_job(fake_aap, job_id=42)
    result = CliInvoker().invoke(
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


def test_jobs_wait_multi_id_timeout(fake_aap: Any) -> None:
    """Multi-id ``wait --timeout`` aggregates timeout breadcrumbs to
    stderr and exits 1 if any id never reaches terminal.

    FakeAap returns the seeded job ``status="running"`` so ``WatchJob``
    never sees a terminal state within ``--timeout 0``.
    """
    fake_aap.seed("jobs", id=42, status="running")
    fake_aap.seed("jobs", id=43, status="running")
    result = CliInvoker().invoke(app, ["jobs", "wait", "42", "43", "--timeout", "0"])
    assert result.exit_code == 1
    stderr = result.stderr or ""
    # One breadcrumb per id — neither was silently dropped.
    assert "timeout: job 42" in stderr
    assert "timeout: job 43" in stderr


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_jobs_events_multi_id_non_follow_emits_one_document(fake_aap: Any, fmt: str) -> None:
    """json/yaml print a single document per invocation: several jobs'
    events merge into one array, each row naming its ``job``."""
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    fake_aap.seed("job_events", id=1, job=42, counter=1, event="playbook_on_play_start")
    fake_aap.seed("job_events", id=2, job=43, counter=2, event="playbook_on_play_start")
    result = CliInvoker().invoke(
        app,
        ["jobs", "events", "42", "43", "--format", fmt, "--columns", "job,counter"],
    )
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.stdout) == [
        {"job": 42, "counter": 1},
        {"job": 43, "counter": 2},
    ]
    assert "[42]" in result.stderr


def test_jobs_events_json_keeps_one_document_when_an_id_is_missing(fake_aap: Any) -> None:
    _seed_running_job(fake_aap, job_id=42)
    fake_aap.seed("job_events", id=1, job=42, counter=1, event="playbook_on_play_start")
    result = CliInvoker().invoke(
        app, ["jobs", "events", "9999", "42", "--format", "json", "--columns", "counter"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout) == [{"counter": 1}]
    assert "error: 9999" in result.stderr


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_jobs_logs_multi_id_non_follow_emits_one_document(fake_aap: Any, fmt: str) -> None:
    _seed_running_job(fake_aap, job_id=42)
    _seed_running_job(fake_aap, job_id=43)
    result = CliInvoker().invoke(app, ["jobs", "logs", "42", "43", "--format", fmt])
    assert result.exit_code == 0, result.output
    rows = yaml.safe_load(result.stdout)
    assert [(row["job"], row["line"]) for row in rows] == [
        (job, f"line-{n}") for job in (42, 43) for n in range(3)
    ]


def test_jobs_list_empty_guides_with_stderr_hint(fake_aap: Any) -> None:
    result = CliInvoker().invoke(app, ["jobs", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "No jobs found" in result.stderr
