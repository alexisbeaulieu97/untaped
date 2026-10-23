At `ansible.index_path`'s default, the dependency index is now
`~/.untaped/plugins/ansible/index.v4.sqlite3`: the first command that opens
it renames `index.sqlite3` there, with nothing rebuilt. A path you set is used
as it is. Upgrade every install that shares `~/.untaped` to this release:
from the rename on, 11.0 no longer shares the index and builds its own.
