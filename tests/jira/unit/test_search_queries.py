"""JQL rendering tests for Jira issue search."""

from __future__ import annotations

from untaped.capabilities.jira.domain import JiraIssueSearchFilters


def test_default_issue_search_targets_current_users_unresolved_work() -> None:
    query = JiraIssueSearchFilters().to_jql()

    assert query == "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"


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
