`untaped jira issues patch` sends its requests once instead of resending them
after Jira's 429 and 503 answers or a dropped connection, so an `update` add
(a comment, a label) from `--fields-file` can no longer be applied twice.
