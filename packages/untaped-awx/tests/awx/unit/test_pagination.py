from __future__ import annotations

import httpx
import pytest
import respx

from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure import AwxClient
from untaped_awx.infrastructure.pagination import paginate
from untaped_awx.settings import AwxSettings


def _page(*items: dict[str, int], next_url: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={"count": len(items), "next": next_url, "results": list(items)},
    )


def test_paginate_follows_next_url(awx_config: AwxSettings) -> None:
    pages = iter(
        [
            _page({"id": 1}, {"id": 2}, next_url="/api/v2/job_templates/?page=2"),
            _page({"id": 3}),
        ]
    )
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(side_effect=lambda _r: next(pages))
        with AwxClient(awx_config) as client:
            ids = [item["id"] for item in paginate(client, "job_templates/")]
    assert ids == [1, 2, 3]


def test_paginate_respects_limit(awx_config: AwxSettings) -> None:
    big_page = _page(*[{"id": i} for i in range(50)], next_url="/api/v2/x/?page=2")
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(return_value=big_page)
        with AwxClient(awx_config) as client:
            ids = [item["id"] for item in paginate(client, "job_templates/", limit=5)]
    assert ids == [0, 1, 2, 3, 4]


def test_paginate_passes_initial_params_then_follows_next(awx_config: AwxSettings) -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        if "page=2" in seen_paths[-1]:
            return _page({"id": 99})
        return _page({"id": 1}, next_url="/api/v2/job_templates/?page=2")

    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(side_effect=handler)
        with AwxClient(awx_config) as client:
            list(paginate(client, "job_templates/", params={"search": "deploy"}))

    assert any("search=deploy" in p for p in seen_paths)
    assert any("page=2" in p for p in seen_paths)
    # Search filter MUST NOT be re-applied to follow-ups; the `next` URL has its own params.
    assert sum("search=deploy" in p for p in seen_paths) == 1


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example.com/api/v2/job_templates/?page=2",
        "//evil.example.com/api/v2/job_templates/?page=2",
        "http://aap.example.com/api/v2/job_templates/?page=2",
        "https://aap.example.com:8443/api/v2/job_templates/?page=2",
    ],
)
def test_paginate_refuses_next_url_on_another_origin(
    awx_config: AwxSettings, next_url: str
) -> None:
    with respx.mock(assert_all_called=False) as mock:
        first = mock.get("https://aap.example.com/api/v2/job_templates/").mock(
            return_value=_page({"id": 1}, next_url=next_url)
        )
        elsewhere = mock.route().mock(return_value=_page({"id": 2}))
        with AwxClient(awx_config) as client, pytest.raises(AwxApiError, match="origin"):
            list(paginate(client, "job_templates/"))
    assert first.call_count == 1
    assert elsewhere.call_count == 0


def test_paginate_follows_absolute_next_url_on_the_configured_origin(
    awx_config: AwxSettings,
) -> None:
    pages = iter(
        [
            _page({"id": 1}, next_url="https://AAP.example.com:443/api/v2/job_templates/?page=2"),
            _page({"id": 2}),
        ]
    )
    with respx.mock(base_url="https://aap.example.com", assert_all_called=False) as mock:
        mock.get(url__regex=r".*/job_templates/.*").mock(side_effect=lambda _r: next(pages))
        with AwxClient(awx_config) as client:
            ids = [item["id"] for item in paginate(client, "job_templates/")]
    assert ids == [1, 2]
