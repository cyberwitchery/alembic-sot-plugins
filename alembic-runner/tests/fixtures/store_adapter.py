"""an external adapter over a json file, for driving the real cli in tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alembic_adapter import (
    Adapter,
    AppliedOp,
    ApplyReport,
    Create,
    Delete,
    ExternalObject,
    Op,
    ProvisionReport,
    Schema,
    State,
    Update,
    run,
)


class StoreAdapter(Adapter):
    def __init__(self) -> None:
        self.path = Path("store.json")

    def setup(self, config: Any) -> None:
        self.path = Path(config["path"])

    def _load(self) -> dict:
        if not self.path.exists():
            return {"next_id": 1, "objects": {}}
        return json.loads(self.path.read_text())

    def read(self, schema: Schema, types: list[str], state: State) -> list[ExternalObject]:
        store = self._load()
        return [
            ExternalObject(type_name=r["type"], key=r["key"], attrs=r["attrs"], backend_id=int(i))
            for i, r in sorted(store["objects"].items(), key=lambda kv: int(kv[0]))
            if r["type"] in types
        ]

    def write(self, schema: Schema, ops: list[Op], state: State) -> ApplyReport:
        store = self._load()
        report = ApplyReport()
        for op in ops:
            if isinstance(op, Create):
                backend_id = store["next_id"]
                store["next_id"] += 1
                store["objects"][str(backend_id)] = {
                    "type": op.type_name,
                    "key": op.desired.key,
                    "attrs": op.desired.attrs,
                }
            elif isinstance(op, Update):
                backend_id = op.backend_id
                store["objects"][str(backend_id)]["attrs"].update(op.desired.attrs)
            elif isinstance(op, Delete):
                backend_id = op.backend_id
                store["objects"].pop(str(backend_id))
            report.applied.append(
                AppliedOp(uid=op.uid, type_name=op.type_name, backend_id=backend_id)
            )
        self.path.write_text(json.dumps(store))
        return report

    def ensure_schema(self, schema: Schema) -> ProvisionReport:
        return ProvisionReport()

    def preview_schema(self, schema: Schema) -> ProvisionReport:
        return ProvisionReport()


if __name__ == "__main__":
    run(StoreAdapter())
