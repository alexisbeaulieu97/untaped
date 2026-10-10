To keep the repo store somewhere else, set `git.store_dir` first, to a new
directory rather than an old cache root (not your old `ansible.cache_dir`).
Then run `untaped setup migrate-dirs`: it deletes the old ansible cache (a
custom `ansible.cache_dir` included, while that setting still names it).
Last, run `untaped config migrate`: it deletes the four ansible settings and
prints the values they held. If you set `ansible.git_clone_protocol: ssh`,
set `github.git_protocol: ssh`. A source's repos now live in the shared repo
store: `untaped ansible source remove NAME` frees the ones no other saved
source selects.
