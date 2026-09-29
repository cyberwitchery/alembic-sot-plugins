"""what a run's review page shows: ops grouped for reading, drift entries, and
the names behind the uids they reference. host-independent."""

from __future__ import annotations

import json
from collections import defaultdict

from .status import RunStatus


def _load(text):
    """an inventory's text, yaml or json. yaml when the host has it, which netbox
    and nautobot both do."""
    try:
        import yaml
    except ImportError:  # pragma: no cover
        return json.loads(text)
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(str(e)) from e


BUSY = frozenset(
    s.value for s in (RunStatus.PENDING, RunStatus.PLANNING, RunStatus.APPROVED, RunStatus.APPLYING)
)


# runs whose page offers a fresh plan of the flow.
PLAN_AGAIN = frozenset(
    s.value for s in (RunStatus.STALE, RunStatus.FAILED, RunStatus.EXPIRED, RunStatus.DRIFTED)
)


def _compact(value):
    return json.dumps(value, sort_keys=True, separators=(", ", ": ")) if value is not None else "-"


def _name(key):
    values = [v for v in (key or {}).values() if isinstance(v, str | int)]
    return "/".join(str(v) for v in values) if values else None


def _names(ops):
    """uid -> a readable name, for every object a plan creates or updates."""
    names = {}
    for op in ops:
        name = _name(op.get("key") or (op.get("desired") or {}).get("key"))
        if op.get("uid") and name:
            names[op["uid"]] = name
    return names


def input_names(text):
    """uid -> a readable name, for every object an inventory declares. this names
    what a plan only references, such as an adopted status object."""
    if not text:
        return {}
    try:
        doc = _load(text)
    except ValueError:
        return {}
    names = {}
    for obj in (doc or {}).get("objects") or ():
        name = _name(obj.get("key")) if isinstance(obj, dict) else None
        if name and obj.get("uid"):
            names[str(obj["uid"])] = name
    return names


def _readable(value, names):
    """a value with the uids the plan defines replaced by their objects' names."""
    if isinstance(value, str):
        return names.get(value, value)
    if isinstance(value, list):
        return [_readable(v, names) for v in value]
    if isinstance(value, dict):
        return {k: _readable(v, names) for k, v in value.items()}
    return value


def _changes(entry, names=None):
    names = names or {}
    return [
        {
            "field": c.get("field"),
            "from": _compact(_readable(c.get("from"), names)),
            "to": _compact(_readable(c.get("to"), names)),
        }
        for c in entry.get("changes") or ()
    ]


def drift_rows(report, names=None):
    """drift entries in category order, for review."""
    rows = []
    for category in ("changed", "missing", "extra"):
        for entry in (report or {}).get(category, ()):
            rows.append(
                {
                    "category": category,
                    "type_name": entry.get("type_name"),
                    "key": _compact(entry.get("key")),
                    "changes": _changes(entry, names),
                }
            )
    return rows


def group_ops(plan_text, known=None):
    """plan ops grouped for review: deletes, updates and creates, each by type."""
    if not plan_text:
        return []
    ops = json.loads(plan_text).get("ops", [])
    names = {**(known or {}), **_names(ops)}
    grouped = defaultdict(lambda: defaultdict(list))
    for op in ops:
        key = _readable(op.get("key") or (op.get("desired") or {}).get("key"), names)
        entry = {"key": _compact(key), "changes": _changes(op, names), "uid": op.get("uid")}
        grouped[op.get("op")][op.get("type_name")].append(entry)
    return [
        (kind, sorted(grouped[kind].items()))
        for kind in ("delete", "update", "create")
        if grouped.get(kind)
    ]
