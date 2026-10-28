from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "spec" / "examples"
CORPUS = REPO / "tests" / "corpus"


@pytest.fixture
def write_swarm(tmp_path):
    def _write(text: str, name: str = "swarm.toml") -> Path:
        p = tmp_path / name
        p.write_text(text)
        return p

    return _write
