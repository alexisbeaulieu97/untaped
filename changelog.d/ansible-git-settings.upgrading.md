Run `untaped config migrate`: it deletes the four ansible settings and prints
the values they held. If you set `ansible.git_clone_protocol: ssh`, set
`github.git_protocol: ssh`; if you kept a custom `ansible.cache_dir`, set
`git.store_dir` instead. Nothing reads the old ansible cache directory any
more. A source's repos now live in the shared repo store: `untaped ansible
source remove NAME` frees the ones no other saved source selects.
