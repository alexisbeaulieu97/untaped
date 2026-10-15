`untaped workspace remove NAME` drops a workspace's records (archiving an
active one first) and releases each repo no other workspace uses from the
repo store: workspace's refs and its pushed branches go, and the repo itself
when nothing else uses it. Unpushed commits on the workspace's own branch
refuse the removal and other unpushed branches are kept, unless `--force`; a
stash is never deleted.
