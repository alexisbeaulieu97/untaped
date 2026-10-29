Every place that reads a GitHub `owner/name` repo id agrees on what one is:
exactly one slash, an owner that does not start with a dot, a name that is
not `.` or `..`. `untaped ansible` read `acme/roles/web` as a repo in a
dependency source and in a live read; it now leaves such a source unresolved.
