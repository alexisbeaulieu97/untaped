Repo caches (workspace, ansible refresh, github sweep and cache) now delete
what interrupted fetches left behind: temporary pack files, which could hold
gigabytes, and a `shallow.lock` that made every later fetch fail.
