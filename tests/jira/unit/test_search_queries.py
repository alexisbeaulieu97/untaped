"""JQL rendering tests for Jira issue search."""

from __future__ import annotations

from untaped.capabilities.jira.domain import JiraIssueSearchFilters


def test_default_jql_applies_only_without_other_filters() -> None:
    default = "assignee = currentUser() AND resolution = Unresolved"

    bare = JiraIssueSearchFilters(default_jql=default).to_jql()
    filtered = JiraIssueSearchFilters(default_jql=default, project="ABC").to_jql()

    assert bare == "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
    assert filtered == "project = ABC ORDER BY updated DESC"


def test_scope_jql_is_anded_with_raw_jql_and_raw_order_by_wins() -> None:
    query = JiraIssueSearchFilters(
        scope_jql="assignee = currentUser() ORDER BY created DESC",
        raw_jql="project = SEC ORDER BY priority DESC",
        status="Open",
    ).to_jql()

    assert query == (
        '(assignee = currentUser()) AND (project = SEC) AND status = "Open" ORDER BY priority DESC'
    )


def test_scope_jql_order_by_used_when_raw_jql_has_none() -> None:
    query = JiraIssueSearchFilters(
        scope_jql="assignee = currentUser() ORDER BY created DESC",
        raw_jql="project = SEC",
    ).to_jql()

    assert query == "(assignee = currentUser()) AND (project = SEC) ORDER BY created DESC"


def test_shortcut_filters_render_jql_with_default_order() -> None:
    query = JiraIssueSearchFilters(
        project="ABC",
        assignee="alexis",
        status="In Progress",
        text="broken deploy",
        sprint="42",
    ).to_jql()

    assert query == (
        'project = ABC AND assignee = "alexis" AND status = "In Progress" '
        'AND text ~ "broken deploy" AND sprint = 42 ORDER BY updated DESC'
    )


def test_raw_jql_combines_with_shortcut_filters_before_order_by() -> None:
    query = JiraIssueSearchFilters(
        raw_jql="labels = urgent ORDER BY priority DESC",
        project="ABC",
        status="Open",
    ).to_jql()

    assert query == '(labels = urgent) AND project = ABC AND status = "Open" ORDER BY priority DESC'


def test_raw_jql_order_by_inside_quoted_text_is_not_split() -> None:
    query = JiraIssueSearchFilters(
        raw_jql='text ~ "foo order by bar"',
        project="ABC",
    ).to_jql()

    assert query == '(text ~ "foo order by bar") AND project = ABC ORDER BY updated DESC'
