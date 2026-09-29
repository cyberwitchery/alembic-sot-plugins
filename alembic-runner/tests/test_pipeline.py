import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from alembic_runner import Backend, Flow, Runner, RunStatus
from alembic_runner.pipeline import (
    DERIVED_ENTRY,
    RunFailed,
    apply_run,
    approved_plan,
    output_text,
    plan_run,
    snapshot,
)
from alembic_runner.runner import Completed

FIXTURES = Path(__file__).parent / "fixtures"
LOG = logging.getLogger("test")
S = RunStatus

INVENTORY = """\
schema:
  types:
    dcim.site:
      key:
        slug: { type: slug }
      fields:
        name: { type: string }
        slug: { type: slug }
objects:
  - uid: "11111111-1111-1111-1111-111111111111"
    type: dcim.site
    key: { slug: fra1 }
    attrs: { name: FRA1, slug: fra1 }
"""

MAP = """\
schema:
  types:
    dcim.site:
      key:
        slug: { type: slug }
      fields:
        name: { type: string }
        slug: { type: slug }
rules:
  - name: sites
    match: "dcim.site"
    emit:
      type: dcim.site
      key: { slug: "${key.slug}" }
      attrs: { name: "${attrs.slug|upper}", slug: "${key.slug}" }
"""


class SnapshotTests(unittest.TestCase):
    def test_keeps_text_and_skips_binary(self):
        files = snapshot([("inv.yaml", b"a"), ("logo.png", b"\xff\xfe")], "inv.yaml", "x", LOG)
        self.assertEqual(files, {"inv.yaml": "a"})

    def test_needs_the_entry(self):
        with self.assertRaises(RunFailed) as ctx:
            snapshot([("other.yaml", b"a")], "inv.yaml", "repo x", LOG)
        self.assertIn("inv.yaml", str(ctx.exception))

    def test_caps_the_total(self):
        big = b"a" * ((20 << 20) + 1)
        with self.assertRaises(RunFailed):
            snapshot([("inv.yaml", big)], "inv.yaml", "x", LOG)

    def test_output_names_the_stale_check(self):
        c = Completed(["alembic", "plan", "-f", "x", "--dry-run"], 0, "", "", 0.1)
        self.assertEqual(
            output_text(c, before="earlier"), "earlier\n\n$ alembic plan --dry-run  (stale check)"
        )

    def test_approved_plan_refuses_other_bytes(self):
        with self.assertRaises(RunFailed):
            approved_plan('{"ops": []}', "0" * 64)


ALEMBIC = shutil.which(os.environ.get("ALEMBIC", "alembic"))


@unittest.skipUnless(ALEMBIC, "needs the alembic binary on PATH or in $ALEMBIC")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.store = self.tmp / "store.json"
        self.runner = Runner(self.tmp / "work", alembic_path=ALEMBIC, timeout=60)
        self.runner.check_version()
        self.flow = Flow("1", self.store_backend(self.store))
        self.files = {"inv.yaml": INVENTORY}

    @staticmethod
    def store_backend(path):
        return Backend(
            "external",
            {"args": [str(FIXTURES / "store_adapter.py")], "setup": {"path": str(path)}},
            command=sys.executable,
        )

    def plan(self, run, drift=False, flow=None, files=None, entry="inv.yaml", map_spec=""):
        return plan_run(
            self.runner,
            flow or self.flow,
            run,
            files or self.files,
            entry,
            map_spec,
            "x",
            drift,
            LOG,
        )

    def apply(self, run, outcome, resuming=False):
        plan = approved_plan(outcome.fields["plan"], outcome.fields["plan_sha256"])
        return apply_run(
            self.runner,
            self.flow,
            run,
            outcome.fields["input"],
            outcome.fields["input_entry"],
            plan,
            outcome.fields["plan_sha256"],
            resuming,
            outcome.fields["output"],
            LOG,
        )

    def test_plan_then_apply(self):
        planned = self.plan("1")
        self.assertEqual(planned.status, S.AWAITING_APPROVAL)
        self.assertFalse(planned.finished)
        applied = self.apply("1", planned)
        self.assertEqual(applied.status, S.APPLIED)
        self.assertIn("$ alembic apply", applied.fields["output"])
        self.assertEqual(self.plan("2").status, S.NO_CHANGES)
        self.assertEqual(self.plan("3", drift=True).status, S.NO_CHANGES)

    def test_a_changed_target_is_stale(self):
        planned = self.plan("1")
        self.store.write_text(
            json.dumps(
                {
                    "next_id": 2,
                    "objects": {
                        "1": {
                            "type": "dcim.site",
                            "key": {"slug": "fra1"},
                            "attrs": {"name": "hand", "slug": "fra1"},
                        }
                    },
                }
            )
        )
        outcome = self.apply("1", planned)
        self.assertEqual(outcome.status, S.STALE)
        self.assertIn("plan again", outcome.fields["error"])

    def test_a_source_is_imported_and_kept(self):
        self.apply("1", self.plan("1"))
        mirror = Flow("2", self.store_backend(self.tmp / "mirror.json"), source=self.flow.target)
        planned = self.plan("1", flow=mirror)
        self.assertEqual(planned.status, S.AWAITING_APPROVAL)
        self.assertEqual(planned.fields["input_entry"], DERIVED_ENTRY)
        self.assertEqual(list(planned.fields["input"]), [DERIVED_ENTRY])

    def test_a_map_spec_is_relative_to_the_root(self):
        files = {"schemas/inv.yaml": INVENTORY, "maps/upper.yaml": MAP}
        planned = self.plan("1", files=files, entry="schemas/inv.yaml", map_spec="maps/upper.yaml")
        self.assertEqual(planned.status, S.AWAITING_APPROVAL, planned.fields.get("error"))
        self.assertIn('"name": "FRA1"', planned.fields["input"][DERIVED_ENTRY])


if __name__ == "__main__":
    unittest.main()
