`untaped ansible source patch` flags that leave an invalid source (no org,
team or repo left, a repo that isn't `owner/name`) exit 2, as `source set`'s
do; they exited 1. A damaged source row reads `a source selects at least one
org, team or repo` instead of naming `source set`'s flags.
