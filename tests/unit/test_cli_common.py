from __future__ import annotations

import pytest
import typer

from untaped_cli.common import get_tower_url


def test_get_tower_url_requires_environment(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("TOWER_HOST", raising=False)

    with pytest.raises(typer.Exit) as exit_info:
        get_tower_url()

    assert exit_info.value.exit_code == 2
    captured = capsys.readouterr()
    assert "TOWER_HOST" in captured.err


def test_get_tower_url_reflects_environment_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOWER_HOST", "https://tower.one.example.com")
    assert get_tower_url() == "https://tower.one.example.com"

    monkeypatch.setenv("TOWER_HOST", "https://tower.two.example.com/")
    assert get_tower_url() == "https://tower.two.example.com"
