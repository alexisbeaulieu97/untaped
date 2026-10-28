`untaped workspace run NAME -` runs the piped script as a `sh -c` string
instead of a temporary file, and an empty pipe says the command is empty
(exit 2).
