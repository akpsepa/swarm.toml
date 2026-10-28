"""M2: `swarm check` exits 0, 1 or 2 and prints file:line for each finding."""

from swarm_toml.check import main

from .conftest import CORPUS, EXAMPLES


def test_exit_0_ok(capsys):
    assert main(["check", str(EXAMPLES / "literature-review")]) == 0


def test_exit_0_with_warnings(capsys):
    assert main(["check", str(EXAMPLES / "pipeline")]) == 0
    assert "warning I8" in capsys.readouterr().out


def test_exit_1_errors_with_file_line(capsys):
    assert main(["check", str(CORPUS / "i3-01-over.toml")]) == 1
    out = capsys.readouterr().out
    # the over-budget finding points at the spawner's budget line
    assert "i3-01-over.toml:9: error I3:" in out


def test_exit_2_invalid(write_swarm, capsys):
    assert main(["check", str(write_swarm("[roles.a\n"))]) == 2
    assert "invalid" in capsys.readouterr().err


def test_exit_2_missing(tmp_path, capsys):
    assert main(["check", str(tmp_path)]) == 2


def test_json_output(capsys):
    import json

    main(["check", "--json", str(EXAMPLES / "pipeline")])
    doc = json.loads(capsys.readouterr().out)
    assert doc["level"] == "L1" and doc["hash"].startswith("sha256:")
    assert all({"rule", "severity", "line"} <= set(f) for f in doc["findings"])


def test_runtime_findings_escalate_at_l3(write_swarm):
    body = "[roles.a]\npermissions = { allow = ['Write(shared/**)'] }\n" \
           "[roles.b]\npermissions = { allow = ['Read(shared/**)'] }\n"
    assert main(["check", str(write_swarm(body))]) == 0  # I6 is a warning at L1
    assert main(["check", str(write_swarm("[conformance]\nrequired='L3'\n" + body))]) == 1
