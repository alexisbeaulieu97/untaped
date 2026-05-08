"""Render a :class:`JobEvent` as a single human-readable line.

Two surfaces:

- :func:`render_event` — plain string, used by tests, ``--format raw``,
  and any consumer that wants stable byte-for-byte output.
- :func:`render_event_text` — :class:`rich.text.Text` carrying status
  styles. Pass it to a :class:`rich.console.Console`; when stdout/stderr
  is a TTY, Rich emits ANSI colour, when piped or redirected the styling
  is stripped automatically — same shape as ``less | grep | tee``.

Picks an indent and a status word from the AWX event-name discriminator
so a streaming feed reads like the AWX UI's "Output" tab without needing
a TUI:

    PLAY [Deploy app]
    TASK [common : install]
      ok: web-01
      changed: web-02
      failed: api-01
"""

from __future__ import annotations

from rich.text import Text

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

# Per-result colour. Conventions match `ansible-playbook` so anyone
# already used to Ansible's output recognises them at a glance.
_RUNNER_STYLES: dict[str, str] = {
    "ok": "green",
    "changed": "yellow",
    "failed": "bold red",
    "unreachable": "bold red",
    "skipped": "cyan",
    "no-hosts": "dim",
}

_PLAY_STYLE = "bold cyan"
_TASK_STYLE = "bold blue"
_RECAP_STYLE = "bold"
_PREFIX_STYLE = "dim cyan"


def _host_label(ev: JobEvent) -> str:
    """Prefer the denormalised ``host_name``; fall back to the FK id.

    AWX's ``host`` field on a JobEvent is a foreign key (an integer or
    ``null``). The rendered name lives on ``host_name`` because the
    referenced :class:`Host` record can be deleted while events that
    pointed at it remain in the audit log.
    """
    if ev.host_name:
        return ev.host_name
    if ev.host is not None:
        return str(ev.host)
    return "?"


def _render_body(ev: JobEvent) -> Text:
    if ev.event == "playbook_on_play_start":
        play = ev.play or "(unnamed play)"
        return Text(f"PLAY [{play}]", style=_PLAY_STYLE)
    if ev.event == "playbook_on_task_start":
        task = ev.task or "(unnamed task)"
        return Text(f"TASK [{task}]", style=_TASK_STYLE)
    if ev.event in _RUNNER_RESULTS:
        verdict = _RUNNER_RESULTS[ev.event]
        style = _RUNNER_STYLES.get(verdict, "")
        line = Text("  ")
        line.append(verdict, style=style)
        line.append(f": {_host_label(ev)}")
        return line
    if ev.event == "playbook_on_stats":
        return Text("PLAY RECAP", style=_RECAP_STYLE)
    if ev.event == "playbook_on_no_hosts_matched":
        return Text("skipped: no hosts matched", style=_RUNNER_STYLES["skipped"])
    parts = [ev.event or f"#{ev.counter}"]
    if ev.host_name or ev.host is not None:
        parts.append(f"host={_host_label(ev)}")
    if ev.task:
        parts.append(f"task={ev.task}")
    return Text(" ".join(parts))


def render_event_text(ev: JobEvent, *, prefix: str = "") -> Text:
    """Return :class:`rich.text.Text` with status styling.

    Use with :class:`rich.console.Console` so colour is emitted on TTY
    and stripped on pipes — no manual ``isatty`` check required.

    When ``prefix`` is non-empty, ``[<prefix>] `` is prepended (dim
    cyan) so concurrent multi-template event streams stay
    disambiguable on a shared stderr.
    """
    body = _render_body(ev)
    if not prefix:
        return body
    return Text.assemble(Text(f"[{prefix}] ", style=_PREFIX_STYLE), body)


def render_event(ev: JobEvent, *, prefix: str = "") -> str:
    """Return one rendered line for ``ev`` (no trailing newline, no ANSI)."""
    return render_event_text(ev, prefix=prefix).plain
