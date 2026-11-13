"""Discover, parse and resolve a swarm.toml file."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .model import SwarmFile
from .perms import PermSet, resolve

FILENAME = "swarm.toml"


class InvalidFile(Exception):
    """The file is missing, is not TOML, or does not match the schema (exit code 2)."""

    def __init__(self, path: Path, problems: list[tuple[int, str]]):
        self.path = path
        self.problems = problems
        super().__init__("; ".join(m for _, m in problems))


def discover(start: Path) -> Path:
    start = start.resolve()
    if start.is_file():
        return start
    for d in (start, *start.parents):
        if (d / FILENAME).is_file():
            return d / FILENAME
    raise InvalidFile(start / FILENAME, [(0, f"no {FILENAME} found in {start} or its parents")])


_HEADER = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")
_KEY = re.compile(r"^\s*([A-Za-z0-9_\-\"'.]+)\s*=")


class Locator:
    """Maps a key path such as ("roles", "coder", "can_spawn") or ("channel", 1, "paths")
    to a 1-based line number by scanning the TOML text. Falls back to the nearest table."""

    def __init__(self, text: str):
        self.tables: dict[tuple, int] = {}
        self.keys: dict[tuple, int] = {}
        counters: dict[tuple, int] = {}
        current: tuple = ()
        for no, line in enumerate(text.splitlines(), 1):
            m = _HEADER.match(line)
            if m:
                parts = tuple(p.strip().strip("\"'") for p in m.group(2).split("."))
                if m.group(1) == "[[":
                    idx = counters.get(parts, 0)
                    counters[parts] = idx + 1
                    current = (*parts, idx)
                else:
                    current = parts
                self.tables.setdefault(current, no)
                continue
            k = _KEY.match(line)
            if k:
                kparts = tuple(p.strip("\"'") for p in k.group(1).split("."))
                self.keys.setdefault((*current, *kparts), no)

    def line(self, path: tuple) -> int:
        for n in range(len(path), 0, -1):
            p = tuple(path[:n])
            if p in self.keys:
                return self.keys[p]
            if p in self.tables:
                return self.tables[p]
        return 1


@dataclass
class Swarm:
    path: Path
    model: SwarmFile
    locator: Locator
    perms: dict[str, PermSet]
    referenced: list[Path] = field(default_factory=list)

    @property
    def root_dir(self) -> Path:
        return self.path.parent

    @property
    def name(self) -> str:
        return self.model.swarm.name or self.root_dir.name

    def rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.root_dir.resolve()).as_posix()
        except ValueError:
            return p.as_posix()

    def protected(self) -> list[str]:
        """Repo-relative globs no role may write (I7)."""
        out = [FILENAME, ".claude/**", ".swarm/**", self.model.audit.path]
        out += [self.rel(p) for p in self.referenced]
        return sorted(set(out))

    def digest(self) -> str:
        """sha256 over swarm.toml and every file it references, in a stable order."""
        return digest_files(self.root_dir, [self.rel(p) for p in self.referenced])


def digest_files(root: Path, referenced: list[str]) -> str:
    """The policy hash: swarm.toml plus the referenced files (repo-relative), in order.
    The enforcer recomputes this from disk on every call."""
    h = hashlib.sha256()
    for rel in [FILENAME, *sorted(set(referenced))]:
        p = root / rel
        h.update(rel.encode() + b"\0")
        h.update(p.read_bytes() if p.is_file() else b"<missing>")
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def _pydantic_problems(err: ValidationError, loc: Locator) -> list[tuple[int, str]]:
    out = []
    for e in err.errors():
        path = tuple(e["loc"])
        where = ".".join(str(p) for p in path) or "(file)"
        out.append((loc.line(path), f"{where}: {e['msg']}"))
    return out


def load(start: Path | str) -> Swarm:
    path = discover(Path(start))
    try:
        text = path.read_text()
    except OSError as e:
        raise InvalidFile(path, [(0, str(e))]) from e
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"line (\d+)", str(e))
        raise InvalidFile(path, [(int(m.group(1)) if m else 1, f"TOML: {e}")]) from e
    loc = Locator(text)
    try:
        model = SwarmFile.model_validate(data)
    except ValidationError as e:
        raise InvalidFile(path, _pydantic_problems(e, loc)) from e
    perms = {name: resolve(role, path.parent) for name, role in model.roles.items()}
    referenced = sorted({f for ps in perms.values() for f in ps.files})
    return Swarm(path=path, model=model, locator=loc, perms=perms, referenced=referenced)
