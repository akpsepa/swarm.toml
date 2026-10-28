import json

import pytest

from swarm_toml.load import InvalidFile, load
from swarm_toml.model import json_schema

from .conftest import REPO


def test_committed_schema_matches_model():
    committed = json.loads((REPO / "spec" / "swarm.schema.json").read_text())
    assert committed == json_schema(), "regenerate spec/swarm.schema.json"


@pytest.mark.parametrize("text,needle", [
    ("[roles.a\n", "TOML"),
    ("[swarm]\nname = 'x'\n", "roles"),
    ("[roles.a]\nbogus = 1\n", "bogus"),
    ("[conformance]\nrequired = 'L9'\n[roles.a]\n", "required"),
    ("[roles.A]\n", "roles"),
    ("[roles.a]\nmax_instances = 0\n", "max_instances"),
    ("[roles.a]\npermissions = 'perms:x'\n", "permissions"),
    ("[roles.a]\n[[channel]]\nname='c'\npaths=['x']\n[[channel]]\nname='c'\npaths=['y']\n",
     "duplicate"),
])
def test_invalid_files_raise(write_swarm, text, needle):
    with pytest.raises(InvalidFile) as e:
        load(write_swarm(text))
    assert needle in str(e.value)


def test_invalid_file_reports_line(write_swarm):
    p = write_swarm("[swarm]\nname = 'x'\n\n[roles.a]\nmax_instances = 0\n")
    with pytest.raises(InvalidFile) as e:
        load(p)
    assert e.value.problems[0][0] == 5
