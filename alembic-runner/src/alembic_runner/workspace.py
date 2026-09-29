"""the directories a flow's runs work in."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")


class WorkspaceError(ValueError):
    pass


def _check_id(value: str, what: str) -> str:
    value = str(value)
    if not _ID.match(value):
        raise WorkspaceError(f"{what} {value!r} is not a safe directory name")
    return value


def _relative(path: str) -> PurePosixPath:
    rel = PurePosixPath(path)
    if not path or rel.is_absolute() or ".." in rel.parts or "\\" in path:
        raise WorkspaceError(f"input path {path!r} must be relative and stay inside the input")
    return rel


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


@dataclass(frozen=True)
class Workspace:
    """`<root>/flows/<flow>/` is alembic's working directory for every run of a
    flow: the apply journal and local state live under its `.alembic/`, so a
    resume has to run there. each run keeps its own files under `runs/<run>/`.
    """

    root: Path
    flow: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "flow", _check_id(self.flow, "flow id"))

    @property
    def flow_dir(self) -> Path:
        return self.root / "flows" / self.flow

    def run_dir(self, run: str) -> Path:
        return self.flow_dir / "runs" / _check_id(run, "run id")

    def prepare(self, run: str) -> Path:
        path = self.run_dir(run)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def input_dir(self, run: str) -> Path:
        """where `write_input` puts a run's files: paths in the flow resolve here."""
        return self.run_dir(run) / "input"

    def write_input(self, run: str, files: dict[str, str], entry: str) -> Path:
        """write the snapshotted source files, replacing any earlier copy, and
        return the path of `entry` among them."""
        entry_rel = _relative(entry)
        if entry not in files:
            raise WorkspaceError(f"entry {entry!r} is not among the input files")
        self.prepare(run)
        base = self.input_dir(run)
        if base.exists():
            shutil.rmtree(base)
        for path, content in files.items():
            target = base / _relative(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return base / entry_rel

    def write_backend_config(self, run: str, name: str, document: dict[str, Any]) -> Path:
        # json is yaml, and alembic reads it as such. the file is private even
        # though no credential is in it: configs name hosts and paths.
        path = self.prepare(run) / f"{_check_id(name, 'config name')}.backend.yaml"
        _write_private(path, json.dumps(document, indent=2).encode())
        return path

    def write_file(self, run: str, name: str, data: bytes) -> Path:
        if not _FILE.match(name):
            raise WorkspaceError(f"{name!r} is not a plain file name")
        path = self.prepare(run) / name
        _write_private(path, data)
        return path
