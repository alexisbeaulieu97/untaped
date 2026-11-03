`untaped jira` asks its server which Jira it runs on first use and again after
a day (`jira.caches.server_api.max_age_seconds`), refuses Jira Cloud and
releases below 8 with exit 4, and shows what it found in a `jira.server-api`
doctor row; `jira.server_version` says the version instead of detecting it.
