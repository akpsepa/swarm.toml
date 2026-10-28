import pytest

from swarm_toml import globs


@pytest.mark.parametrize("a,b,expect", [
    ("notes/**", "notes/a.md", True),
    ("**", "anything/at/all", True),
    ("src/**", "tests/**", False),
    ("out/*.json", "out/report.json", True),
    ("out/*.json", "out/report.md", False),
    ("a/*/c", "a/b/*", True),
    ("**/secret.txt", "deep/dir/secret.txt", True),
    ("./docs/", "docs/x", True),
    ("swarm.toml", "**", True),
    ("data/a/**", "data/b/**", False),
])
def test_overlaps(a, b, expect):
    assert globs.overlaps(a, b) is expect
    assert globs.overlaps(b, a) is expect


@pytest.mark.parametrize("outer,inner,expect", [
    ("**", "src/**", True),
    ("src/**", "src/a/b.py", True),
    ("src/**", "**", False),
    ("work/notes/**", "work/**", False),
    ("work/**", "work/notes/**", True),
    ("out/*.json", "out/a.json", True),
    ("out/*", "out/*.json", True),
    ("out/*.json", "out/*", False),
])
def test_contains(outer, inner, expect):
    assert globs.contains(outer, inner) is expect


def test_matches():
    assert globs.matches("notes/x/y.md", "notes/**")
    assert not globs.matches("notesx/y.md", "notes/**")
