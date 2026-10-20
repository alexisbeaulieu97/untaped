`untaped jira issues patch` sends its requests once instead of retrying them
on Jira's 429 and 503 answers, so an `update` add (a comment, a label) from
`--fields-file` can no longer be applied twice.
