`awx job-templates usage` and `awx workflow-templates usage` draw the
workflows that run a template as a tree with `--format tree` (add
`--recursive` for the whole chain), and each row now says what the workflow
runs (`contains`), for which queried template (`target`) and whether the
depth limit stopped there (`stopped`), so JSON holds the whole graph.
