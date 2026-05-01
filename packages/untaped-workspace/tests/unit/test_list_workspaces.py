from untaped_workspace.application import ListWorkspaces
from untaped_workspace.domain import Workspace


class _StubRepo:
    def __init__(self, items: list[Workspace]) -> None:
        self.items = items

    def entries(self) -> list[Workspace]:
        return self.items


def test_returns_repository_results() -> None:
    repo = _StubRepo([Workspace(name="prod", path="/tmp/prod")])
    use_case = ListWorkspaces(repo)
    assert use_case() == [Workspace(name="prod", path="/tmp/prod")]


def test_empty_when_no_workspaces() -> None:
    use_case = ListWorkspaces(_StubRepo([]))
    assert use_case() == []
