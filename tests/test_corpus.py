"""RQ3 / Gate 1: validator recall and false positives on the seeded corpus.

A misconfiguration is *caught* when its expected invariant is reported as an error.
A *false positive* is any error on a file marked `expect: none`. Errors from other
invariants on a seeded file are reported as *collateral*; each was reviewed by hand.
"""

from collections import defaultdict

import pytest

from swarm_toml.check import findings_for
from swarm_toml.invariants import MODULES
from swarm_toml.load import load

from .conftest import CORPUS

FILES = sorted(CORPUS.glob("*.toml"))


def expected(p):
    return p.read_text().splitlines()[0].split(":", 1)[1].strip()


def errors(p):
    return {f["rule"] for f in findings_for(load(p)) if f["severity"] == "error"}


@pytest.fixture(scope="module")
def scored():
    return [(p, expected(p), errors(p)) for p in FILES]


def test_corpus_size(scored):
    seeded = [s for s in scored if s[1] != "none"]
    assert len(seeded) >= 40
    assert len(scored) - len(seeded) >= 10


def test_every_invariant_has_failing_fixtures(scored):
    ids = {m.ID for m in MODULES}
    assert ids == {e for _, e, _ in scored if e != "none"}


def test_gate1_recall_and_false_positives(scored, capsys):
    per = defaultdict(lambda: [0, 0])
    fps, collateral = [], []
    for p, exp, errs in scored:
        if exp == "none":
            if errs:
                fps.append((p.name, sorted(errs)))
            continue
        per[exp][1] += 1
        per[exp][0] += exp in errs
        if errs - {exp}:
            collateral.append((p.name, sorted(errs - {exp})))
    caught = sum(c for c, _ in per.values())
    total = sum(n for _, n in per.values())
    recall = caught / total
    with capsys.disabled():
        print(f"\nRQ3 corpus: recall {caught}/{total} = {recall:.1%}; "
              f"false positives {len(fps)}/{len(scored) - total}; collateral {len(collateral)}")
        for k in sorted(per, key=lambda s: int(s[1:])):
            print(f"  {k}: {per[k][0]}/{per[k][1]}")
    assert recall >= 0.95, per
    assert fps == []
