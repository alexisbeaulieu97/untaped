"""Render a :class:`JobEvent` as a single human-readable stderr line.

Picks an indent and a status word from the AWX event-name discriminator
so a streaming feed reads like the AWX UI's "Output" tab without ANSI
or a TUI:

    PLAY [Deploy app]
    TASK [common : install]
      ok: web-01
      changed: web-02
      failed: api-01

The format is intentionally line-oriented (one event per line, no
multi-line bodies, no colour codes) so it survives piping into
``less``, ``grep``, or a CI log collector.
"""

from __future__ import annotations

from untaped_awx.domain import JobEvent

# AWX event names → terse human result word for runner_on_* rows.
_RUNNER_RESULTS: dict[str, str] = {
    "runner_on_ok": "ok",
    "runner_on_changed": "changed",
    "runner_on_failed": "failed",
    "runner_on_unreachable": "unreachable",
    "runner_on_skipped": "skipped",
    "runner_on_no_hosts": "no-hosts",
    "runner_on_async_ok": "ok",
    "runner_on_async_failed": "failed",
    "runner_item_on_ok": "ok",
    "runner_item_on_changed": "changed",
    "runner_item_on_failed": "failed",
    "runner_item_on_skipped": "skipped",
}


def render_event(ev: JobEvent) -> str:
    """Return one rendered line for ``ev`` (no trailing newline)."""
    if ev.event == "playbook_on_play_start":
        play = ev.play or "(unnamed play)"
        return f"PLAY [{play}]"
    if ev.event == "playbook_on_task_start":
        task = ev.task or "(unnamed task)"
        return f"TASK [{task}]"
    if ev.event in _RUNNER_RESULTS:
        verdict = _RUNNER_RESULTS[ev.event]
        host = ev.host or "?"
        return f"  {verdict}: {host}"
    if ev.event == "playbook_on_stats":
        return "PLAY RECAP"
    if ev.event == "playbook_on_no_hosts_matched":
        return "skipped: no hosts matched"
    # Fallback for events we don't have a special rendering for.
    parts = [ev.event or f"#{ev.counter}"]
    if ev.host:
        parts.append(f"host={ev.host}")
    if ev.task:
        parts.append(f"task={ev.task}")
    return " ".join(parts)
