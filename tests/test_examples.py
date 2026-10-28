"""M1: the RFC plan's two examples and its ten-line minimal file parse, with defaults."""

import pytest

from swarm_toml.check import findings_for
from swarm_toml.graph import Graph
from swarm_toml.load import load

from .conftest import EXAMPLES


@pytest.mark.parametrize("name", ["literature-review", "pipeline", "docs-bot"])
def test_example_parses_with_no_errors(name):
    s = load(EXAMPLES / name)
    assert [f for f in findings_for(s) if f["severity"] == "error"] == []


def test_minimal_file_is_ten_lines_and_gets_defaults():
    s = load(EXAMPLES / "docs-bot")
    assert len(s.path.read_text().strip().splitlines()) == 10
    m = s.model
    assert m.swarm.default == "deny"
    assert m.delegation.attenuate is True
    assert m.conformance.required == "L1"
    assert m.identity.issuer == "enforcer"
    g = Graph(s)
    assert g.roots == ["planner"]
    assert m.delegation.max_depth is None and g.max_depth() == 1  # deepest declared chain


def test_flat_group_is_clean_at_l4():
    s = load(EXAMPLES / "literature-review")
    assert s.model.conformance.required == "L4"
    assert findings_for(s) == []


def test_pipeline_explicit_max_depth_and_resolved_profiles():
    s = load(EXAMPLES / "pipeline")
    assert Graph(s).max_depth() == 2
    assert all(ps.resolved for ps in s.perms.values())
    assert "code.exec" in s.perms["coder"].caps
    assert "network.egress" not in s.perms["coder"].caps  # removed by `capabilities`


def test_digest_covers_referenced_files(tmp_path):
    import shutil

    d = tmp_path / "p"
    shutil.copytree(EXAMPLES / "pipeline", d)
    before = load(d).digest()
    prof = d / ".agents" / "permissions.json"
    prof.write_text(prof.read_text().replace("Bash(uv run pytest:*)", "Bash"))
    assert load(d).digest() != before
