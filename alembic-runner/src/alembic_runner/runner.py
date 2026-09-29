"""running the alembic cli for plan, stale check, apply and drift."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .backend import Backend, StateStore
from .plan import ApplyReport, DocumentError, DriftReport, Plan, sha256
from .workspace import Workspace

# pre-1.0, a minor release may break the cli, so the range stops at the next minor.
SUPPORTED = ((0, 10, 0), (0, 11, 0))

# what a run inherits from the worker. everything else is built per run, so the
# host's own secrets (django SECRET_KEY, database passwords) never reach alembic.
PASSTHROUGH_ENV = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

OUTPUT_LIMIT = 1 << 20

_VERSION = re.compile(r"^alembic (\d+)\.(\d+)\.(\d+)\b")


class RunnerError(RuntimeError):
    pass


def _truncate(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    if len(text) > OUTPUT_LIMIT:
        return text[:OUTPUT_LIMIT] + f"\n[truncated {len(text) - OUTPUT_LIMIT} characters]"
    return text


@dataclass(frozen=True)
class Completed:
    argv: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def describe(self) -> str:
        if self.timed_out:
            return f"alembic {self.argv[1]} timed out after {self.duration:.0f}s"
        return f"alembic {self.argv[1]} exited {self.exit_code}"


@dataclass(frozen=True)
class Flow:
    """what a flow's runs need from the host: its id, its target and its flags.
    a flow with a `source` imports its inventory from that backend."""

    id: str
    target: Backend
    allow_delete: bool = False
    no_adopt: bool = False
    state_key: str | None = None
    source: Backend | None = None

    @property
    def key(self) -> str:
        return self.state_key or f"flow-{self.id}"


@dataclass(frozen=True)
class PlanOutcome:
    completed: Completed
    plan: Plan | None = None
    error: str | None = None


@dataclass(frozen=True)
class InventoryOutcome:
    """an inventory an import or a map wrote, at `inventory`."""

    completed: Completed
    inventory: Path | None = None
    error: str | None = None


@dataclass(frozen=True)
class StaleCheck:
    completed: Completed
    stale: bool
    current: Plan | None = None
    error: str | None = None


@dataclass(frozen=True)
class ApplyOutcome:
    completed: Completed
    report: ApplyReport | None = None
    error: str | None = None


@dataclass(frozen=True)
class DriftOutcome:
    completed: Completed
    report: DriftReport | None = None
    error: str | None = None


@dataclass
class Runner:
    workspace_root: Path
    state: StateStore = field(default_factory=StateStore)
    alembic_path: str = "alembic"
    timeout: float = 1800
    rust_log: str | None = None
    # alembic's plugins directory. runs get a scrubbed environment and run in the
    # flow's directory, so alembic would otherwise look in ./plugins there.
    plugins_dir: str | None = None

    def version(self) -> tuple[int, int, int]:
        try:
            out = subprocess.run(
                [self.alembic_path, "--version"],
                capture_output=True,
                timeout=30,
                env=self._base_env(Path("/")),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RunnerError(f"cannot run {self.alembic_path}: {e}") from e
        match = _VERSION.match(out.stdout.decode("utf-8", "replace"))
        if not match:
            raise RunnerError(f"{self.alembic_path} --version printed no alembic version")
        return tuple(int(part) for part in match.groups())  # type: ignore[return-value]

    def check_version(self) -> str:
        version = self.version()
        low, high = SUPPORTED
        text = ".".join(map(str, version))
        if not low <= version < high:
            raise RunnerError(
                f"alembic {text} is not supported; need >= {'.'.join(map(str, low))}, "
                f"< {'.'.join(map(str, high))}"
            )
        return text

    # -- commands

    def plan(self, flow: Flow, run: str, inventory: Path) -> PlanOutcome:
        ws = self._workspace(flow)
        config = self._config(ws, run, flow)
        out = ws.run_dir(run) / "plan.json"
        args = ["plan", "-f", str(inventory), "-o", str(out), *config]
        args += self._flags(flow)
        completed = self._run(flow, args)
        if not completed.ok:
            return PlanOutcome(completed, error=completed.describe())
        try:
            return PlanOutcome(completed, plan=Plan.from_bytes(out.read_bytes()))
        except (OSError, DocumentError) as e:
            return PlanOutcome(completed, error=f"plan output unreadable: {e}")

    def import_(self, flow: Flow, run: str, schema_inventory: Path) -> InventoryOutcome:
        """observe the flow's source into an inventory; `schema_inventory` selects the types."""
        if flow.source is None:
            raise RunnerError("this flow has no source backend to import from")
        ws = self._workspace(flow)
        out = ws.run_dir(run) / "imported.json"
        args = ["import", "-f", str(schema_inventory), "-o", str(out)]
        args += self._backend_args(ws, run, "source", flow.source)
        completed = self._run(flow, args, credentials=flow.source.env)
        return self._inventory_outcome(completed, out)

    def map(self, flow: Flow, run: str, inventory: Path, spec: Path) -> InventoryOutcome:
        """reshape `inventory` with the map `spec`. it reaches no backend: no credentials."""
        out = self._workspace(flow).prepare(run) / "mapped.json"
        args = ["map", "-f", str(inventory), "--spec", str(spec), "-o", str(out)]
        completed = self._run(flow, args, credentials={})
        return self._inventory_outcome(completed, out)

    @staticmethod
    def _inventory_outcome(completed: Completed, out: Path) -> InventoryOutcome:
        if not completed.ok:
            return InventoryOutcome(completed, error=completed.describe())
        if not out.is_file():
            return InventoryOutcome(completed, error=f"alembic wrote no inventory to {out.name}")
        return InventoryOutcome(completed, inventory=out)

    def check_stale(self, flow: Flow, run: str, inventory: Path, approved: Plan) -> StaleCheck:
        """re-plan without saving anything and compare with the approved plan."""
        ws = self._workspace(flow)
        config = self._config(ws, run, flow)
        args = ["plan", "-f", str(inventory), "--dry-run", *config]
        args += self._flags(flow)
        completed = self._run(flow, args, keep_stdout=True)
        if not completed.ok:
            return StaleCheck(completed, stale=False, error=completed.describe())
        try:
            current = Plan.from_bytes(completed.stdout.encode())
        except DocumentError as e:
            return StaleCheck(completed, stale=False, error=f"dry-run output unreadable: {e}")
        # the dry-run's json is the check's result, not something to keep as a log.
        completed = Completed(
            completed.argv, completed.exit_code, "", completed.stderr, completed.duration
        )
        return StaleCheck(completed, stale=not approved.same_work(current), current=current)

    def apply(self, flow: Flow, run: str, plan: Plan, expected_sha256: str) -> ApplyOutcome:
        if plan.sha256 != expected_sha256:
            raise RunnerError("the plan does not match the approved plan's hash")
        ws = self._workspace(flow)
        config = self._config(ws, run, flow)
        path = ws.write_file(run, "approved-plan.json", plan.raw)
        if sha256(path.read_bytes()) != expected_sha256:
            raise RunnerError("the plan written for apply does not match the approved hash")
        out = ws.run_dir(run) / "apply.json"
        args = ["apply", "-p", str(path), "-o", str(out), *config]
        if flow.allow_delete:
            args.append("--allow-delete")
        completed = self._run(flow, args)
        if not completed.ok:
            return ApplyOutcome(completed, error=completed.describe())
        try:
            return ApplyOutcome(completed, report=ApplyReport.from_bytes(out.read_bytes()))
        except (OSError, DocumentError) as e:
            return ApplyOutcome(completed, error=f"apply report unreadable: {e}")

    def drift(self, flow: Flow, run: str, inventory: Path) -> DriftOutcome:
        ws = self._workspace(flow)
        config = self._config(ws, run, flow)
        out = ws.run_dir(run) / "drift.json"
        args = ["plan", "-f", str(inventory), "--report", "-o", str(out)]
        args += config
        completed = self._run(flow, args)
        if not completed.ok:
            return DriftOutcome(completed, error=completed.describe())
        try:
            return DriftOutcome(completed, report=DriftReport.from_bytes(out.read_bytes()))
        except (OSError, DocumentError) as e:
            return DriftOutcome(completed, error=f"drift report unreadable: {e}")

    # -- plumbing

    def workspace(self, flow: Flow) -> Workspace:
        return self._workspace(flow)

    def _workspace(self, flow: Flow) -> Workspace:
        return Workspace(self.workspace_root, flow.id)

    def _config(self, ws: Workspace, run: str, flow: Flow) -> list[str]:
        return self._backend_args(ws, run, "target", flow.target)

    @staticmethod
    def _backend_args(ws: Workspace, run: str, name: str, backend: Backend) -> list[str]:
        """how alembic finds a backend: a plugin by name, anything else by a config
        file written for the run."""
        if backend.plugin:
            return ["--backend", backend.kind]
        return [
            "--backend-config",
            str(ws.write_backend_config(run, name, backend.config_document())),
        ]

    @staticmethod
    def _flags(flow: Flow) -> list[str]:
        flags = []
        if flow.allow_delete:
            flags.append("--allow-delete")
        if flow.no_adopt:
            flags.append("--no-adopt")
        return flags

    def _base_env(self, home: Path) -> dict[str, str]:
        env = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
        env["HOME"] = str(home)
        if self.plugins_dir:
            env["ALEMBIC_PLUGINS_DIR"] = str(self.plugins_dir)
        if self.rust_log:
            env["RUST_LOG"] = self.rust_log
        return env

    def env(self, flow: Flow, credentials: dict[str, str] | None = None) -> dict[str, str]:
        """a run's environment. `credentials` are those of the backend the command
        talks to, the target's unless given: no command sees another's secrets."""
        env = self._base_env(self._workspace(flow).flow_dir)
        env.update(self.state.env(flow.key))
        env.update(flow.target.env if credentials is None else credentials)
        return env

    def _run(
        self,
        flow: Flow,
        args: Sequence[str],
        keep_stdout: bool = False,
        credentials: dict[str, str] | None = None,
    ) -> Completed:
        cwd = self._workspace(flow).flow_dir
        cwd.mkdir(parents=True, exist_ok=True)
        argv = [self.alembic_path, *args]
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env=self.env(flow, credentials),
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            return Completed(
                argv,
                None,
                _truncate(e.stdout or b""),
                _truncate(e.stderr or b""),
                time.monotonic() - start,
                timed_out=True,
            )
        except OSError as e:
            raise RunnerError(f"cannot run {self.alembic_path}: {e}") from e
        stdout = proc.stdout.decode("utf-8", "replace") if keep_stdout else _truncate(proc.stdout)
        return Completed(
            argv, proc.returncode, stdout, _truncate(proc.stderr), time.monotonic() - start
        )
