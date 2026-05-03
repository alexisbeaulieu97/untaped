"""Quick smoke for TyperPrompt.is_interactive — stdin-only."""

from __future__ import annotations

from untaped_awx.infrastructure.test.prompt import TyperPrompt


def test_is_interactive_when_stdin_is_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    assert TyperPrompt().is_interactive() is True


def test_not_interactive_when_stdin_redirected(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    assert TyperPrompt().is_interactive() is False


def test_force_non_interactive_overrides(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert TyperPrompt(force_non_interactive=True).is_interactive() is False
