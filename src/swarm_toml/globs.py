"""Path-glob relations used by the invariants: match, overlap and containment.

Globs are repo-relative and use `/` separators. `**` matches any number of segments,
`*` and `?` match within one segment. Overlap and containment are decided segment by
segment. Within one segment, patterns with wildcards on both sides are compared by
their literal prefix and suffix, which over-approximates overlap (never misses one) and
under-approximates containment (never claims one that does not hold).
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache

WILD = set("*?[")


def norm(glob: str) -> str:
    g = glob.strip()
    while g.startswith("./"):
        g = g[2:]
    g = g.lstrip("/")
    if g in ("", "."):
        return "**"
    if g.endswith("/"):
        g += "**"
    return g


def _segs(glob: str) -> tuple[str, ...]:
    return tuple(s for s in norm(glob).split("/") if s)


def _has_wild(seg: str) -> bool:
    return any(c in WILD for c in seg)


def _lit_prefix(seg: str) -> str:
    out = []
    for c in seg:
        if c in WILD:
            break
        out.append(c)
    return "".join(out)


def _lit_suffix(seg: str) -> str:
    return _lit_prefix(seg[::-1])[::-1]


def _seg_overlap(a: str, b: str) -> bool:
    if not _has_wild(a):
        return fnmatchcase(a, b) if _has_wild(b) else a == b
    if not _has_wild(b):
        return fnmatchcase(b, a)
    pa, pb = _lit_prefix(a), _lit_prefix(b)
    sa, sb = _lit_suffix(a), _lit_suffix(b)
    return (pa.startswith(pb) or pb.startswith(pa)) and (sa.endswith(sb) or sb.endswith(sa))


def _seg_contains(outer: str, inner: str) -> bool:
    if outer == inner or outer == "*":
        return True
    if not _has_wild(inner):
        return fnmatchcase(inner, outer)
    return False


@lru_cache(maxsize=4096)
def _overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a and not b:
        return True
    if a and a[0] == "**":
        return _overlap(a[1:], b) or (bool(b) and _overlap(a, b[1:]))
    if b and b[0] == "**":
        return _overlap(a, b[1:]) or (bool(a) and _overlap(a[1:], b))
    if not a or not b:
        return False
    return _seg_overlap(a[0], b[0]) and _overlap(a[1:], b[1:])


@lru_cache(maxsize=4096)
def _contains(o: tuple[str, ...], i: tuple[str, ...]) -> bool:
    if not o:
        return not i
    if o[0] == "**":
        # `**` absorbs zero or more leading segments of the inner pattern.
        if _contains(o[1:], i):
            return True
        return bool(i) and (i[0] == "**" and _contains(o, i[1:]) or
                            i[0] != "**" and _contains(o, i[1:]))
    if not i or i[0] == "**":
        return False
    return _seg_contains(o[0], i[0]) and _contains(o[1:], i[1:])


def overlaps(a: str, b: str) -> bool:
    """True if some path could match both globs."""
    return _overlap(_segs(a), _segs(b))


def contains(outer: str, inner: str) -> bool:
    """True if every path matching `inner` also matches `outer`."""
    return _contains(_segs(outer), _segs(inner))


def matches(path: str, glob: str) -> bool:
    """True if the concrete repo-relative `path` matches `glob`."""
    return _contains(_segs(glob), _segs(path))
