"""what a plan job and an apply job do, apart from where the host keeps the run.

the host snapshots the flow's files, locks and moves the run, and stores the
fields these functions return; everything alembic-shaped happens here."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .plan import Plan
from .runner import Completed, Flow, Runner
from .status import RunStatus

S = RunStatus

SNAPSHOT_LIMIT = 20 << 20

# what a run keeps when an import or a map produced its inventory.
DERIVED_ENTRY = "inventory.json"


class RunFailed(Exception):
    """a run that cannot go on, with the alembic output that led there."""

    def __init__(self, message: str, output: str = ""):
        super().__init__(message)
        self.output = output


def failed(outcome: Any, output: str) -> RunFailed:
    return RunFailed(f"{outcome.error}\n{outcome.completed.stderr}".strip(), output)


def output_text(*completed: Completed, before: str = "") -> str:
    """alembic's output, one block per command, after what earlier jobs recorded."""
    blocks = [before.rstrip()] if before else []
    for c in completed:
        mode = next((f" {flag}" for flag in ("--dry-run", "--report") if flag in c.argv), "")
        head = f"$ alembic {c.argv[1]}{mode}"
        if mode == " --dry-run":
            head += "  (stale check)"
        body = f"{c.stdout}{c.stderr}".rstrip()
        blocks.append(f"{head}\n{body}" if body else head)
    return "\n\n".join(blocks)


def input_sha256(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def snapshot(
    items: Iterable[tuple[str, bytes]],
    entry: str,
    where: str,
    logger: logging.Logger,
) -> dict[str, str]:
    """a flow's files, from `(path under the root, bytes)` pairs. binary files are
    skipped, the total is capped, and `entry` must be among them."""
    files, total = {}, 0
    for path, data in items:
        total += len(data)
        if total > SNAPSHOT_LIMIT:
            raise RunFailed(f"The files in {where} exceed {SNAPSHOT_LIMIT} bytes.")
        try:
            files[path] = data.decode("utf-8")
        except UnicodeDecodeError:
            logger.info(f"skipping {path}: not utf-8 text")
    if entry not in files:
        raise RunFailed(f"{entry} is not in {where}.")
    return files


def derive(
    runner: Runner,
    flow: Flow,
    run: str,
    files: dict[str, str],
    entry: str,
    map_spec: str,
    where: str,
    logger: logging.Logger,
) -> tuple[dict[str, str], str, list[Completed]]:
    """import from the flow's source and apply its map spec, when it has them.
    returns the files to plan from, the entry among them, and what ran."""
    inventory = runner.workspace(flow).write_input(run, files, entry)
    completed: list[Completed] = []
    current = inventory
    if flow.source is not None:
        logger.info("importing from the flow's source")
        imported = runner.import_(flow, run, inventory)
        completed.append(imported.completed)
        if imported.error:
            raise failed(imported, output_text(*completed))
        current = imported.inventory
    if map_spec:
        if map_spec not in files:
            raise RunFailed(f"map spec {map_spec} is not in {where}")
        spec = runner.workspace(flow).input_dir(run) / map_spec
        mapped = runner.map(flow, run, current, spec)
        completed.append(mapped.completed)
        if mapped.error:
            raise failed(mapped, output_text(*completed))
        current = mapped.inventory
    if current == inventory:
        return files, entry, completed
    # the plan, the stale check and the apply all work from the derived
    # inventory, so that is what the run keeps.
    return {DERIVED_ENTRY: Path(current).read_text(encoding="utf-8")}, DERIVED_ENTRY, completed


@dataclass(frozen=True)
class Outcome:
    """where a job leaves the run, and what the host stores on it."""

    status: RunStatus
    fields: dict[str, Any] = field(default_factory=dict)
    finished: bool = True


def plan_run(
    runner: Runner,
    flow: Flow,
    run: str,
    files: dict[str, str],
    entry: str,
    map_spec: str,
    where: str,
    drift: bool,
    logger: logging.Logger,
) -> Outcome:
    """plan (or, with `drift`, report drift) from the flow's snapshot."""
    files, entry, derived = derive(runner, flow, run, files, entry, map_spec, where, logger)
    inventory = runner.workspace(flow).write_input(run, files, entry)
    common = {"input": files, "input_entry": entry, "input_sha256": input_sha256(files)}

    if drift:
        outcome = runner.drift(flow, run, inventory)
        output = output_text(*derived, outcome.completed)
        if outcome.error:
            raise failed(outcome, output)
        report = outcome.report
        logger.info(f"drift: {report.counts()}")
        return Outcome(
            S.DRIFTED if report.has_drift else S.NO_CHANGES,
            {"drift_report": report.doc, "summary": report.counts(), "output": output, **common},
        )

    outcome = runner.plan(flow, run, inventory)
    output = output_text(*derived, outcome.completed)
    if outcome.error:
        raise failed(outcome, output)
    plan = outcome.plan
    logger.info(f"plan: {plan.summary()}")
    fields = {
        "plan": plan.raw.decode("utf-8"),
        "plan_sha256": plan.sha256,
        "summary": {**plan.summary(), "schema_preview": plan.schema_preview},
        "output": output,
        **common,
    }
    if plan.is_empty:
        return Outcome(S.NO_CHANGES, fields)
    return Outcome(S.AWAITING_APPROVAL, fields, finished=False)


def approved_plan(plan_text: str, approved_sha256: str) -> Plan:
    """the stored plan, refused unless it is the one that was approved."""
    plan = Plan.from_bytes(plan_text.encode("utf-8"))
    if not approved_sha256 or plan.sha256 != approved_sha256:
        raise RunFailed("The stored plan is not the one that was approved.")
    return plan


def apply_run(
    runner: Runner,
    flow: Flow,
    run: str,
    files: dict[str, str],
    entry: str,
    plan: Plan,
    approved_sha256: str,
    resuming: bool,
    before: str,
    logger: logging.Logger,
) -> Outcome:
    """check the approved plan against the target, then apply it."""
    inventory = runner.workspace(flow).write_input(run, files, entry)
    outputs: list[Completed] = []
    if not resuming:
        # a resumed apply re-runs its plan on top of what already landed, so a
        # re-plan would differ by construction. the journal carries it instead.
        check = runner.check_stale(flow, run, inventory, plan)
        outputs.append(check.completed)
        if check.error:
            raise failed(check, output_text(*outputs, before=before))
        logger.info(f"stale check: the target {'changed' if check.stale else 'is unchanged'}")
        if check.stale:
            return Outcome(
                S.STALE,
                {
                    "output": output_text(*outputs, before=before),
                    "error": "The target changed since this plan was made; plan again.",
                },
            )

    outcome = runner.apply(flow, run, plan, approved_sha256)
    outputs.append(outcome.completed)
    if outcome.error:
        raise failed(outcome, output_text(*outputs, before=before))
    logger.info(f"applied {outcome.report.applied_count} operations")
    return Outcome(
        S.APPLIED,
        {
            "apply_report": outcome.report.doc,
            "output": output_text(*outputs, before=before),
            "error": "",
        },
    )
