"""plan, drift and apply documents as the cli writes them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

OP_KINDS = ("create", "update", "delete")


class DocumentError(ValueError):
    pass


def _load(raw: bytes, what: str) -> dict[str, Any]:
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise DocumentError(f"{what} is not json: {e}") from e
    if not isinstance(doc, dict):
        raise DocumentError(f"{what} is not a json object")
    return doc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Plan:
    raw: bytes
    doc: dict[str, Any]

    @classmethod
    def from_bytes(cls, raw: bytes) -> Plan:
        doc = _load(raw, "plan")
        if not isinstance(doc.get("ops"), list):
            raise DocumentError("plan has no ops list")
        return cls(raw=raw, doc=doc)

    @property
    def sha256(self) -> str:
        return sha256(self.raw)

    @property
    def ops(self) -> list[dict[str, Any]]:
        return self.doc["ops"]

    @property
    def schema_preview(self) -> dict[str, Any]:
        return self.doc.get("schema_preview") or {}

    def summary(self) -> dict[str, int]:
        """op counts, from the ops themselves rather than the optional `summary`."""
        counts = dict.fromkeys(OP_KINDS, 0)
        for op in self.ops:
            kind = op.get("op")
            if kind in counts:
                counts[kind] += 1
        return counts

    @property
    def has_deletes(self) -> bool:
        return any(op.get("op") == "delete" for op in self.ops)

    @property
    def is_empty(self) -> bool:
        """no ops and no pending schema work: applying it would write nothing."""
        return not self.ops and not any(self.schema_preview.values())

    def same_work(self, other: Plan) -> bool:
        """whether applying `other` would do exactly what applying this plan would."""
        return self.ops == other.ops and self.schema_preview == other.schema_preview


@dataclass(frozen=True)
class DriftReport:
    doc: dict[str, Any]

    CATEGORIES = ("changed", "missing", "extra")

    @classmethod
    def from_bytes(cls, raw: bytes) -> DriftReport:
        doc = _load(raw, "drift report")
        for category in cls.CATEGORIES:
            if not isinstance(doc.get(category), list):
                raise DocumentError(f"drift report has no {category} list")
        return cls(doc=doc)

    def counts(self) -> dict[str, int]:
        return {category: len(self.doc[category]) for category in self.CATEGORIES}

    @property
    def has_drift(self) -> bool:
        return any(self.counts().values())


@dataclass(frozen=True)
class ApplyReport:
    doc: dict[str, Any]

    @classmethod
    def from_bytes(cls, raw: bytes) -> ApplyReport:
        doc = _load(raw, "apply report")
        if not isinstance(doc.get("applied"), list):
            raise DocumentError("apply report has no applied list")
        return cls(doc=doc)

    @property
    def applied_count(self) -> int:
        return len(self.doc["applied"])

    @property
    def resumed_count(self) -> int:
        return int(self.doc.get("previously_applied_count") or 0)
