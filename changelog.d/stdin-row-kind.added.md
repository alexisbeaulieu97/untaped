`self.stdin.kind(row)` names the kind a piped row was read as, so a command
taking `Stdin[Record]` can report where each row came from.
