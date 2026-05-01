from typing import Any

from untaped_github.application import WhoAmI
from untaped_github.domain import GithubUser


class _StubClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def me(self) -> dict[str, Any]:
        return self.payload


def test_validates_payload_into_domain_model() -> None:
    payload = {"login": "octocat", "id": 1, "name": "The Octocat", "email": None}
    user = WhoAmI(_StubClient(payload))()
    assert user == GithubUser(login="octocat", id=1, name="The Octocat", email=None)


def test_ignores_extra_fields() -> None:
    payload = {"login": "octocat", "id": 1, "avatar_url": "https://...", "company": "GH"}
    user = WhoAmI(_StubClient(payload))()
    assert user.login == "octocat"
    assert user.id == 1
