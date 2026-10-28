"""M3: golden-file tests for `swarm compile --target claude-code`, and refusal below
the required conformance level. Regenerate with UPDATE_GOLDEN=1 uv run pytest."""

import os
import shutil

import pytest

from swarm_toml.compile.claude_code import CompileRefused, blocking, compile_swarm, conformance
from swarm_toml.graph import Graph
from swarm_toml.load import load

from .conftest import EXAMPLES, REPO

GOLDEN = REPO / "tests" / "golden"
NAMES = ["literature-review", "pipeline", "docs-bot"]


@pytest.mark.parametrize("name", NAMES)
def test_golden(name, tmp_path):
    out = tmp_path / name
    _, written = compile_swarm(load(EXAMPLES / name), out)
    got = {p.relative_to(out).as_posix(): p.read_text() for p in written}
    gdir = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        shutil.rmtree(gdir, ignore_errors=True)
        for rel, text in got.items():
            (gdir / rel).parent.mkdir(parents=True, exist_ok=True)
            (gdir / rel).write_text(text)
    want = {p.relative_to(gdir).as_posix(): p.read_text()
            for p in gdir.rglob("*") if p.is_file()}
    assert sorted(got) == sorted(want)
    for rel, text in want.items():
        assert got[rel] == text, rel


def test_every_clause_has_a_status():
    for name in NAMES:
        for c in conformance(Graph(load(EXAMPLES / name))):
            assert c.status in {"enforced", "approximate", "unenforced", "n/a"}
            assert c.how


def test_refuses_below_required(tmp_path):
    d = tmp_path / "p"
    shutil.copytree(EXAMPLES / "pipeline", d)
    text = (d / "swarm.toml").read_text()
    # escape:coder is unenforced on this target, so requiring L3 must refuse
    (d / "swarm.toml").write_text('[conformance]\nrequired = "L3"\n' + text.replace(
        "[roles.lead]", "[roles.lead]\nallow_escape = true\nescape_reason = \"test\"", 1))
    s = load(d)
    assert blocking(conformance(Graph(s)), "L3")
    with pytest.raises(CompileRefused):
        compile_swarm(s, tmp_path / "out")


def test_refuses_when_check_fails(tmp_path):
    from .conftest import CORPUS

    with pytest.raises(CompileRefused, match="swarm check"):
        compile_swarm(load(CORPUS / "i3-01-over.toml"), tmp_path / "out")


def test_never_overwrites_referenced_files(tmp_path):
    d = tmp_path / "docs-bot"
    shutil.copytree(EXAMPLES / "docs-bot", d)
    before = (d / ".claude/agents/writer.md").read_text()
    compile_swarm(load(d), d)
    assert (d / ".claude/agents/writer.md").read_text() == before
    assert (d / ".claude/agents/planner.md").exists()
