from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from untaped_awx.infrastructure import AwxClient
from untaped_awx.infrastructure.pagination import paginate
from untaped_core.settings import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        """
        awx:
          base_url: https://aap.example.com
          token: secret
          api_prefix: /api/v2/
        """
    )
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))


def _page(*items: dict[str, int], next_url: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={"count": len(items), "next": next_url, "results": list(items)},
    )


def test_paginate_follows_next_url(_config: None) -> None:
    pages = iter(
        [
            _page({"id": 1}, {"id": 2}, next_url="/api/v2/job_templates/?page=2"),
            _page({"id": 3}),
        ]
    )
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(side_effect=lambda _r: next(pages))
        with AwxClient() as client:
            ids = [item["id"] for item in paginate(client, "job_templates/")]
    assert ids == [1, 2, 3]


def test_paginate_respects_limit(_config: None) -> None:
    big_page = _page(*[{"id": i} for i in range(50)], next_url="/api/v2/x/?page=2")
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(return_value=big_page)
        with AwxClient() as client:
            ids = [item["id"] for item in paginate(client, "job_templates/", limit=5)]
    assert ids == [0, 1, 2, 3, 4]


def test_paginate_passes_initial_params_then_follows_next(_config: None) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        if "page=2" in seen_paths[-1]:
            return _page({"id": 99})
        return _page({"id": 1}, next_url="/api/v2/job_templates/?page=2")

    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(side_effect=handler)
        with AwxClient() as client:
            list(paginate(client, "job_templates/", params={"search": "deploy"}))

    assert any("search=deploy" in p for p in seen_paths)
    assert any("page=2" in p for p in seen_paths)
    # Search filter MUST NOT be re-applied to follow-ups; the `next` URL has its own params.
    assert sum("search=deploy" in p for p in seen_paths) == 1
