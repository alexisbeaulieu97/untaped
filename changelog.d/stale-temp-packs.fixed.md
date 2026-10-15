The repo store deletes what an interrupted fetch left in a repository once it
is an hour old, such as temporary pack files that could hold gigabytes and
lock files that made every later fetch fail; the 10.x repo caches kept them.
