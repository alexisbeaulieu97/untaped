**Breaking (users):** the install and project references live in
`~/.untaped/plugins/untaped/refs/`, core's `refs` folder, instead of
`~/.untaped/refs/`. Each file is a data file stamped like every other file
untaped keeps (`"untaped": {"file": 1, "by": "<version>"}`), readable by its
owner only, and a writer waits on the folder's own lock: a busy one says
`untaped.refs is busy (another untaped process)`.
