**Breaking (users):** ansible no longer renames 11.0's `index.sqlite3` to its
layout's name; it builds its index anew, and leaves the old file in place.
