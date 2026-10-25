From 11.0, refresh each ansible source once after upgrading (`untaped ansible
source refresh NAME`), then delete `~/.untaped/plugins/ansible/index.sqlite3`:
12.0 builds the index anew rather than adopt 11.0's file.
