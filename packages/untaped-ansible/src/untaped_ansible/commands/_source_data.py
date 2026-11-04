"""The source data the graph commands read: the ``--source`` they share, and their guide."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourceData(BaseModel):
    """Read before trusting a deps, impact, find or graph answer: which data answered it
    (a live read or a source's cache), when a source refreshes, and how a stale, partial or
    paused refresh shows.

    A *source* is a saved selection of a plugin's repos (`source set`) whose
    dependency files are indexed in a local cache; the repos themselves live
    in the git plugin's repo store. `impact` needs one; `deps`, `find` and `graph` read one
    when it is selected.

    ## Where the answer comes from

    Downstream is `deps`, `find` and `graph` down; upstream is `impact` and
    `graph` up.

    - No source selected: downstream reads each repo live from the plugin
      that lists it (`untaped plugin list` shows which fill git's
      `RepoSource`), fetched into the repo store and fetched again only when
      its ref moved; a repo no plugin lists warns and stays unread. Upstream
      has none, so `impact` fails and `graph` leaves it out with a warning.
    - A source selected: both read the source's cache; a target outside it warns
      "not cached" and gives no rows.

    A source is selected by `--source`, or by `ansible.default_source` when
    none is given; an unknown name there fails naming the setting. Repeated
    `--source` add up. `--refresh` and `--cached` need a source.

    ## When a source refreshes

    A command that reads a source refreshes it first when it was never
    refreshed or is older than `ansible.caches.source_data.max_age_seconds`.
    `--refresh` refreshes it now; `--cached` reads only what is cached, never
    asks the source's plugin, and fails with "no cached source data" for a source with no
    cached data (never refreshed, or changed by `source set` or `source patch`
    since).

    A refresh that fails over cached data serves that data with one
    `untaped.stale` line on stderr: `error` says why, and `retry_at` when the
    next automatic check runs (a failure is not retried within five minutes).
    With nothing cached, or when the cached data answers nothing, the command
    fails with the refresh's error (exit 5 for a transient one).

    ## Partial and paused refreshes

    After a refresh that failed for some repos, reads warn on every run with
    an `untaped.stale` line whose `partial` is `true` and whose `error` names
    the repos, and exit 0 unless the answer is empty, until `--refresh` or
    `source refresh` completes a whole refresh.

    After a refresh paused because a host's probe budget was spent
    (`source refresh` says how refs are probed and how it resumes), the source
    is not marked refreshed: reads serve the cached data with a warning whose
    `retry_at` is when the budget resets.

    ## The cache

    The index is a cache. One in an older format is rebuilt empty with a
    warning, and each source refreshes on its next read. One written by a newer
    untaped is refused with an error naming the file, so an older release never
    destroys it.
    """

    source: list[str] = Field(
        default_factory=list,
        description=(
            "Saved source to read cached graph data from; repeat to union. "
            "Defaults to ansible.default_source."
        ),
    )
