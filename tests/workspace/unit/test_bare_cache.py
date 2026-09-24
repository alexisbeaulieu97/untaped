import hashlib
from pathlib import Path

import pytest

from untaped.capabilities.workspace.infrastructure.bare_cache import cache_path_for


def _unknown(url: str) -> str:
    return f"_unknown/{hashlib.sha256(url.encode()).hexdigest()[:16]}.git"


@pytest.mark.parametrize(
    ("url", "relative"),
    [
        ("https://github.com/org/svc-a.git", "github.com/org/svc-a.git"),
        ("git@github.com:org/svc-bee.git", "github.com/org/svc-bee.git"),
        ("https://github.com/org/svc-c", "github.com/org/svc-c.git"),
        # No host (plain path, file:// URL, garbage) -> deterministic hashed leaf.
        ("/local/path/no-host", _unknown("/local/path/no-host")),
        ("file:///tmp/foo/svc-a.git", _unknown("file:///tmp/foo/svc-a.git")),
        ("not a url at all", _unknown("not a url at all")),
    ],
)
def test_cache_layout(tmp_path: Path, url: str, relative: str) -> None:
    assert cache_path_for(url, cache_dir=tmp_path) == (tmp_path / relative).resolve()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil/../../tmp/pwn.git",
        "https://evil/org/..",
        "git@evil:../../../tmp/pwn.git",
        "a@evil/../..:x/y.git",
        "https://evil/org/..\\..\\x.git",
    ],
)
def test_dot_dot_segments_stay_inside_cache_root(tmp_path: Path, url: str) -> None:
    root = tmp_path.resolve()
    p = cache_path_for(url, cache_dir=tmp_path)
    assert p.resolve().is_relative_to(root)
    assert ".." not in p.parts
    assert "\\" not in str(p.relative_to(root))
