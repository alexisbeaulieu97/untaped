`workspace.caches.repo_source_repos.max_age_seconds` sets how long workspace
keeps the repos each provider lists (6 hours by default). After a failed
refresh workspace serves the cached list for 5 minutes without asking again
(`ctrl-r` in the picker asks at once), and while a cached list exists an
automatic refresh gives up after 10 s instead of retrying.
