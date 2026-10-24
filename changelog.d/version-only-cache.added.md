`Cache(max_age=None, model=..., version=...)` is a version-only cache: a value
that depends only on the code computing it is filled once per version and
served from then on, with no max-age setting and nothing on stderr.
